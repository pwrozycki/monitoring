import logging
import math
import time
from threading import Lock
from typing import Any

from PIL import Image
from injector import inject

from events_processor.configtools import get_config, ConfigProvider
from events_processor.interfaces import Detector
from events_processor.models import FrameInfo, Rect, Detection


class CoralDetector(Detector):
    log = logging.getLogger("events_processor.CoralDetector")

    @inject
    def __init__(self, config: ConfigProvider):
        self._config = config

        from edgetpu.detection.engine import DetectionEngine
        self._engine = DetectionEngine(config.model_file)
        self._engine_lock = Lock()
        self._pending_processing_start = None

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
                detections = self.detect_in_rect(frame_info, img, Rect(left, top, right, bottom))

                result += detections

        frame_info.detections = result

    def detect_in_rect(self, frame_info: FrameInfo, img: Any, rect: Rect):
        cropped_img = img.crop(rect.box_tuple)
        with self._engine_lock:
            self._pending_processing_start = time.monotonic()
            result = self._engine.DetectWithImage(cropped_img,
                                                  threshold=self._config.min_score,
                                                  keep_aspect_ratio=True,
                                                  relative_coord=False, top_k=1000)
            self._pending_processing_start = None

        detections = []
        for detection in result:
            r = Rect(*map(int, detection.bounding_box.flatten().tolist()))
            d = Detection(r.moved_by(rect.left, rect.top), detection.score, detection.label_id)
            detections.append(d)

        return detections

    def get_pending_processing_seconds(self) -> float:
        start = self._pending_processing_start
        return (time.monotonic() - start) if start else 0
