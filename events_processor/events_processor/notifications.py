import json
import logging
import re
import smtplib
import time
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from threading import Thread, Condition

import cv2
import requests

from events_processor import config
from events_processor.models import EventInfo
from events_processor.renderer import DetectionRenderer


class MailNotificationSender:
    HOST = config['mail']['host']
    PORT = config['mail']['port']
    USER = config['mail']['user']
    PASSWORD = config['mail']['password']
    TO_ADDR = config['mail']['to_addr']
    FROM_ADDR = config['mail']['from_addr']
    TIMEOUT = float(config['mail']['timeout'])

    log = logging.getLogger('events_processor.MailNotificationSender')

    def send_notification(self, event_info, subject, message):
        msg = MIMEMultipart()
        msg['Subject'] = subject

        text = MIMEText(message)
        msg.attach(text)

        bytes = cv2.imencode(".jpg", event_info.frame_info.image)[1].tostring()
        image = MIMEImage(bytes, name="notification.jpg")
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


class FSNotificationSender:
    log = logging.getLogger('events_processor.FSNotificationSender')

    def __init__(self):
        super().__init__()

    def send_notification(self, event_info, subject, message):
        frame_info = event_info.frame_info
        cv2.imwrite("mailed_{EventId}_{FrameId}.jpg".format(**frame_info.frame_json), frame_info.image)
        self.log.info(f"Notification subject: {subject}")
        self.log.info(f"Notification message: {message}")
        return True


class EventUpdater:
    EVENT_DETAILS_URL = config['zm']['event_details_url']

    log = logging.getLogger('events_processor.EventUpdater')

    @classmethod
    def update_event(cls, event_info, **update_spec):
        try:
            url = cls.EVENT_DETAILS_URL.format(eventId=event_info.event_json['Id'])
            mark_as_mailed_json = {'Event': update_spec}
            response = requests.post(url, json=mark_as_mailed_json)
            response_json = json.loads(response.content)
            return 200 == response.status_code and 'Saved' == response_json.get('message', '')
        except Exception as e:
            pass

        cls.log.error(f"Error encountered during event update: {e}")


class DetectionNotifier:
    SUBJECT = config['mail']['subject']
    MESSAGE = re.sub(r'\n\|', '\n', config['mail']['message'])

    log = logging.getLogger('events_processor.DetectionNotifier')

    def __init__(self, notification_sender):
        self._notification_sender = notification_sender

    def notify(self, event_info):
        mail_dict = {f"Event-{x}": y for (x, y) in event_info.event_json.items()}
        mail_dict.update({f"Frame-{x}": y for (x, y) in event_info.frame_info.frame_json.items()})
        mail_dict['Detection-Score'] = 100 * event_info.frame_score

        subject = self.SUBJECT.format(**mail_dict)
        message = self.MESSAGE.format(**mail_dict)

        return self._notification_sender(event_info, subject, message)


class NotificationWorker(Thread):
    NOTIFICATION_DELAY_SECONDS = config['timings'].getint('notification_delay_seconds')

    log = logging.getLogger("events_processor.NotificationWorker")

    def __init__(self, notify, annotate_image=None, sleep=time.sleep):
        super().__init__()
        self._stop = False
        self._sleep = sleep

        self._notify = notify
        self._annotate_image = annotate_image if annotate_image else DetectionRenderer().annotate_image

        self._notifications = set()
        self._condition = Condition()

    def register_notification(self, event_info: EventInfo):
        if event_info.notification_sent:
            return

        self.log.info(f"Registering event notification {event_info.frame_info}, score: {event_info.frame_score}")
        self._calculate_notification_time(event_info)
        with self._condition:
            self._notifications.add(event_info)
            self._condition.notify_all()

    def reshedule_notification(self, event_info):
        with self._condition:
            self._calculate_notification_time(event_info)
            self._condition.notify_all()

    def _calculate_notification_time(self, event_info):
        if not event_info.all_frames_were_read:
            delay = event_info.first_detection_time + self.NOTIFICATION_DELAY_SECONDS - time.monotonic()
            notification_delay = max(delay, 0)
        else:
            notification_delay = 0
        event_info.planned_notification = time.monotonic() + notification_delay

    def run(self, a=None):
        while not self._stop:
            with self._condition:
                event_info = self._get_closest_notification_event()
                if event_info:
                    timeout = event_info.planned_notification - time.monotonic()
                else:
                    timeout = None

                if timeout is None or timeout > 0:
                    self._condition.wait(timeout)
                    continue

            self._annotate_image(event_info.frame_info)
            notification_succeeded = self._notify(event_info)
            if notification_succeeded:
                with event_info.lock:
                    event_info.notification_sent = True
                    event_info.frame_info = None

                with self._condition:
                    self._notifications.remove(event_info)
            else:
                self.log.error("Notification error, throttling")
                self._sleep(5)

        self.log.info("Terminating")

    def stop(self):
        self._stop = True
        with self._condition:
            self._condition.notify_all()

    def _get_closest_notification_event(self) -> EventInfo:
        event_info = None
        for notification in self._notifications:
            if event_info is None or event_info.planned_notification < event_info.planned_notification:
                event_info = notification
        return event_info
