import logging
import os
import time
from queue import Queue
from threading import Thread
from typing import Callable, Optional, Any, Iterable

import cv2

from events_processor.filters import DetectionFilter
from events_processor.models import FrameInfo, EventInfo, Rect, ZoneInfo
from events_processor.preprocessor import RotatingPreprocessor


def get_frame_score(frame_info: FrameInfo) -> float:
    if len(frame_info.detections) > 0:
        return max([p.score for p in frame_info.detections])
    else:
        return 0


class FrameProcessorWorker(Thread):
    log = logging.getLogger("events_processor.FrameProcessorWorker")

    def __init__(self,
                 frame_queue: 'Queue[FrameInfo]',
                 detect: Optional[Callable[[FrameInfo], None]],
                 notification_queue: 'Queue[EventInfo]',
                 retrieve_alarm_stats: Callable[[str, str], Rect] = None,
                 retrieve_zones: Callable[[], Iterable[ZoneInfo]] = None,
                 calculate_score: Callable[[FrameInfo], float] = get_frame_score,
                 read_image: Callable[[str], Any] = None):
        super().__init__()
        self._stop_requested = False

        self._frame_queue = frame_queue
        self._notification_queue = notification_queue
        self._preprocessor = RotatingPreprocessor()
        self._detect = detect
        self._filter_detections = DetectionFilter(transform_coords=self._preprocessor.transform_coords,
                                                  retrieve_alarm_stats=retrieve_alarm_stats,
                                                  retrieve_zones=retrieve_zones
                                                  ).filter_detections
        self._calculate_score = calculate_score
        self._read_image = read_image if read_image else self._read_image_from_fs

    def _read_image_from_fs(self, file_name: str) -> Any:
        if os.path.isfile(file_name):
            return cv2.imread(file_name)

    def run(self) -> None:
        while not self._stop_requested:
            frame_info = self._frame_queue.get()
            if self._stop_requested:
                break

            if frame_info.event_info.notification_sent:
                self.log.info(f"Notification already sent for event: {frame_info.event_info}, skipping processing of frame: {frame_info}")
                continue

            frame_info.image = self._read_image(frame_info.image_path)
            if frame_info.image is None:
                self.log.error(f"Could not read frame image, skipping frame {frame_info}")

            for action in (self._preprocessor.preprocess,
                           self._detect,
                           self._filter_detections,
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

                if event_info.first_detection_time is None:
                    event_info.first_detection_time = time.monotonic()
                self._notification_queue.put(event_info)
