import logging
import time
from queue import Queue
from threading import Thread
from typing import Callable, Any, List, Iterable

from events_processor import config
from events_processor.detector import CoralDetector
from events_processor.models import FrameInfo, EventInfo, Rect, ZoneInfo
from events_processor.notifications import MailNotificationSender, NotificationWorker, DetectionNotifier
from events_processor.processor import FrameProcessorWorker
from events_processor.reader import FrameReaderWorker, FrameReader


class MainController:
    FRAME_PROCESSING_THREADS = config['threading'].getint('frame_processing_threads')
    THREAD_WATCHDOG_DELAY = config['threading'].getint('thread_watchdog_delay')

    log = logging.getLogger("events_processor.EventController")

    def __init__(self,
                 event_ids: Iterable[str] = None,
                 detect: Callable[[FrameInfo], None] = None,
                 send_notification: Callable[[EventInfo, str, str], bool] = None,
                 frame_reader: FrameReader = None,
                 read_image: Callable[[str], Any] = None,
                 retrieve_alarm_stats: Callable[[str, str], Rect] = None,
                 retrieve_zones: Callable[[], Iterable[ZoneInfo]] = None,
                 sleep: Callable[[float], None] = time.sleep):
        frame_queue: Queue[FrameInfo] = Queue()
        notification_queue: Queue[EventInfo] = Queue()
        self._threads: List[Thread] = []

        send_notification = send_notification if send_notification else MailNotificationSender().send_notification
        self._threads.append(NotificationWorker(notify=DetectionNotifier(send_notification).notify,
                                                notification_queue=notification_queue))
        detect = self._determine_detect(detect)
        for a in range(self.FRAME_PROCESSING_THREADS):
            processor_worker = FrameProcessorWorker(frame_queue=frame_queue,
                                                    detect=detect,
                                                    notification_queue=notification_queue,
                                                    read_image=read_image,
                                                    retrieve_alarm_stats=retrieve_alarm_stats,
                                                    retrieve_zones=retrieve_zones)
            self._threads.append(processor_worker)

        self._threads.append(FrameReaderWorker(frame_queue=frame_queue,
                                               event_ids=event_ids,
                                               skip_mailed=not event_ids,
                                               frame_reader=frame_reader,
                                               sleep=sleep))

    def _determine_detect(self, detect: Callable[[FrameInfo], None] = None) -> Callable[[FrameInfo], None]:
        self._detector = None
        if not detect:
            self._detector = CoralDetector()
            detect = self._detector.detect
        return detect

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
        return self._detector is not None and self._detector.get_pending_processing_seconds() > 60

    def _any_thread_is_dead(self) -> bool:
        return any(not t.is_alive() for t in self._threads)
