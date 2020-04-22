import json
import logging
import time
from datetime import datetime, timedelta
from queue import Queue
from threading import Thread
from typing import Dict, Optional, Tuple, Callable, Iterable

import requests
from cachetools import TTLCache
from requests import Response

from events_processor import config
from events_processor.models import FrameInfo, EventInfo

EVENTS_WINDOW_SECONDS = config['timings'].getint('events_window_seconds')
CACHE_SECONDS_BUFFER = config['timings'].getint('cache_seconds_buffer')


class FrameReader:
    EVENT_LIST_URL = config['zm']['event_list_url']
    EVENT_DETAILS_URL = config['zm']['event_details_url']
    FRAME_FILE_NAME = config['zm']['frame_jpg_path']

    log = logging.getLogger("events_processor.FrameReader")

    def __init__(self, get_resource: Callable[[str], Optional[Response]] = None):
        self._get_resource = get_resource if get_resource else self._get_resource_by_request

    def _get_past_events_json(self, page: int) -> Dict:
        events_fetch_from = datetime.now() - timedelta(seconds=EVENTS_WINDOW_SECONDS)

        query = self.EVENT_LIST_URL.format(startTime=datetime.strftime(events_fetch_from, '%Y-%m-%d %H:%M:%S'),
                                           page=page)
        query = query.replace(' ', '%20')

        response = self._get_resource(query)
        if response:
            return json.loads(response.content)
        return {}

    def get_event_details_json(self, event_id: str) -> Optional[Tuple[Dict, Dict]]:
        query = self.EVENT_DETAILS_URL.format(eventId=event_id)
        response = self._get_resource(query)
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
        file_name = self.FRAME_FILE_NAME.format(
            monitorId=event_json['MonitorId'],
            startDay=event_json['StartTime'][:10],
            eventId=event_id,
            frameId=frame_id
        )
        return file_name

    def _get_resource_by_request(self, url: str) -> Optional[Response]:
        try:
            response = requests.get(url)
            if response.status_code == 200:
                return response
        except requests.exceptions.RequestException:
            pass
        self.log.error(f"Could not retrieve resource: {url}")
        return None


class FrameReaderWorker(Thread):
    EVENT_LOOP_SECONDS = config['timings'].getint('event_loop_seconds')
    FRAME_READ_DELAY_SECONDS = config['timings'].getint('frame_read_delay_seconds')

    log = logging.getLogger("events_processor.FrameReaderWorker")

    def __init__(self, frame_queue: 'Queue[FrameInfo]',
                 event_ids: Optional[Iterable[str]] = None,
                 skip_mailed: bool = False,
                 frame_reader: FrameReader = None,
                 sleep: Callable[[float], None] = time.sleep):
        super().__init__()
        self._stop_requested = False
        self._sleep = sleep

        self._frame_queue = frame_queue
        self._events_cache = TTLCache(maxsize=10000000, ttl=EVENTS_WINDOW_SECONDS + CACHE_SECONDS_BUFFER)
        self._frames_cache = TTLCache(maxsize=10000000, ttl=EVENTS_WINDOW_SECONDS + CACHE_SECONDS_BUFFER)

        self._frame_reader = frame_reader if frame_reader else FrameReader()
        if event_ids:
            self._events_iter = lambda: self._frame_reader.events_by_id_iter(event_ids)
        else:
            self._events_iter = self._frame_reader.events_iter
        self._skip_mailed = skip_mailed

    def run(self) -> None:
        while not self._stop_requested:
            before = time.monotonic()
            self._collect_events()
            time_spent = (time.monotonic() - before)
            self._sleep(max(self.EVENT_LOOP_SECONDS - time_spent, 0))

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
                if datetime.now() - frame_time < timedelta(seconds=self.FRAME_READ_DELAY_SECONDS):
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
