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
        events_fetch_from = datetime.now() - timedelta(seconds=self._config.EVENTS_WINDOW_SECONDS)

        query = self._config.EVENT_LIST_URL.format(startTime=datetime.strftime(events_fetch_from, '%Y-%m-%d %H:%M:%S'),
                                                   page=page)
        query = query.replace(' ', '%20')

        response = self._resource_reader.read(query)
        if response:
            return json.loads(response.content)
        return {}

    def get_event_details_json(self, event_id: str) -> Optional[Tuple[Dict, Dict]]:
        query = self._config.EVENT_DETAILS_URL.format(eventId=event_id)
        response = self._resource_reader.read(query)
        if response:
            data = json.loads(response.content)['event']
            return data['Event'], data['Frame']
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
            (event_json, frames_json) = details
            yield event_json

    def frames_iter(self, event_ids: Iterable[str]) -> Iterable[FrameInfo]:
        for event_id in event_ids:
            details = self.get_event_details_json(event_id)
            if not details:
                continue
            (event_json, frames_json) = details

            for frame_json in frames_json:
                frame_id = frame_json['FrameId']

                file_name = self._get_frame_file_name(event_id, event_json, frame_id)
                yield FrameInfo(frame_json, file_name)

    def _get_frame_file_name(self, event_id: str, event_json: Dict, frame_id: str) -> str:
        file_name = self._config.FRAME_FILE_NAME.format(
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
        self._events_cache = TTLCache(maxsize=10000000, ttl=config.EVENTS_WINDOW_SECONDS + config.CACHE_SECONDS_BUFFER)
        self._frames_cache = TTLCache(maxsize=10000000, ttl=config.EVENTS_WINDOW_SECONDS + config.CACHE_SECONDS_BUFFER)

        self._frame_reader = frame_reader
        if config.EVENT_IDS:
            self._events_iter = lambda: self._frame_reader.events_by_id_iter(config.EVENT_IDS)
            self._skip_mailed = False
        else:
            self._events_iter = self._frame_reader.events_iter
            self._skip_mailed = True

    def run(self) -> None:
        while not self._stop_requested:
            before = time.monotonic()
            self._collect_events()
            time_spent = (time.monotonic() - before)
            self._system_time.sleep(max(self._config.EVENT_LOOP_SECONDS - time_spent, 0))

        self.log.info("Terminating")

    def stop(self) -> None:
        self._stop_requested = True

    def _collect_events(self) -> None:
        self.log.info("Fetching event list")
        for event_json in self._events_iter():
            event_id = event_json['Id']
            event_info = self._events_cache.setdefault(event_id, EventInfo())
            event_info.event_json = event_json

            if event_info.all_frames_were_read or event_info.notification_sent:
                continue

            mailed = event_json['Emailed'] == '1'
            if mailed and self._skip_mailed:
                self.log.debug(f'Skipping processing of event {event_info} as it was already mailed')
                continue

            self.log.info(f"Reading event {event_info}")

            frame_skipped = False
            for frame_info in self._frame_reader.frames_iter(event_ids=(event_id,)):
                if frame_info.frame_json['Type'] != 'Alarm':
                    continue

                frame_time = datetime.strptime(frame_info.frame_json['TimeStamp'], '%Y-%m-%d %H:%M:%S')
                if datetime.now() - frame_time < timedelta(seconds=self._config.FRAME_READ_DELAY_SECONDS):
                    frame_skipped = True
                    continue

                key = '{EventId}_{FrameId}'.format(**frame_info.frame_json)
                if key in self._frames_cache:
                    continue
                self._frames_cache[key] = 1

                frame_info.event_info = event_info
                self._frame_queue.put(frame_info)

            if not frame_skipped and event_info.event_json['EndTime'] is not None:
                event_info.all_frames_were_read = True
