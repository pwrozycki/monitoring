import time
from typing import Iterable

import numpy as np
from injector import Injector

from events_processor.bindings import AppBindingsModule
from events_processor.configtools import ConfigProvider
from events_processor.controller import MainController
from events_processor.interfaces import Detector, NotificationSender, ZoneReader, AlarmBoxReader, ResourceReader
from events_processor.models import Detection, Rect, ZoneInfo
from tests.bindings import TestBindingsModule


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


def run_pipeline(detections=None,
                 score=0.8,
                 events=(ResourceTemplate.event_template(),),
                 frames=(ResourceTemplate.frame_template(frames=1),),
                 wait_time=0.2,
                 config_updates=None,
                 alarm_box=None,
                 zones: Iterable[ZoneInfo] = ()):
    detections = detections if detections else {
        '0': [TestDetection(score=score, bounding_box=np.array([1, 1, 50, 50]))]}

    injector = Injector([AppBindingsModule, TestBindingsModule])

    zone_reader = injector.get(ZoneReader)
    zone_reader.zones = zones

    config = injector.get(ConfigProvider)
    if config_updates:
        for (key, value) in config_updates.items():
            config[key].update(config_updates[key])
        config.reread()

    alarm_box_reader = injector.get(AlarmBoxReader)
    alarm_box_reader.box = alarm_box

    resource_reader = injector.get(ResourceReader)
    resource_reader.events = events
    resource_reader.frames = frames

    detector = injector.get(Detector)
    detector.detections = {'1': detections} if detections else {}

    controller = injector.get(MainController)

    controller.start(watchdog=False)
    time.sleep(wait_time)
    controller.stop()
    time.sleep(0.2)

    sender = injector.get(NotificationSender)
    return list(sender.notifications.keys())
