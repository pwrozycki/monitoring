import unittest

import numpy as np

from events_processor.models import Rect, ZoneInfo
from tests.pipeline import TestDetection, ResourceTemplate, run_pipeline


class DetectionTestCase(unittest.TestCase):

    def test_single_detection(self):
        notifications = run_pipeline(score=0.8)
        self.assertAlmostEqual(notifications[0].frame_info.score, 0.8)

    def test_detection_picks_frame_with_higher_score(self):
        notifications = run_pipeline(
            detections={
                '0': [TestDetection(score=0.8)],
                '2': [TestDetection(score=0.9)],
            },
            events=[
                ResourceTemplate.event_template(end_time=None),
                ResourceTemplate.event_template(),
            ],
            frames=[
                ResourceTemplate.frame_template(end_time=None, frames=3),
                ResourceTemplate.frame_template(frames=3),
            ],
            config_updates={
                'detection_filter': {'min_accepted_frames': '2'}
            }
        )
        self.assertAlmostEqual(notifications[0].frame_info.score, 0.9)

    def test_not_enough_accepted_frames(self):
        notifications = run_pipeline(
            detections={
                '0': [TestDetection(score=0.8)],
                '2': [TestDetection(score=0.9)],
            },
            events=[
                ResourceTemplate.event_template(end_time=None),
                ResourceTemplate.event_template(),
            ],
            frames=[
                ResourceTemplate.frame_template(end_time=None, frames=3),
                ResourceTemplate.frame_template(frames=3),
            ],
            config_updates={
                'detection_filter': {'min_accepted_frames': '3'}
            }
        )
        self.assertAlmostEqual(len(notifications), 0)

    def _test_detection_excluded_point(self, detection, exclusion):
        notifications = run_pipeline(
            detections={
                '0': [TestDetection(bounding_box=np.array(detection))],
            },
            config_updates={
                'detection_filter': {'excluded_points1': exclusion}
            }
        )
        return notifications

    def test_detection_excluded_point_within_detection(self):
        notifications = self._test_detection_excluded_point(detection=[1, 1, 3, 3], exclusion='2,2')
        self.assertEqual(len(notifications), 0)

    def test_detection_excluded_point_not_within_detection(self):
        notifications = self._test_detection_excluded_point(detection=[1, 1, 3, 3], exclusion='4,4')
        self.assertEqual(len(notifications), 1)

    def test_detection_excluded_polygons_within_detection(self):
        notifications = run_pipeline(
            detections={
                '0': [TestDetection(bounding_box=np.array([1, 1, 50, 50]))],
            },
            config_updates={
                'detection_filter': {'excluded_polygons1': '2,2,3,3,4,4'}
            }
        )

        self.assertEqual(len(notifications), 0)

    def test_detection_excluded_zone_polygons_within_detection(self):
        notifications = self._test_detection_excluded_zone_polygons('0,999 0,1000 1,1000 1,999', [0, 1000, 1, 999])
        self.assertEqual(len(notifications), 0)

    def test_detection_excluded_zone_polygons_outside_detection(self):
        notifications = self._test_detection_excluded_zone_polygons('0,999 0,1000 1,1000 1,999', [0, 998, 1, 997])
        self.assertEqual(len(notifications), 1)

    def _test_detection_excluded_zone_polygons(self, excluded_zone, detection):
        notifications = run_pipeline(
            detections={
                '0': [TestDetection(score=0.4, bounding_box=np.array(detection))],
            },
            zones=(ZoneInfo('1', 1000, 1000, 'exclusion', excluded_zone),)
        )
        return notifications

    def test_detection_excluded_zone_polygons_within_detection_rotation(self):
        notifications = run_pipeline(
            detections={
                '0': [TestDetection(score=0.4, bounding_box=np.array([0, 1000, 1, 999]))],
            },
            config_updates={
                'rotating_preprocessor': {'rotate1': '90'},
            },
            zones=(ZoneInfo('1', 1000, 1000, 'exclusion', '0,0 0,1 1,1 1,0'),)
        )

        self.assertEqual(len(notifications), 0)

    def test_detection_no_movement_too_small_score(self):
        notifications = run_pipeline(
            detections={
                '0': [TestDetection(bounding_box=np.array([0, 1, 50, 50]))],
            },
            config_updates={
                'detection_filter': {'movement_indifferent_min_score': '0.9'}
            }
        )

        self.assertEqual(len(notifications), 0)

    def _test_detection_coarse_movement(self, score):
        notifications = run_pipeline(
            detections={
                '0': [TestDetection(score=score, bounding_box=np.array([1, 1, 50, 50]))],
            },
            config_updates={
                'detection_filter': {'movement_indifferent_min_score': '0.9',
                                     'coarse_movement_min_score': '0.85'}
            },
            alarm_box=Rect(1, 1, 500, 500)
        )
        return notifications

    def test_detection_coarse_movement(self):
        notifications = self._test_detection_coarse_movement(0.86)
        self.assertAlmostEqual(notifications[0].frame_info.score, 0.86)

    def test_detection_coarse_movement_too_small_score(self):
        notifications = self._test_detection_coarse_movement(0.8)
        self.assertEqual(len(notifications), 0)

    def test_detection_precise_movement_rotation(self):
        notifications = run_pipeline(
            detections={
                '0': [TestDetection(score=0.4, bounding_box=np.array([0, 1000, 1, 999]))],
            },
            config_updates={
                'rotating_preprocessor': {'rotate1': '90'},
                'detection_filter': {'movement_indifferent_min_score': '0.9',
                                     'coarse_movement_min_score': '0.85',
                                     'precise_movement_min_score': '0.3'}
            },
            alarm_box=Rect(0, 0, 1, 1)
        )
        self.assertAlmostEqual(notifications[0].frame_info.score, 0.4)

    def _test_detection_precise_movement(self, score=0.4):
        notifications = run_pipeline(
            detections={
                '0': [TestDetection(score=score, bounding_box=np.array([1, 1, 50, 50]))],
            },
            config_updates={
                'detection_filter': {'movement_indifferent_min_score': '0.9',
                                     'coarse_movement_min_score': '0.85',
                                     'precise_movement_min_score': '0.3',
                                     'max_alarm_to_intersect_diff': '75'}
            },
            alarm_box=Rect(1, 1, 50, 50)
        )
        return notifications

    def test_detection_precise_movement(self):
        notifications = self._test_detection_precise_movement()
        self.assertAlmostEqual(notifications[0].frame_info.score, 0.4)

    def test_detection_precise_movement_too_small_score(self):
        notifications = self._test_detection_precise_movement(score=0.2)
        self.assertEqual(len(notifications), 0)
