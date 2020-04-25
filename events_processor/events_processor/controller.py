import logging
import time
from queue import Queue
from threading import Thread
from typing import List, Iterable

from injector import inject, noninjectable

from events_processor import config
from events_processor.detector import CoralDetector
from events_processor.interfaces import Detector, ImageReader, NotificationSender, SystemTime, ZoneReader, \
    AlarmBoxReader
from events_processor.models import FrameInfo, EventInfo
from events_processor.notifications import NotificationWorker, DetectionNotifier
from events_processor.processor import FrameProcessorWorker
from events_processor.reader import FrameReaderWorker, FrameReader


class MainController:
    FRAME_PROCESSING_THREADS = config['threading'].getint('frame_processing_threads')
    THREAD_WATCHDOG_DELAY = config['threading'].getint('thread_watchdog_delay')

    log = logging.getLogger("events_processor.EventController")

    @inject
    @noninjectable('event_ids')
    def __init__(self,
                 detector: Detector,
                 notification_sender: NotificationSender,
                 frame_reader: FrameReader,
                 image_reader: ImageReader,
                 system_time: SystemTime,
                 alarm_box_reader: AlarmBoxReader,
                 zone_reader: ZoneReader,
                 event_ids: Iterable[str] = None):
        frame_queue: Queue[FrameInfo] = Queue()
        notification_queue: Queue[EventInfo] = Queue()
        self._threads: List[Thread] = []

        self._threads.append(NotificationWorker(notify=DetectionNotifier(notification_sender).notify,
                                                notification_queue=notification_queue))
        self._detector = detector
        for a in range(self.FRAME_PROCESSING_THREADS):
            processor_worker = FrameProcessorWorker(frame_queue=frame_queue,
                                                    notification_queue=notification_queue,
                                                    detector=detector,
                                                    image_reader=image_reader,
                                                    alarm_box_reader=alarm_box_reader,
                                                    zone_reader=zone_reader)
            self._threads.append(processor_worker)

        self._threads.append(FrameReaderWorker(frame_queue=frame_queue,
                                               event_ids=event_ids,
                                               skip_mailed=not event_ids,
                                               frame_reader=frame_reader,
                                               system_time=system_time))

    def start(self, watchdog: bool = True) -> None:
        for thread in self._threads:
            thread.daemon = True
            thread.start()

        if watchdog:
            self._do_watchdog()

    def stop(self) -> None:
        for thread in self._threads:
            thread.stop()

    def _do_watchdog(self) -> None:
        while True:
            if self._any_thread_is_dead():
                self.log.error("One of threads has died, terminating")
                break

            if self._detector_is_stuck():
                self.log.error("Pending processing is stuck, terminating")
                break

            time.sleep(self.THREAD_WATCHDOG_DELAY)

    def _detector_is_stuck(self) -> bool:
        return isinstance(self._detector, CoralDetector) and self._detector.get_pending_processing_seconds() > 60

    def _any_thread_is_dead(self) -> bool:
        return any(not t.is_alive() for t in self._threads)
