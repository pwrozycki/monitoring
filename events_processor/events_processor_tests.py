import json
import time

import numpy as np

from events_processor import FSNotificationSender, MainController, FrameReader, config


class Response:
    pass


class Detection:
    def __init__(self, label_id=1, score=0.5, bounding_box=np.array([1, 1, 50, 50])):
        self.label_id = label_id
        self.bounding_box = bounding_box
        self.score = score


class ResourceProducer:
    def __init__(self):
        self._event_list_invocation = None
        self.events = [
            self.event_template(event_id=1),
            self.event_template(event_id=1),
            self.event_template(event_id=1, end_time="2020-01-01"),
        ]
        self.event_details = [
            self.frame_template(event_id=1, frames=5),
            self.frame_template(event_id=1, frames=10),
            self.frame_template(event_id=1, frames=15, end_time="2020-01-01"),
        ]

    @staticmethod
    def event_template(event_id, end_time=None):
        return {
            'events': [{
                'Event': {
                    'Id': event_id,
                    'EndTime': end_time,
                    'MonitorId': '1',
                    'StartTime': '2019-08-08'
                }
            }],
            'pagination': {
                'page': 1,
                'pageCount': 1
            }
        }

    def frame_template(self, event_id, frames=0, end_time=None):
        return {
            'event': {
                'Event': self.event_template(event_id, end_time)['events'][0]['Event'],
                'Frame': [
                    {
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
                self._event_list_invocation = min(len(self.events) - 1, self._event_list_invocation + 1)
            content = self.events[self._event_list_invocation]
        elif url.find('/api/events') != -1:
            content = self.event_details[self._event_list_invocation]

        response.content = json.dumps(content)
        return response


class MockDetector:
    DETECTIONS = {
        1: {
            1: [Detection(score=0.5)],
            5: [Detection(score=0.9)],
        }
    }

    def __init__(self):
        pass

    def detect(self, frame_info):
        time.sleep(0.1)
        event_id = frame_info.frame_json['EventId']
        frame_id = frame_info.frame_json['FrameId']
        frame_info.detections = self.DETECTIONS.get(event_id, {}).get(frame_id, [])


def get_image(file_name):
    img = np.zeros((1000, 1000, 3), np.uint8)
    img[::] = (255, 255, 255)
    return img


def main():
    del (config['detection_filter']['excluded_polygons1'])
    config['threading']['thread_watchdog_delay'] = '1'
    config['timings']['event_loop_seconds'] = '1'
    event_controller = MainController(send_notification=FSNotificationSender().send_notification,
                                      detect=MockDetector().detect,
                                      frame_reader=FrameReader(get_resource=ResourceProducer().get_resource,
                                                               read_image=get_image))
    event_controller.run()


if __name__ == '__main__':
    main()
