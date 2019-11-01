import logging
import time
from queue import Queue

from events_processor import config
from events_processor.detector import CoralDetector
from events_processor.notifications import MailNotificationSender, NotificationWorker, DetectionNotifier
from events_processor.processor import FrameProcessorWorker
from events_processor.reader import FrameReaderWorker


class MainController:
    FRAME_PROCESSING_THREADS = config['threading'].getint('frame_processing_threads')
    THREAD_WATCHDOG_DELAY = config['threading'].getint('thread_watchdog_delay')

    log = logging.getLogger("events_processor.EventController")

    def __init__(self,
                 event_ids=None,
                 detect=None,
                 send_notification=None,
                 frame_reader=None,
                 read_image=None,
                 retrieve_alarm_stats=None,
                 retrieve_zones=None,
                 sleep=time.sleep):
        frame_queue = Queue()
        notification_queue = Queue()
        self._threads = []

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

    def _determine_detect(self, detect):
        self._detector = None
        if not detect:
            self._detector = CoralDetector()
            detect = self._detector.detect
        return detect

    def start(self, watchdog=True):
        for thread in self._threads:
            thread.daemon = True
            thread.start()

        if watchdog:
            self._do_watchdog()

    def stop(self):
        for thread in self._threads:
            thread.stop()

    def _do_watchdog(self):
        while True:
            if self._any_thread_is_dead():
                self.log.error("One of threads has died, terminating")
                break

            if self._detector_is_stuck():
                self.log.error("Pending processing is stuck, terminating")
                break

            time.sleep(self.THREAD_WATCHDOG_DELAY)

    def _detector_is_stuck(self):
        return self._detector and self._detector.get_pending_processing_seconds() > 60

    def _any_thread_is_dead(self):
        return any(not t.is_alive() for t in self._threads)
