import json
import time

import numpy as np


class Response:
    pass


class Detection:
    def __init__(self, label_id=0, score=0.5, bounding_box=np.array([1, 1, 50, 50])):
        self.label_id = label_id
        self.bounding_box = bounding_box
        self.score = score


class Resource:
    def __init__(self):
        self._event_list_invocation = None
        self._events = []
        self._frames = []

    def set_events(self, *events):
        self._events = events

    def set_frames(self, *frames):
        self._frames = frames

    @staticmethod
    def event_template(event_id, end_time=None):
        return {
            'events': [{
                'Event': {
                    'Id': event_id,
                    'EndTime': end_time,
                    'MonitorId': '1',
                    'Emailed': '0',
                    'StartTime': '2019-08-08',
                    'Length': '10',
                    'Frames': '100',
                    'AlarmFrames': '50',
                    'TotScore': '702',
                    'AvgScore': '7',
                    'MaxScore': '17',
                }
            }],
            'pagination': {
                'page': 1,
                'pageCount': 1
            }
        }

    @classmethod
    def frame_template(cls, event_id, frames=0, end_time=None):
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
    def __init__(self):
        self._detections = {}

    def set_detections(self, detections):
        self._detections = detections

    def detect(self, frame_info):
        time.sleep(0.1)
        event_id = frame_info.frame_json['EventId']
        frame_id = frame_info.frame_json['FrameId']
        frame_info.detections = self._detections.get(event_id, {}).get(frame_id, [])


class Sender:
    def __init__(self):
        self.notifications = {}

    def send_notification(self, event_info, subject, message):
        self.notifications[event_info] = (subject, message)
        return True


def get_image(file_name):
    img = np.zeros((1000, 1000, 3), np.uint8)
    img[::] = (255, 255, 255)
    return img


def sleep(t):
    time.sleep(t / 20)
