import copy
import json
import time

import numpy as np

import events_processor
from events_processor.controller import MainController
from events_processor.reader import FrameReader


class Response:
    pass


class Detection:
    def __init__(self, label_id=0, score=0.5, bounding_box=np.array([1, 1, 50, 50])):
        self.label_id = label_id
        self.bounding_box = bounding_box
        self.score = score


class ResourceTemplate:
    @staticmethod
    def event_template(event_id=1, end_time="2020-01-01", monitor_id='1'):
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
                        'EventId': event_id,
                    } for x in range(frames)
                ]
            }
        }


class ResourceProducer:
    def __init__(self, events=(), frames=()):
        self._event_list_invocation = None
        self._events = events
        self._frames = frames

    def get_resource(self, url):
        response = Response()
        response.status_code = 200

        content = None
        if url.find('/api/events/index') != -1:
            if self._event_list_invocation is None:
                self._event_list_invocation = 0
            else:
                self._event_list_invocation = min(len(self._events) - 1, self._event_list_invocation + 1)
            content = self._events[self._event_list_invocation]
        elif url.find('/api/events') != -1:
            content = self._frames[self._event_list_invocation]

        response.content = json.dumps(content)
        return response


class Detector:
    def __init__(self, detections={}):
        self._detections = detections

    def detect(self, frame_info):
        event_id = frame_info.frame_json['EventId']
        frame_id = frame_info.frame_json['FrameId']
        frame_info.detections = self._detections.get(event_id, {}).get(frame_id, [])


class Sender:
    def __init__(self):
        self.notifications = {}

    def send_notification(self, event_info, subject, message):
        self.notifications[copy.copy(event_info)] = (subject, message)
        print(f"Sending notification with score {event_info.frame_score}")
        return True


def get_image(file_name):
    img = np.zeros((1000, 1000, 3), np.uint8)
    img[::] = (255, 255, 255)
    return img


def sleep(t):
    time.sleep(t / 50)


def reset_config():
    for section in events_processor.config.sections():
        events_processor.config.remove_section(section)
    events_processor.read_config()


def run_pipeline(detections=None,
                 score=0.8,
                 events=(ResourceTemplate.event_template(),),
                 frames=(ResourceTemplate.frame_template(frames=1),),
                 wait_time=0.2,
                 config_updates={},
                 retrieve_alarm_stats=lambda *a: [],
                 retrieve_zones=lambda *a: []):
    reset_config()
    events_processor.config.update(config_updates)

    res = ResourceProducer(events=events, frames=frames)
    detections = detections if detections else {0: [Detection(score=score, bounding_box=np.array([1, 1, 50, 50]))]}
    detector = Detector({1: detections})
    sender = Sender()

    controller = MainController(send_notification=sender.send_notification,
                                detect=detector.detect,
                                frame_reader=FrameReader(get_resource=res.get_resource),
                                read_image=get_image,
                                retrieve_alarm_stats=retrieve_alarm_stats,
                                retrieve_zones=retrieve_zones,
                                sleep=sleep)

    controller.start(watchdog=False)
    time.sleep(wait_time)
    controller.stop()
    time.sleep(0.2)

    return list(sender.notifications.keys())
