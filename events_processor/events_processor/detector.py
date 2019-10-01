import logging
from threading import Lock

from PIL import Image

from events_processor import config


class CoralDetector:
    MODEL_FILE = config['coral']['model_file']
    MIN_SCORE = float(config['coral']['min_score'])

    log = logging.getLogger("events_processor.CoralDetector")

    def __init__(self):
        from edgetpu.detection.engine import DetectionEngine
        self._engine = DetectionEngine(self.MODEL_FILE)
        self._engine_lock = Lock()

    def detect(self, frame_info):
        pil_img = Image.fromarray(frame_info.image)
        with self._engine_lock:
            detections = self._engine.DetectWithImage(pil_img,
                                                      threshold=self.MIN_SCORE,
                                                      keep_aspect_ratio=True,
                                                      relative_coord=False, top_k=10)
        frame_info.detections = detections
