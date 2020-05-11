import logging
from typing import Any, Iterable

import cv2
import numpy as np
from PIL.ImageColor import getrgb
from injector import inject

from events_processor.configtools import ConfigProvider
from events_processor.models import FrameInfo, Rect, Point
from events_processor.preprocessor import RotatingPreprocessor
from events_processor.shapeutils import bounding_box


class DetectionRenderer:
    log = logging.getLogger('events_processor.DetectionRenderer')

    @inject
    def __init__(self, config: ConfigProvider, preprocessor: RotatingPreprocessor):
        self._config = config
        self._preprocessor = preprocessor

    def annotate_image(self, frame_info: FrameInfo) -> Any:
        self.log.debug(f'Rendering detection: {frame_info}')

        image = frame_info.image
        for (i, detection) in enumerate(reversed(frame_info.detections)):
            discarded = bool(detection.discard_reasons)

            box = detection.rect
            self.draw_rect(image, detection.rect, "blue" if not discarded else "white", 1)
            self._draw_text(f'{detection.label} {detection.score * 100:.0f}%', box, image)

        def rect_drawer(box, color):
            return self.draw_rect(image, box, color, 2)

        self.draw_boxes(frame_info, rect_drawer)

        monitor_id = frame_info.event_info.monitor_id
        for poly in self._config.excluded_zone_polygons.get(monitor_id, []):
            self.draw_poly(image, self.transform_points(frame_info, poly.polygon.points))

        for poly in self._config.excluded_polygons.get(monitor_id, []):
            self.draw_poly(image, self.transform_points(frame_info, poly.points))

    def transform_points(self, frame_info, pts):
        monitor_id = frame_info.event_info.monitor_id
        width = frame_info.event_info.width
        height = frame_info.event_info.height

        return [self._preprocessor.transform_coords(monitor_id, width, height, pt) for pt in pts]

    def draw_rect(self, image, box: Rect, color, thickness):
        rect = Rect(*map(int, box.box_tuple))
        cv2.rectangle(image, rect.top_left.tuple, rect.bottom_right.tuple, self.bgr(color), thickness)

        return image

    def draw_poly(self, image, points: Iterable[Point], color="red", alpha=0.4):
        box = bounding_box(points)
        (x, y) = box.top_left.tuple
        (h, w) = box.height, box.width

        overlay = image[y:y + h, x:x + w].copy()

        a3 = np.array([[*([*p.moved_by(-x, -y).tuple] for p in points)]], dtype=np.int32)
        cv2.fillPoly(overlay, a3, color=np.uint8(np.array(self.bgr(color))).tolist())

        res = cv2.addWeighted(overlay, alpha, image[y:y + h, x:x + w], 1 - alpha, 0)

        image[y:y + h, x:x + w] = res

    def bgr(self, color):
        (r, g, b) = getrgb(color)
        return b, g, r

    def draw_boxes(self, frame_info, rect_drawer):
        if frame_info.alarm_box:
            box = frame_info.alarm_box
            rect_drawer(box, "blue")

        for (i, r) in enumerate(frame_info.chunk_rects):
            color = COLORS[i % len(COLORS)]
            rect_drawer(r, color)

    def _draw_text(self, text: str, box: Rect, image: Any) -> None:
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.5
        font_thickness = 1
        text_size = cv2.getTextSize(text, font, font_scale, font_thickness)[0]
        y_offset = text_size[1]
        top_left = box.top_left
        cv2.rectangle(image,
                      top_left.tuple, top_left.moved_by(text_size[0], text_size[1] + 1).tuple, (0, 0, 0), cv2.FILLED)
        cv2.putText(image, text, top_left.moved_by(0, y_offset).tuple, font, font_scale, (0, 255, 0), font_thickness)


COLORS = ["#C755FF", "#FF8C1F", "#FEFF3B", "#85FF41", "#37FFB3", "#38D3FF", "#2427FF"]
