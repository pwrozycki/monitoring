import logging
import math
import time
from threading import Lock
from typing import Any

from PIL import Image
from injector import inject

from events_processor.configtools import get_config, ConfigProvider
from events_processor.interfaces import Detector, Engine
from events_processor.models import FrameInfo, Rect, Detection


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
                 engine: SynchronizedDetectionEngine):
        self._config = config
        self._engine = engine

    def detect(self, frame_info: FrameInfo) -> None:
        (h, w, _) = frame_info.image.shape

        monitor_id = frame_info.event_info.event_json['MonitorId']
        (x_chunks, y_chunks) = get_config(self._config.detection_chunks, monitor_id, (1, 1))

        chunk_width = w // x_chunks
        chunk_height = h // y_chunks

        result = []
        img = Image.fromarray(frame_info.image)
        for y in range(y_chunks):
            for x in range(x_chunks):
                left = math.ceil(chunk_width * x)
                right = math.ceil(chunk_width * (x + 1))
                top = math.ceil(chunk_height * y)
                bottom = math.ceil(chunk_height * (y + 1))
                detections = self.detect_in_rect(img, Rect(left, top, right, bottom))

                result += detections

        frame_info.detections = result

    def detect_in_rect(self, img: Any, rect: Rect):
        cropped_img = img.crop(rect.box_tuple)

        result = self._engine.detect(cropped_img, self._config.min_score)

        detections = []
        for detection in result:
            r = Rect(*map(int, detection.bounding_box.flatten().tolist()))
            d = Detection(r.moved_by(rect.left, rect.top), detection.score, detection.label_id)
            detections.append(d)

        return detections


# TODO: prozycki:
#  CONSIDER RUNNING FIRST PASS DETECTION ONLY ON ALARM BOX - PERHAPS ONLY ONE DETECTION REGION IN MOST CASES?
#  at least several (2) ? matching frames per event

class SecondPassCoralDetector(Detector):
    log = logging.getLogger("events_processor.SecondPassCoralDetector")

    @inject
    def __init__(self,
                 config: ConfigProvider,
                 engine: SynchronizedDetectionEngine):
        self._config = config
        self._engine = engine

    def detect(self, frame_info: FrameInfo):
        detections = []
        for detection in frame_info.detections:
            image = frame_info.image
            (height, width, _) = image.shape

            rect = _rect_expanded_to_box(detection.rect, 300, 300, width, height)

            img = Image.fromarray(image[rect.top:rect.bottom, rect.left:rect.right])
            # TODO: prozycki: parametrize threshold, label_id
            result = self._engine.detect(img, threshold=0.7)
            matching = [x for x in result if x.label_id == 0]
            if matching:
                self.log.debug([(str(frame_info), x.score, x.label_id) for x in matching])
                detections.append(detection)

        frame_info.detections = detections


def _rect_expanded_to_box(rect, box_w, box_h, clip_w, clip_h):
    if rect.width > box_w or rect.height > box_h:
        return rect

    mid_x = (rect.left + rect.right) // 2
    (left, right) = _keep_within_range(mid_x - box_w // 2, mid_x + box_w // 2, 0, clip_w)
    mid_y = (rect.top + rect.bottom) // 2
    (top, bottom) = _keep_within_range(mid_y - box_h // 2, mid_y + box_h // 2, 0, clip_h)
    return Rect(left, top, right, bottom)


def _keep_within_range(a, b, min, max):
    if a < min:
        offset = min - a
        a = a + offset
        b = b + offset
    elif b >= max:
        offset = b - (max - 1)
        b = b - offset
        a = a - offset

    return a, b
