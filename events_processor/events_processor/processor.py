import logging
import os
import time
from threading import Thread
from typing import Any

import cv2
from injector import inject

from events_processor.configtools import get_config, ConfigProvider
from events_processor.filters import DetectionFilter
from events_processor.interfaces import Detector, ImageReader
from events_processor.models import FrameInfo, FrameQueue, NotificationQueue, NotificationStatus, EventInfo
from events_processor.preprocessor import RotatingPreprocessor


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
                 image_reader: ImageReader,
                 detection_filter: DetectionFilter,
                 preprocessor: RotatingPreprocessor,
                 config: ConfigProvider):

        super().__init__()
        self._stop_requested = False

        self._frame_queue = frame_queue
        self._notification_queue = notification_queue
        self._detector = detector
        self._detection_filter = detection_filter
        self._preprocessor = preprocessor
        self._config = config

        self._image_reader = image_reader

    def _read_image_from_fs(self, file_name: str) -> Any:
        if os.path.isfile(file_name):
            return cv2.imread(file_name)

    def run(self) -> None:
        while not self._stop_requested:
            frame_info = self._frame_queue.get()
            if self._stop_requested:
                break

            if frame_info.event_info.notification_status.was_sending:
                self.log.info(f"Notification already sent for event: {frame_info.event_info}, "
                              f"skipping processing of frame: {frame_info}")
            else:
                frame_info.image = self._image_reader.read(frame_info.image_path)
                if frame_info.image is None:
                    self.log.error(f"Could not read frame image, skipping frame {frame_info}")

                for action in (self._preprocessor.preprocess,
                               self._detector.detect,
                               self._detection_filter.filter_detections,
                               self._calculate_frame_score,
                               self._record_event_frame):
                    if action:
                        action(frame_info)

            event_info = frame_info.event_info
            with event_info.lock:
                event_info.processed_frame_ids.add(frame_info.frame_id)
                if event_info.all_frames_were_read_and_processed_none_submitted():
                    event_info.release_resources()

        self.log.info(f"Terminating")

    def stop(self) -> None:
        self._stop_requested = True
        self._frame_queue.put(None)

    def _calculate_frame_score(self, frame_info: FrameInfo) -> None:
        accepted_detections = frame_info.accepted_detections
        frame_info.score = max([p.score for p in accepted_detections], default=0)

    def _record_event_frame(self, frame_info: FrameInfo) -> None:
        event_info = frame_info.event_info
        with event_info.lock:
            if frame_info.score > 0:
                if not event_info.notification_status.was_sending:
                    event_info.candidate_frames.append(frame_info)
                    event_info.release_less_scored_frames_images()

                    min_accepted = get_config(self._config.min_accepted_frames, event_info.monitor_id, 1)
                    n_accepted = len(event_info.candidate_frames)

                    if n_accepted >= min_accepted and not event_info.notification_status.was_submitted:
                        self._submit_notification(event_info)
            else:
                frame_info.image = None

    def _submit_notification(self, event_info: EventInfo):
        event_info.notification_submission_time = time.monotonic()
        event_info.notification_status = NotificationStatus.SUBMITTED
        self._notification_queue.put(event_info)
