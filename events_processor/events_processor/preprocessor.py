import re

import cv2

from events_processor import config


class RotatingPreprocessor:
    def __init__(self):
        self._config_parse_rotations()

    def _config_parse_rotations(self):
        self._rotations = {}
        for (key, value) in config['rotating_preprocessor'].items():
            match = re.match(r'rotate(\d+)', key)
            if match:
                self._rotations[match.group(1)] = value

    def preprocess(self, frame_info):
        monitor_id = frame_info.event_info.event_json['MonitorId']
        rotation = int(self._rotations.get(monitor_id, '0'))
        if rotation != 0:
            frame_info.image = self.rotate_and_expand_image(frame_info.image, rotation)

    @staticmethod
    def rotate_and_expand_image(mat, angle):
        h, w = mat.shape[:2]
        image_center = (w / 2, h / 2)
        rotation_mat = cv2.getRotationMatrix2D(image_center, angle, 1.)

        abs_cos = abs(rotation_mat[0, 0])
        abs_sin = abs(rotation_mat[0, 1])

        bound_w = int(h * abs_sin + w * abs_cos)
        bound_h = int(h * abs_cos + w * abs_sin)

        rotation_mat[0, 2] += bound_w / 2 - image_center[0]
        rotation_mat[1, 2] += bound_h / 2 - image_center[1]

        return cv2.UMat.get(
            cv2.warpAffine(cv2.UMat(mat), rotation_mat, (bound_w, bound_h), flags=cv2.INTER_CUBIC))
