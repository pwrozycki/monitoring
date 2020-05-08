import logging
import math
import time
from threading import Lock
from typing import Any

from PIL import Image
from PIL.ImageDraw import Draw
from injector import inject

from events_processor.configtools import get_config, ConfigProvider
from events_processor.interfaces import Detector, Engine, AlarmBoxReader
from events_processor.models import FrameInfo, Rect, Detection
from events_processor.preprocessor import RotatingPreprocessor
from events_processor.renderer import DetectionRenderer
from events_processor.shapeutils import bounding_box


class SynchronizedDetectionEngine(Engine):
    @inject
    def __init__(self, config: ConfigProvider):
        from edgetpu.detection.engine import DetectionEngine
        self._engine = DetectionEngine(config.detector_model_file)
        self._engine_lock = Lock()
        self._pending_processing_start = 0

    def detect(self, img, threshold):
        with self._engine_lock:
            self._pending_processing_start = time.monotonic()
            result = self._engine.DetectWithImage(img,
                                                  threshold=threshold,
                                                  keep_aspect_ratio=True,
                                                  relative_coord=False, top_k=1000)
            self._pending_processing_start = 0
            return result

    def get_pending_processing_seconds(self) -> float:
        start = self._pending_processing_start
        return (time.monotonic() - start) if start != 0 else 0


class CoralDetector(Detector):
    log = logging.getLogger("events_processor.CoralDetector")

    @inject
    def __init__(self,
                 config: ConfigProvider,
                 engine: SynchronizedDetectionEngine,
                 alarm_box_reader: AlarmBoxReader,
                 preprocessor: RotatingPreprocessor,
                 detection_renderer: DetectionRenderer):
        self._config = config
        self._engine = engine
        self._alarm_box_reader = alarm_box_reader
        self._transform_coords = preprocessor.transform_coords
        self._detection_renderer = detection_renderer

    def detect(self, frame_info: FrameInfo) -> None:
        monitor_id = frame_info.event_info.monitor_id
        img = Image.fromarray(frame_info.image)

        box = self._calculate_detection_box(frame_info, img, monitor_id)

        frame_info.chunk_rects = self._calculate_chunk_rects(box, img, monitor_id)

        result = []
        for rect in frame_info.chunk_rects:
            detections = self.detect_in_rect(img, rect)
            result += detections

        self._debug_draw(frame_info, img)

        frame_info.detections = result

    def _calculate_detection_box(self, frame_info, img, monitor_id):
        event_id = frame_info.event_id
        frame_id = frame_info.frame_id
        height = frame_info.event_info.height
        width = frame_info.event_info.width

        alarm_box = self._alarm_box_reader.read(event_id, frame_id, self._config.excluded_zone_prefix)
        if alarm_box:
            transformed_points = (self._transform_coords(monitor_id, width, height, pt)
                                  for pt in alarm_box.points)
            box = bounding_box(transformed_points)
            frame_info.alarm_box = box
        else:
            box = Rect(0, 0, img.width, img.height)
        return box

    def _calculate_chunk_rects(self, box, img, monitor_id):
        x_chunks, y_chunks = self._calculate_optimal_chunks_number(box, monitor_id)
        box = self._rect_expanded_to_box(box, 300 * x_chunks, 300 * y_chunks, img.width, img.height)
        chunk_rects = self._chunk_rects(box, x_chunks, y_chunks)

        if x_chunks != 1 or y_chunks != 1:
            box = self._rect_expanded_to_box(box, 300, 300, img.width, img.height)
            chunk_rects.append(box)

        return chunk_rects

    def _calculate_optimal_chunks_number(self, box, monitor_id):
        configured_chunks = get_config(self._config.detection_chunks, monitor_id, (1, 1))
        x_chunks = max(min(configured_chunks[0], math.ceil(box.width / 300)), 1)
        y_chunks = max(min(configured_chunks[1], math.ceil(box.height / 300)), 1)
        return x_chunks, y_chunks

    def _rect_expanded_to_box(self, rect, ideal_box_w, ideal_box_h, clip_w, clip_h):
        r = Rect(*map(int, rect.box_tuple))
        mid_x, mid_y = ((r.left + r.right) // 2, (r.top + r.bottom) // 2)

        box_h, box_w = self._calculate_box_size(r, ideal_box_w, ideal_box_h, clip_w, clip_h)

        return self._calculate_expanded_box(mid_x, mid_y, box_h, box_w, clip_h, clip_w)

    def _debug_draw(self, frame_info, img):
        if self._config.debug_images:
            dw = Draw(img)

            def rect_drawer(box, color):
                dw.line([pt.tuple for pt in (box.points * 2)[:5]], fill=color, width=4)

            self._detection_renderer.draw_boxes(frame_info, rect_drawer)

            img.save(f"debug_{frame_info.event_id}_{frame_info.frame_id}.jpg")

    def _chunk_rects(self, box, x_chunks, y_chunks):
        rects = []
        chunk_width = box.width // x_chunks
        chunk_height = box.height // y_chunks
        for y in range(y_chunks):
            for x in range(x_chunks):
                left = math.ceil(chunk_width * x)
                right = math.ceil(chunk_width * (x + 1)) - 1
                top = math.ceil(chunk_height * y)
                bottom = math.ceil(chunk_height * (y + 1)) - 1
                rect = Rect(left, top, right, bottom).moved_by(*box.top_left.tuple)
                rects.append(rect)
        return rects

    def detect_in_rect(self, img: Any, rect: Rect):
        cropped_img = img.crop(rect.box_tuple)

        result = self._engine.detect(cropped_img, self._config.min_score)

        detections = []
        for detection in result:
            r = Rect(*map(int, detection.bounding_box.flatten().tolist()))
            d = Detection(r.moved_by(rect.left, rect.top), detection.score, detection.label_id)
            detections.append(d)

        return detections

    def _calculate_box_size(self, r, ideal_box_w, ideal_box_h, clip_w, clip_h):
        (box_w, box_h) = r.width, r.height

        ideal_box_ratio = ideal_box_h / ideal_box_w
        desired_h = int(box_w * ideal_box_ratio)
        desired_w = int(box_h / ideal_box_ratio)

        if box_w < ideal_box_w and box_h < ideal_box_h:
            (box_w, box_h) = (ideal_box_w, ideal_box_h)
        elif box_h < desired_h:
            box_h = min(desired_h, clip_h)
        elif box_w < desired_w:
            box_w = min(desired_w, clip_w)

        return box_h, box_w

    def _calculate_expanded_box(self, mid_x, mid_y, box_h, box_w, clip_h, clip_w):
        (left, right) = self._keep_within_range(mid_x - box_w // 2, mid_x + box_w // 2 - 1, 0, clip_w)
        (top, bottom) = self._keep_within_range(mid_y - box_h // 2, mid_y + box_h // 2 - 1, 0, clip_h)
        re = Rect(*map(int, (left, top, right, bottom)))
        return re

    def _keep_within_range(self, a, b, min, max):
        if a < min:
            offset = min - a
            a = a + offset
            b = b + offset
        elif b >= max:
            offset = b - (max - 1)
            b = b - offset
            a = a - offset

        return a, b
