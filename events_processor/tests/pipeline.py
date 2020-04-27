import time
from typing import Iterable

import numpy as np
from injector import Injector

import events_processor
from events_processor.bindings import ProcessorModule
from events_processor.controller import MainController
from events_processor.interfaces import Detector, NotificationSender, ZoneReader, AlarmBoxReader, ResourceReader
from events_processor.models import Detection, Rect, ZoneInfo
from tests.bindings import TestOverridesModule


class TestDetection(Detection):
    def __init__(self, label_id=0, score=0.5, bounding_box=np.array([1, 1, 50, 50])):
        super().__init__(Rect(*bounding_box.flatten().tolist()), score, label_id)


class ResourceTemplate:
    @staticmethod
    def event_template(event_id=1, end_time='2019-08-09', monitor_id='1'):
        return {
            'events': [{
                'Event': {
                    'Id': event_id,
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
                'page': 1,
                'pageCount': 1
            }
        }

    @classmethod
    def frame_template(cls, event_id=1, frames=0, end_time=None):
        return {
            'event': {
                'Event': cls.event_template(event_id, end_time)['events'][0]['Event'],
                'Frame': [
                    {
                        'Id': x,
                        'FrameId': x,
                        'Type': 'Alarm',
                        'TimeStamp': '2019-08-08 10:00:00',
                        'EventId': event_id,
                    } for x in range(frames)
                ]
            }
        }


def reset_config():
    for section in events_processor.config.sections():
        events_processor.config.remove_section(section)
    events_processor.read_config()


def run_pipeline(detections=None,
                 score=0.8,
                 events=(ResourceTemplate.event_template(),),
                 frames=(ResourceTemplate.frame_template(frames=1),),
                 wait_time=0.2,
                 config_updates=None,
                 alarm_box=None,
                 zones: Iterable[ZoneInfo] = ()):
    reset_config()
    if config_updates:
        for (key, value) in config_updates.items():
            events_processor.config[key].update(config_updates[key])

    detections = detections if detections else {0: [TestDetection(score=score, bounding_box=np.array([1, 1, 50, 50]))]}

    injector = Injector([ProcessorModule, TestOverridesModule])

    detector = injector.get(Detector)
    sender = injector.get(NotificationSender)
    zone_reader = injector.get(ZoneReader)
    alarm_box_reader = injector.get(AlarmBoxReader)
    resource_reader = injector.get(ResourceReader)

    detector.detections = {1: detections} if detections else {}
    zone_reader.zones = zones
    alarm_box_reader.box = alarm_box
    resource_reader.events = events
    resource_reader.frames = frames

    controller = injector.get(MainController)

    controller.start(watchdog=False)
    time.sleep(wait_time)
    controller.stop()
    time.sleep(0.2)

    return list(sender.notifications.keys())
