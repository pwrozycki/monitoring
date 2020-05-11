import json
import logging
import smtplib
import time
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from queue import Empty
from threading import Thread
from typing import Optional, Set

import cv2
import requests
from injector import inject

from events_processor.configtools import ConfigProvider
from events_processor.interfaces import NotificationSender, SystemTime
from events_processor.models import EventInfo, NotificationQueue, NotificationStatus, FrameInfo
from events_processor.renderer import DetectionRenderer


class MailNotificationSender(NotificationSender):
    log = logging.getLogger('events_processor.MailNotificationSender')

    @inject
    def __init__(self,
                 config: ConfigProvider,
                 event_updater: 'EventUpdater'):
        self._config = config
        self._event_updater = event_updater

    def send(self, frame_info: FrameInfo, subject: str, message: str) -> bool:
        event_info = frame_info.event_info
        msg = MIMEMultipart()
        msg['Subject'] = subject

        text = MIMEText(message)
        msg.attach(text)

        img_data = cv2.imencode(".jpg", frame_info.image)[1].tostring()
        image = MIMEImage(img_data, name="notification.jpg")
        msg.attach(image)

        try:
            s = smtplib.SMTP(self._config.host, self._config.port, timeout=self._config.timeout)
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(self._config.user, self._config.password)
            s.sendmail(self._config.from_addr, self._config.to_addr, msg.as_string())
            s.quit()

            return self._event_updater.update_event(event_info, Emailed=1)
        except Exception as e:
            self.log.error(f"Error encountered when sending mail notification: {e}")
        return False


class FSNotificationSender(NotificationSender):
    log = logging.getLogger('events_processor.FSNotificationSender')

    def send(self, frame_info: FrameInfo, subject: str, message: str) -> bool:
        cv2.imwrite(f"mailed_{frame_info.event_id}_{frame_info.event_id}.jpg", frame_info.image)
        self.log.info(f"Notification subject: {subject}")
        self.log.info(f"Notification message: {message}")
        return True


class EventUpdater:
    log = logging.getLogger('events_processor.EventUpdater')

    @inject
    def __init__(self, config: ConfigProvider):
        self._config = config

    def update_event(self, event_info: EventInfo, **update_spec) -> bool:
        try:
            url = self._config.event_details_url.format(eventId=event_info.event_id)
            mark_as_mailed_json = {'Event': update_spec}
            response = requests.post(url, json=mark_as_mailed_json)
            response_json = json.loads(response.content)
            return 200 == response.status_code and 'Saved' == response_json.get('message', '')
        except Exception as e:
            self.log.error(f"Error encountered during event update: {e}")
            return False


class DetectionNotifier:
    log = logging.getLogger('events_processor.DetectionNotifier')

    @inject
    def __init__(self,
                 notification_sender: NotificationSender,
                 config: ConfigProvider):
        self._config = config
        self._notification_sender = notification_sender

    def notify(self, frame_info: FrameInfo) -> bool:
        event_info = frame_info.event_info
        mail_dict = {f"Event-{x}": y for (x, y) in event_info.event_json.items()}
        mail_dict.update({f"Frame-{x}": y for (x, y) in frame_info.frame_json.items()})
        mail_dict.update({f"Monitor-{x}": y for (x, y) in frame_info.monitor_json.items()})
        mail_dict.update({f"FrameInfo-{x}": y for (x, y) in obj_to_map(frame_info).items()})
        mail_dict['Detection-Score'] = 100 * frame_info.score

        subject = self._config.subject.format(**mail_dict)
        message = self._config.message.format(**mail_dict)

        return self._notification_sender.send(frame_info, subject, message)


def obj_to_map(obj, key_prefix=''):
    return {key_prefix + key: getattr(obj, key) for key in dir(obj) if not key.startswith('__')}


class NotificationWorker(Thread):
    log = logging.getLogger("events_processor.NotificationWorker")

    @inject
    def __init__(self,
                 notification_queue: NotificationQueue,
                 detection_notifier: DetectionNotifier,
                 detection_renderer: DetectionRenderer,
                 system_time: SystemTime,
                 config: ConfigProvider):
        super().__init__()
        self._config = config
        self._stop_requested = False
        self._system_time = system_time

        self._detection_notifier = detection_notifier
        self._detection_renderer = detection_renderer

        self._notification_queue = notification_queue
        self._notifications: Set[EventInfo] = set()

    def _set_notification_time(self, event_info: EventInfo) -> None:
        with event_info.lock:
            delay = event_info.notification_submission_time + self._config.notification_delay_seconds - time.monotonic()
            notification_delay = max(delay, 0)
            event_info.planned_notification = time.monotonic() + notification_delay

    def run(self, a=None) -> None:
        while not self._stop_requested:
            notification = self._get_closest_notification_event()
            seconds_to_notification = self._get_notification_remaining_secs(notification)

            if seconds_to_notification == 0:
                if notification:
                    self._send_notification(notification)
            else:
                try:
                    incoming_notification = self._notification_queue.get(timeout=seconds_to_notification)
                    if self._stop_requested:
                        break

                    self._set_notification_time(incoming_notification)
                    self._notifications.add(incoming_notification)
                except Empty:
                    pass

        self.log.info("Terminating")

    def _send_notification(self, event_info: EventInfo) -> None:
        with event_info.lock:
            notification_frame = max(event_info.candidate_frames, key=lambda frame: frame.score)

        self._detection_renderer.annotate_image(notification_frame)
        notification_succeeded = self._detection_notifier.notify(notification_frame)
        if notification_succeeded:
            event_info.notification_status = NotificationStatus.SENT
            event_info.candidate_frames.clear()
            self._notifications.remove(event_info)
        else:
            self.log.error("Notification error, throttling")
            self._system_time.sleep(5)

    def stop(self) -> None:
        self._stop_requested = True
        self._notification_queue.put(None)

    def _get_closest_notification_event(self) -> Optional[EventInfo]:
        if self._notifications:
            return min(self._notifications, key=lambda x: x.planned_notification)

        return None

    def _get_notification_remaining_secs(self, notification: Optional[EventInfo]) -> Optional[float]:
        if notification:
            return max(notification.planned_notification - time.monotonic(), 0)
        else:
            return None
