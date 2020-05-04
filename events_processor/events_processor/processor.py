import logging
import os
import time
from threading import Thread
from typing import Any

import cv2
from injector import inject

from events_processor.filters import DetectionFilter
from events_processor.interfaces import Detector, ImageReader, SecondPassDetector
from events_processor.models import FrameInfo, FrameQueue, NotificationQueue
from events_processor.preprocessor import RotatingPreprocessor


def get_frame_score(frame_info: FrameInfo) -> float:
    if len(frame_info.detections) > 0:
        return max([p.score for p in frame_info.detections])
    else:
        return 0


class FSImageReader(ImageReader):
    def read(self, file_name: str) -> Any:
        if os.path.isfile(file_name):
            return cv2.imread(file_name)


class FrameProcessorWorker(Thread):
    log = logging.getLogger("events_processor.FrameProcessorWorker")

    @inject
    def __init__(self,
                 frame_queue: FrameQueue,
                 notification_queue: NotificationQueue,
                 detector: Detector,
                 second_pass_detector: SecondPassDetector,
                 image_reader: ImageReader,
                 detection_filter: DetectionFilter,
                 preprocessor: RotatingPreprocessor):

        super().__init__()
        self._stop_requested = False

        self._frame_queue = frame_queue
        self._notification_queue = notification_queue
        self._detector = detector
        self._second_pass_detector = second_pass_detector
        self._detection_filter = detection_filter
        self._preprocessor = preprocessor

        self._calculate_score = get_frame_score
        self._image_reader = image_reader

    def _read_image_from_fs(self, file_name: str) -> Any:
        if os.path.isfile(file_name):
            return cv2.imread(file_name)

    def run(self) -> None:
        while not self._stop_requested:
            frame_info = self._frame_queue.get()
            if self._stop_requested:
                break

            if frame_info.event_info.notification_sent:
                self.log.info(
                    f"Notification already sent for event: {frame_info.event_info}, skipping processing of frame: {frame_info}")
                continue

            frame_info.image = self._image_reader.read(frame_info.image_path)
            if frame_info.image is None:
                self.log.error(f"Could not read frame image, skipping frame {frame_info}")

            for action in (self._preprocessor.preprocess,
                           self._detector.detect,
                           self._detection_filter.filter_detections,
                           self._second_pass_detector.detect,
                           self._record_event_frame):
                if action:
                    action(frame_info)

        self.log.info(f"Terminating")

    def stop(self) -> None:
        self._stop_requested = True
        self._frame_queue.put(None)

    def _record_event_frame(self, frame_info: FrameInfo) -> None:
        event_info = frame_info.event_info

        score = self._calculate_score(frame_info)

        with event_info.lock:
            if score > event_info.frame_score:
                event_info.frame_info = frame_info
                event_info.frame_score = score

                if event_info.first_detection_time == 0:
                    event_info.first_detection_time = time.monotonic()
                self._notification_queue.put(event_info)
