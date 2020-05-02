from typing import Tuple, Any

import cv2
from injector import inject

from events_processor.configtools import ConfigProvider, extract_config
from events_processor.models import FrameInfo, Point


# TODO: prozycki: refactor and move to separate classes: logic responsible for:
# - reading configuration
# - applying transformation to point

class RotatingPreprocessor:
    @inject
    def __init__(self, config: ConfigProvider):
        self._rotations = extract_config(config, 'rotating_preprocessor', 'rotate', int)

    def preprocess(self, frame_info: FrameInfo) -> None:
        monitor_id = frame_info.event_info.event_json['MonitorId']
        rotation = self._rotations.get(monitor_id, 0)
        if rotation != 0:
            frame_info.image = self.rotate_and_expand_image(frame_info.image, rotation)

    @classmethod
    def rotate_and_expand_image(cls, mat: Any, angle: float) -> Any:
        h, w = mat.shape[:2]
        bound_h, bound_w, rotation_mat = cls._get_rotation_matrix(angle, w, h)

        return cv2.UMat.get(
            cv2.warpAffine(cv2.UMat(mat), rotation_mat, (bound_w, bound_h), flags=cv2.INTER_CUBIC))

    def transform_coords(self, monitor_id: str, w: int, h: int, point: Point) -> Point:
        angle = self._rotations.get(monitor_id, 0)
        if angle == 0:
            return point
        rotation_matrix = self._get_rotation_matrix(angle, w, h)[2]
        result = rotation_matrix.dot((point.x, point.y, 1))
        return Point(*result)

    @staticmethod
    def _get_rotation_matrix(angle: float, w: int, h: int) -> Tuple[int, int, Any]:
        image_center = (w / 2, h / 2)
        rotation_mat = cv2.getRotationMatrix2D(image_center, angle, 1.)
        abs_cos = abs(rotation_mat[0, 0])
        abs_sin = abs(rotation_mat[0, 1])
        bound_w = int(h * abs_sin + w * abs_cos)
        bound_h = int(h * abs_cos + w * abs_sin)
        rotation_mat[0, 2] += bound_w / 2 - image_center[0]
        rotation_mat[1, 2] += bound_h / 2 - image_center[1]
        return bound_h, bound_w, rotation_mat
