import json
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import numpy as np
from injector import inject

from events_processor.configtools import ConfigProvider
from events_processor.interfaces import Detector, ResourceReader, NotificationSender, ImageReader, SystemTime, \
    ZoneReader, AlarmBoxReader, Engine, MonitorReader
from events_processor.models import ZoneInfo, Rect, MonitorInfo, FrameInfo, EventInfo
from events_processor.preprocessor import RotatingPreprocessor
from events_processor.shapeutils import bounding_box


class TestDetector(Detector):
    @inject
    def __init__(self,
                 alarm_box_reader: AlarmBoxReader,
                 config: ConfigProvider,
                 preprocessor: RotatingPreprocessor):
        self.detections = {}
        self._alarm_box_reader = alarm_box_reader
        self._config = config
        self._preprocessor = preprocessor

    def detect(self, frame_info: FrameInfo) -> None:
        event_id = frame_info.event_id
        frame_id = frame_info.frame_id

        box = self._alarm_box_reader.read(event_id, frame_id, self._config.excluded_zone_prefix)
        if box:
            frame_info.alarm_box = bounding_box(self._preprocessor.transform_frame_points(frame_info, box.points))
        frame_info.detections = self.detections.get(event_id, {}).get(frame_id, [])


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


class TestSender(NotificationSender):
    def __init__(self):
        self.notifications = []

    def send(self, frame_info: FrameInfo, subject: str, message: str) -> bool:
        event_info = frame_info.event_info
        self.notifications.append(NotificationData(event_info, frame_info, subject, message))
        print(f"Sending notification with score {frame_info.score}")
        return True


class TestImageReader(ImageReader):
    def __init__(self):
        pass

    def read(self, file_name: str) -> Any:
        img = np.zeros((1000, 1000, 3), np.uint8)
        img[::] = (255, 255, 255)
        return img


class TestTime(SystemTime):
    def __init__(self):
        pass

    def sleep(self, t):
        time.sleep(t / 50)


class TestZoneReader(ZoneReader):
    def __init__(self):
        self.zones: Iterable[ZoneInfo] = ()

    def read(self, excl_zone_prefix) -> Iterable[ZoneInfo]:
        return self.zones


class TestAlarmBoxReader(AlarmBoxReader):
    def __init__(self):
        self.box: Optional[Rect] = None

    def read(self, event_id: str, frame_id: str, excl_zone_prefix) -> Optional[Rect]:
        return self.box


class TestMonitorReader(MonitorReader):
    def read(self) -> Iterable[MonitorInfo]:
        return (MonitorInfo('1', 'SomeMonitor'),)


class TestNoOpEngine(Engine):

    def detect(selfself, img, threshold):
        pass

    def get_pending_processing_seconds(self) -> float:
        return 0


class Response:
    pass


@dataclass
class NotificationData:
    event_info: EventInfo
    frame_info: FrameInfo
    subject: str
    message: str
