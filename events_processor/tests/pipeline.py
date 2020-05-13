import time

import numpy as np
from injector import inject

from events_processor.configtools import ConfigProvider
from events_processor.controller import MainController
from events_processor.interfaces import Detector, NotificationSender, ZoneReader, AlarmBoxReader, ResourceReader
from events_processor.models import Detection, Rect


class TestDetection(Detection):
    def __init__(self, label_id=0, score=0.5, bounding_box=np.array([1, 1, 50, 50])):
        super().__init__(Rect(*bounding_box.flatten().tolist()), score, label_id)


class ResourceTemplate:
    @staticmethod
    def event_template(event_id=1, end_time='2019-08-09', monitor_id='1'):
        return {
            'events': [{
                'Event': {
                    'Id': str(event_id),
                    'EndTime': end_time,
                    'MonitorId': monitor_id,
                    'Emailed': '0',
                    'StartTime': '2019-08-08',
                    'Length': '10',
                    'Frames': '100',
                    'AlarmFrames': '50',
                    'TotScore': '702',
                    'AvgScore': '7',
                    'MaxScore': '17',
                    'Width': '1000',
                    'Height': '1000'
                }
            }],
            'pagination': {
                'page': '1',
                'pageCount': '1'
            }
        }

    @classmethod
    def frame_template(cls, event_id=1, frames=0, end_time=None):
        return {
            'event': {
                'Event': cls.event_template(event_id, end_time)['events'][0]['Event'],
                'Frame': [
                    {
                        'Id': 'x',
                        'FrameId': str(x),
                        'Type': 'Alarm',
                        'TimeStamp': '2019-08-08 10:00:00',
                        'EventId': str(event_id),
                    } for x in range(frames)
                ],
                'Monitor': {
                    'Id': '1',
                    'Name': 'SomeMonitor',
                }
            }
        }


class Pipeline:
    @inject
    def __init__(self,
                 zone_reader: ZoneReader,
                 alarm_box_reader: AlarmBoxReader,
                 resource_reader: ResourceReader,
                 detector: Detector,
                 controller: MainController,
                 config: ConfigProvider,
                 sender: NotificationSender):
        self._zone_reader = zone_reader
        self._alarm_box_reader = alarm_box_reader
        self._resource_reader = resource_reader
        self._detector = detector
        self._controller = controller
        self._config = config
        self._sender = sender

    def run_with(self,
                 detections=None,
                 score=0.8,
                 events=None,
                 frames=None,
                 wait_time=0.2,
                 config_updates=None,
                 alarm_box=None,
                 zones=()):
        detections = detections or {
            '0': [TestDetection(score=score, bounding_box=np.array([1, 1, 50, 50]))]}
        events = events or (ResourceTemplate.event_template(),)
        frames = frames or (ResourceTemplate.frame_template(frames=1),)

        self._zone_reader.zones = zones
        self._alarm_box_reader.box = alarm_box
        self._resource_reader.events = events
        self._resource_reader.frames = frames
        self._detector.detections = {'1': detections}

        if config_updates:
            for (key, value) in config_updates.items():
                self._config[key].update(config_updates[key])

        if config_updates or zones:
            self._config.reread()

        self._controller.start(watchdog=False)
        time.sleep(wait_time)
        self._controller.stop()
        time.sleep(0.2)

        return list(self._sender.notifications)
