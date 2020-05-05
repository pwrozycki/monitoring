import logging
from typing import Any

import cv2
from PIL.ImageColor import getrgb

from events_processor.models import FrameInfo, Rect


class DetectionRenderer:
    log = logging.getLogger('events_processor.DetectionRenderer')

    def annotate_image(self, frame_info: FrameInfo) -> Any:
        image = frame_info.image
        detections = frame_info.detections
        for (i, detection) in enumerate(detections):
            box = detection.rect
            cv2.rectangle(image, box.top_left.tuple, box.bottom_right.tuple, (255, 0, 0), 1)

            area_percents = 100 * box.area / Rect(0, 0, *frame_info.image.shape[:2]).area
            score_percents = 100 * detection.score

            self.log.debug(f'Rendering detection: {frame_info.event_info} (index: {i}, '
                           f'score: {score_percents:.0f}%, area: {area_percents:.2f}%), box: {box}')

            self._draw_text(f'{score_percents:.0f}%', box, image)

        def rect_drawer(box: Rect, color):
            rect = Rect(*map(int, box.box_tuple))
            cv2.rectangle(image, rect.top_left.tuple, rect.bottom_right.tuple, getrgb(color), 2)

        DetectionRenderer().draw_boxes(frame_info, rect_drawer)

        return image

    def draw_boxes(self, frame_info, rect_drawer):
        if frame_info.alarm_box:
            box = frame_info.alarm_box
            rect_drawer(box, "red")
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
