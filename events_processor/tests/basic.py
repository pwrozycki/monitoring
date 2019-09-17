import time
import unittest

from events_processor.controller import MainController
from events_processor.reader import FrameReader
from tests.mocks import Detection, Resource, Detector, Sender, get_image, sleep


class DetectionTestCase(unittest.TestCase):
    def test_detection_picks_frame_with_higher_score(self):
        res = Resource()

        res.set_events(
            res.event_template(event_id=1),
            res.event_template(event_id=1),
            res.event_template(event_id=1, end_time="2020-01-01"),
        )
        res.set_frames(
            res.frame_template(event_id=1, frames=5),
            res.frame_template(event_id=1, frames=10),
            res.frame_template(event_id=1, frames=15, end_time="2020-01-01"),
        )

        detector = Detector()
        detector.set_detections(
            {
                1: {
                    1: [Detection(score=0.5)],
                    5: [Detection(score=0.9)],
                }
            }
        )

        sender = Sender()
        controller = MainController(send_notification=sender.send_notification,
                                    detect=detector.detect,
                                    frame_reader=FrameReader(get_resource=res.get_resource),
                                    read_image=get_image,
                                    sleep=sleep)
        controller.start(watchdog=False)
        time.sleep(2)
        controller.stop()
        time.sleep(1)

        self.assertAlmostEqual(list(sender.notifications.keys())[0].frame_score, 0.9)
