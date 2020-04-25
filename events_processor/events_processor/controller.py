import logging
import time

from injector import inject, ProviderOf

from events_processor import config
from events_processor.detector import CoralDetector
from events_processor.interfaces import Detector, SystemTime
from events_processor.notifications import NotificationWorker
from events_processor.processor import FrameProcessorWorker
from events_processor.reader import FrameReaderWorker


class MainController:
    FRAME_PROCESSING_THREADS = config['threading'].getint('frame_processing_threads')
    THREAD_WATCHDOG_DELAY = config['threading'].getint('thread_watchdog_delay')

    log = logging.getLogger("events_processor.EventController")

    @inject
    def __init__(self,
                 detector: Detector,
                 frame_reader_worker: FrameReaderWorker,
                 notification_worker: NotificationWorker,
                 frame_processor_worker_provider: ProviderOf[FrameProcessorWorker]):
        self._detector = detector

        self._threads = [notification_worker, frame_reader_worker]
        self._threads += [frame_processor_worker_provider.get() for a in range(self.FRAME_PROCESSING_THREADS)]

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


class DefaultSystemTime(SystemTime):
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)
