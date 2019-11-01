import logging
import math
import re
import time
from threading import Lock

import numpy as np
from PIL import Image
from events_processor import config
from events_processor.configtools import get_config, set_config


class CoralDetector:
    MODEL_FILE = config['coral']['model_file']
    MIN_SCORE = float(config['coral']['min_score'])

    log = logging.getLogger("events_processor.CoralDetector")

    def __init__(self):
        from edgetpu.detection.engine import DetectionEngine
        self._engine = DetectionEngine(self.MODEL_FILE)
        self._engine_lock = Lock()
        self._pending_processing_start = None

        self._detection_chunks = {}
        for (key, value) in config['coral'].items():
            set_config(key, value, 'detection_chunks', self._detection_chunks, self._extract_int_pair)

    def _extract_int_pair(self, value):
        m = re.search(r'(\d+)x(\d+)', value)
        if m:
            return [int(x) for x in m.groups()]
        return None

    def detect(self, frame_info):
        (h, w, _) = frame_info.image.shape

        monitor_id = frame_info.event_info.event_json['MonitorId']
        (x_chunks, y_chunks) = get_config(self._detection_chunks, monitor_id, (1, 1))

        chunk_width = w // x_chunks
        chunk_height = h // y_chunks

        result = []

        for y in range(y_chunks):
            for x in range(x_chunks):
                left_x = math.ceil(chunk_width * x)
                right_x = math.ceil(chunk_width * (x + 1))
                top_y = math.ceil(chunk_height * y)
                bottom_y = math.ceil(chunk_height * (y + 1))
                detections = self.detect_in_rect(frame_info, left_x, top_y, right_x, bottom_y)

                result += detections

        frame_info.detections = result

    def detect_in_rect(self, frame_info, left_x, top_y, right_x, bottom_y):
        cropped_img = Image.fromarray(frame_info.image[top_y:bottom_y, left_x:right_x])
        self.log.debug(f"waiting for lock - frame: {frame_info}")
        with self._engine_lock:
            self.log.debug(f"starting detection - frame: {frame_info}")
            self._pending_processing_start = time.monotonic()
            detections = self._engine.DetectWithImage(cropped_img,
                                                      threshold=self.MIN_SCORE,
                                                      keep_aspect_ratio=True,
                                                      relative_coord=False, top_k=1000)
            self._pending_processing_start = None
            self.log.debug(f"detection done - frame: {frame_info}")

        for detection in detections:
            (x1, y1, x2, y2) = detection.bounding_box.flatten().tolist()
            detection.bounding_box = np.array([x1 + left_x, y1 + top_y, x2 + left_x, y2 + top_y])
        return detections

    def detect_str(self, detections):
        return [f"label: {d.label_id}, score: {d.score}, box: {[int(c) for c in d.bounding_box.flatten().tolist()]}" for
                d in detections]

    def get_pending_processing_seconds(self):
        return (time.monotonic() - self._pending_processing_start) if self._pending_processing_start else 0
