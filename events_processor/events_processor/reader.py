import json
import logging
import time
from datetime import datetime, timedelta
from threading import Thread
from typing import Dict, Optional, Tuple, Iterable

import requests
from cachetools import TTLCache
from injector import inject
from requests import Response

from events_processor.configtools import ConfigProvider
from events_processor.interfaces import SystemTime, ResourceReader
from events_processor.models import FrameInfo, EventInfo, FrameQueue


class WebResourceReader(ResourceReader):
    log = logging.getLogger("events_processor.WebResourceReader")

    def read(self, url: str) -> Optional[Response]:
        try:
            response = requests.get(url)
            if response.status_code == 200:
                return response
        except requests.exceptions.RequestException:
            pass
        self.log.error(f"Could not retrieve resource: {url}")
        return None


class FrameReader:
    log = logging.getLogger("events_processor.FrameReader")

    @inject
    def __init__(self,
                 resource_reader: ResourceReader,
                 config: ConfigProvider):
        self._config = config
        self._resource_reader = resource_reader

    def _get_past_events_json(self, page: int) -> Dict:
        events_fetch_from = datetime.now() - timedelta(seconds=self._config.events_window_seconds)

        query = self._config.event_list_url.format(startTime=datetime.strftime(events_fetch_from, '%Y-%m-%d %H:%M:%S'),
                                                   page=page)
        query = query.replace(' ', '%20')

        response = self._resource_reader.read(query)
        if response:
            return json.loads(response.content)
        return {}

    def get_event_details_json(self, event_id: str) -> Optional[Tuple[Dict, Dict, Dict]]:
        query = self._config.event_details_url.format(eventId=event_id)
        response = self._resource_reader.read(query)
        if response:
            data = json.loads(response.content)['event']
            return data['Event'], data['Frame'], data['Monitor']
        return None

    def events_iter(self) -> Iterable[Dict]:
        page = 0
        page_count = 1

        while page < page_count:
            event_json = self._get_past_events_json(page=page + 1)
            if not event_json:
                break

            page_count = int(event_json['pagination']['pageCount'])
            page = int(event_json['pagination']['page'])
            yield from (e['Event'] for e in event_json['events'])

    def events_by_id_iter(self, event_ids: Iterable[str]) -> Iterable[Dict]:
        for event_id in event_ids:
            details = self.get_event_details_json(event_id)
            if not details:
                continue
            (event_json, _, _) = details
            yield event_json

    def frames(self, event_info: EventInfo) -> Iterable[FrameInfo]:
        details = self.get_event_details_json(event_info.event_id)
        if not details:
            return ()

        (event_json, frames_json, monitor_json) = details

        frames = []
        for frame_json in frames_json:
            frame_id = frame_json['FrameId']
            file_name = self._get_frame_jpg_path(event_info.event_id, event_json, frame_id)
            frames.append(FrameInfo(frame_json, monitor_json, file_name, event_info))

        return frames

    def _get_frame_jpg_path(self, event_id: str, event_json: Dict, frame_id: str) -> str:
        file_name = self._config.frame_jpg_path.format(
            monitorId=event_json['MonitorId'],
            startDay=event_json['StartTime'][:10],
            eventId=event_id,
            frameId=frame_id
        )
        return file_name


class FrameReaderWorker(Thread):
    log = logging.getLogger("events_processor.FrameReaderWorker")

    @inject
    def __init__(self,
                 config: ConfigProvider,
                 frame_queue: FrameQueue,
                 system_time: SystemTime,
                 frame_reader: FrameReader):
        super().__init__()
        self._config = config
        self._stop_requested = False
        self._system_time = system_time

        self._frame_queue = frame_queue
        event_ttl = config.events_window_seconds + config.cache_seconds_buffer
        self._recent_events = TTLCache(maxsize=10000000, ttl=event_ttl)

        self._frame_reader = frame_reader
        if config.event_ids:
            self._events_iter = lambda: self._frame_reader.events_by_id_iter(config.event_ids)
            self._skip_mailed = False
        else:
            self._events_iter = self._frame_reader.events_iter
            self._skip_mailed = True

    def run(self) -> None:
        while not self._stop_requested:
            before = time.monotonic()
            self._collect_events()
            time_spent = (time.monotonic() - before)
            self._system_time.sleep(max(self._config.event_loop_seconds - time_spent, 0))

        self.log.info("Terminating")

    def stop(self) -> None:
        self._stop_requested = True

    def _collect_events(self) -> None:
        self.log.info("Fetching event list")

        for event_json in self._events_iter():
            event_id = event_json['Id']
            event_info = self._recent_events.setdefault(event_id, EventInfo())
            event_info.event_json = event_json

            if event_info.all_frames_were_read or event_info.notification_status.was_sending:
                continue

            if event_info.emailed and self._skip_mailed:
                self.log.debug(f'Skipping processing of event {event_info} as it was already mailed')
                continue

            self._collect_frames(event_info)

    def _collect_frames(self, event_info: EventInfo):
        self.log.info(f"Reading event frames: {event_info}")

        frames = self._frame_reader.frames(event_info)
        if frames:
            pending_frames = False
            for frame_info in frames:
                if frame_info.type != 'Alarm':
                    continue

                if frame_info.frame_id in event_info.retrieved_frame_ids:
                    continue

                if self._frame_data_has_settled(frame_info):
                    self._frame_queue.put(frame_info)
                    event_info.retrieved_frame_ids.add(frame_info.frame_id)
                else:
                    pending_frames = True

            if not pending_frames and event_info.end_time is not None:
                event_info.all_frames_were_read = True

    def _frame_data_has_settled(self, frame_info):
        frame_timestamp = datetime.strptime(frame_info.timestamp, '%Y-%m-%d %H:%M:%S')
        return datetime.now() >= frame_timestamp + timedelta(seconds=self._config.frame_read_delay_seconds)
