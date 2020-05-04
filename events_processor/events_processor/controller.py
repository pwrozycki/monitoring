import logging
import time

from injector import inject, ProviderOf

from events_processor.configtools import ConfigProvider
from events_processor.interfaces import SystemTime, Engine
from events_processor.notifications import NotificationWorker
from events_processor.processor import FrameProcessorWorker
from events_processor.reader import FrameReaderWorker


class MainController:
    log = logging.getLogger("events_processor.EventController")

    @inject
    def __init__(self,
                 config: ConfigProvider,
                 engine: Engine,
                 frame_reader_worker: FrameReaderWorker,
                 notification_worker: NotificationWorker,
                 frame_processor_worker_provider: ProviderOf[FrameProcessorWorker],
                 ):
        self._config = config
        self._engine = engine
        self._threads = [notification_worker, frame_reader_worker]
        self._threads += [frame_processor_worker_provider.get() for _ in range(config.frame_processing_threads)]

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

            if self._engine_is_stuck():
                self.log.error("Coral engine is stuck, terminating")
                break

            time.sleep(self._config.thread_watchdog_delay)

    def _engine_is_stuck(self) -> bool:
        return self._engine and self._engine.get_pending_processing_seconds() > 60

    def _any_thread_is_dead(self) -> bool:
        return any(not t.is_alive() for t in self._threads)


class DefaultSystemTime(SystemTime):
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)
