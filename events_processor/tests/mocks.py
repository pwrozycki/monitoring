import copy
import json
import time
from typing import Any, Iterable, Optional

import numpy as np
from injector import singleton

from events_processor.interfaces import Detector, ResourceReader, NotificationSender, ImageReader, SystemTime, \
    ZoneReader, AlarmBoxReader
from events_processor.models import EventInfo, ZoneInfo, Rect


@singleton
class TestDetector(Detector):
    def __init__(self):
        self.detections: {}

    def detect(self, frame_info) -> None:
        event_id = frame_info.frame_json['EventId']
        frame_id = frame_info.frame_json['FrameId']
        frame_info.detections = self.detections.get(event_id, {}).get(frame_id, [])


@singleton
class TestResourceReader(ResourceReader):
    def __init__(self):
        self._event_list_invocation = None
        self.events = []
        self.frames = []

    def read(self, url):
        response = Response()
        response.status_code = 200

        content = None
        if url.find('/api/events/index') != -1:
            if self._event_list_invocation is None:
                self._event_list_invocation = 0
            else:
                self._event_list_invocation = min(len(self.events) - 1, self._event_list_invocation + 1)
            content = self.events[self._event_list_invocation]
        elif url.find('/api/events') != -1:
            content = self.frames[self._event_list_invocation]

        response.content = json.dumps(content)
        return response


@singleton
class TestSender(NotificationSender):
    def __init__(self):
        self.notifications = {}

    def send(self, event_info: EventInfo, subject: str, message: str):
        self.notifications[copy.copy(event_info)] = (subject, message)
        print(f"Sending notification with score {event_info.frame_score}")
        return True


@singleton
class TestImageReader(ImageReader):
    def __init__(self):
        pass

    def read(self, file_name: str) -> Any:
        img = np.zeros((1000, 1000, 3), np.uint8)
        img[::] = (255, 255, 255)
        return img


@singleton
class TestTime(SystemTime):
    def __init__(self):
        pass

    def sleep(self, t):
        time.sleep(t / 50)


@singleton
class TestZoneReader(ZoneReader):
    def __init__(self):
        self.zones: Iterable[ZoneInfo] = ()

    def read(self) -> Iterable[ZoneInfo]:
        return self.zones


@singleton
class TestAlarmBoxReader(AlarmBoxReader):
    def __init__(self):
        self.box: Optional[Rect] = None

    def read(self, event_id: str, frame_id: str) -> Optional[Rect]:
        return self.box


class Response:
    pass