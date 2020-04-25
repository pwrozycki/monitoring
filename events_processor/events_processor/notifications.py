import json
import logging
import re
import smtplib
import time
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from queue import Empty, Queue
from threading import Thread
from typing import Optional, Callable, Set

import cv2
import requests

from events_processor import config
from events_processor.interfaces import NotificationSender
from events_processor.models import EventInfo
from events_processor.renderer import DetectionRenderer


class MailNotificationSender(NotificationSender):
    HOST = config['mail']['host']
    PORT = int(config['mail']['port'])
    USER = config['mail']['user']
    PASSWORD = config['mail']['password']
    TO_ADDR = config['mail']['to_addr']
    FROM_ADDR = config['mail']['from_addr']
    TIMEOUT = float(config['mail']['timeout'])

    log = logging.getLogger('events_processor.MailNotificationSender')

    def send(self, event_info: EventInfo, subject: str, message: str) -> bool:
        msg = MIMEMultipart()
        msg['Subject'] = subject

        text = MIMEText(message)
        msg.attach(text)

        img_data = cv2.imencode(".jpg", event_info.frame_info.image)[1].tostring()
        image = MIMEImage(img_data, name="notification.jpg")
        msg.attach(image)

        try:
            s = smtplib.SMTP(self.HOST, self.PORT, timeout=self.TIMEOUT)
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(self.USER, self.PASSWORD)
            s.sendmail(self.FROM_ADDR, self.TO_ADDR, msg.as_string())
            s.quit()

            return EventUpdater.update_event(event_info, Emailed=1)
        except Exception as e:
            self.log.error(f"Error encountered when sending mail notification: {e}")
        return False


class FSNotificationSender:
    log = logging.getLogger('events_processor.FSNotificationSender')

    def __init__(self):
        super().__init__()

    def send_notification(self, event_info: EventInfo, subject: str, message: str) -> bool:
        frame_info = event_info.frame_info
        cv2.imwrite("mailed_{EventId}_{FrameId}.jpg".format(**frame_info.frame_json), frame_info.image)
        self.log.info(f"Notification subject: {subject}")
        self.log.info(f"Notification message: {message}")
        return True


class EventUpdater:
    EVENT_DETAILS_URL = config['zm']['event_details_url']

    log = logging.getLogger('events_processor.EventUpdater')

    @classmethod
    def update_event(cls, event_info: EventInfo, **update_spec) -> bool:
        try:
            url = cls.EVENT_DETAILS_URL.format(eventId=event_info.event_json['Id'])
            mark_as_mailed_json = {'Event': update_spec}
            response = requests.post(url, json=mark_as_mailed_json)
            response_json = json.loads(response.content)
            return 200 == response.status_code and 'Saved' == response_json.get('message', '')
        except Exception as e:
            cls.log.error(f"Error encountered during event update: {e}")
            return False


class DetectionNotifier:
    SUBJECT = config['mail']['subject']
    MESSAGE = re.sub(r'\n\|', '\n', config['mail']['message'])

    log = logging.getLogger('events_processor.DetectionNotifier')

    def __init__(self, notification_sender: NotificationSender):
        self._notification_sender = notification_sender

    def notify(self, event_info: EventInfo) -> bool:
        mail_dict = {f"Event-{x}": y for (x, y) in event_info.event_json.items()}
        mail_dict.update({f"Frame-{x}": y for (x, y) in event_info.frame_info.frame_json.items()})
        mail_dict['Detection-Score'] = 100 * event_info.frame_score

        subject = self.SUBJECT.format(**mail_dict)
        message = self.MESSAGE.format(**mail_dict)

        return self._notification_sender.send(event_info, subject, message)


class NotificationWorker(Thread):
    NOTIFICATION_DELAY_SECONDS = config['timings'].getint('notification_delay_seconds')

    log = logging.getLogger("events_processor.NotificationWorker")

    def __init__(self, notify: Callable[[EventInfo], bool], notification_queue: 'Queue[EventInfo]', annotate_image=None,
                 sleep=time.sleep):
        super().__init__()
        self._stop_requested = False
        self._sleep = sleep

        self._notify = notify
        self._annotate_image = annotate_image if annotate_image else DetectionRenderer().annotate_image

        self._notification_queue = notification_queue
        self._notifications: Set[EventInfo] = set()

    def _set_notification_time(self, event_info: EventInfo) -> None:
        with event_info.lock:
            delay = event_info.first_detection_time + self.NOTIFICATION_DELAY_SECONDS - time.monotonic()
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
        if event_info.notification_sent:
            self._notifications.remove(event_info)
        else:
            self._annotate_image(event_info.frame_info)
            notification_succeeded = self._notify(event_info)
            if notification_succeeded:
                with event_info.lock:
                    event_info.notification_sent = True
                    event_info.frame_info = None

                self._notifications.remove(event_info)
            else:
                self.log.error("Notification error, throttling")
                self._sleep(5)

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
