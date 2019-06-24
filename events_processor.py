import datetime
import json
import logging.config
import os
import re
import smtplib
import time
from argparse import ArgumentParser
from configparser import ConfigParser, ExtendedInterpolation
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import cv2
import requests
from PIL import Image
from cachetools import TTLCache
from edgetpu.detection.engine import DetectionEngine

logging.config.fileConfig('events_processor.logging.conf')

# TODO: prozycki: list:
# - remove intersecting boxes of smaller priority (? - when almost whole rectangle is contained within another rectangle)
# - send events api POST and update event .mailed to true when mailed, and
#       when fetching events (possibly) skip mailed events - unless multiple mails per event are foreseen
# - consider async io reimplementation

config = ConfigParser(interpolation=ExtendedInterpolation())
config.read('events_processor.ini')

EVENTS_WINDOW_SECONDS = int(config['timings']['events_window_seconds'])
CACHE_SECONDS_BUFFER = int(config['timings']['cache_seconds_buffer'])


class FrameInfo:
    def __init__(self, event, frame, file_name):
        self.event_json = event
        self.frame_json = frame
        self.file_name = file_name
        self.detections = None
        self.image = None


class EventInfo:
    def __init__(self):
        self.frame_score = 0
        self.frame_info = None
        self.event_json = None
        self.first_detection_time = None
        self.last_notified = None
        self.processing_complete = False
        self.frame_read_interrupted = False


class RotatingPreprocessor:
    def __init__(self):
        self._config_parse_rotations()

    def _config_parse_rotations(self):
        self._rotations = {}
        for (key, value) in config['rotating_preprocessor'].items():
            match = re.match(r'rotate(\d+)', key)
            if match:
                self._rotations[match.group(1)] = value

    def preprocess(self, frame_info):
        monitor_id = frame_info.event_json['MonitorId']
        rotation = int(self._rotations.get(monitor_id, '0'))
        if rotation != 0:
            frame_info.image = self.rotate_image(frame_info.image, rotation)

    @staticmethod
    def rotate_image(mat, angle):
        """
        Rotates an image (angle in degrees) and expands image to avoid cropping
        """

        h, w = mat.shape[:2]  # image shape has 3 dimensions
        image_center = (w / 2, h / 2)  # getRotationMatrix2D needs coordinates in reverse order (w, h) compared to shape

        rotation_mat = cv2.getRotationMatrix2D(image_center, angle, 1.)

        # rotation calculates the cos and sin, taking absolutes of those.
        abs_cos = abs(rotation_mat[0, 0])
        abs_sin = abs(rotation_mat[0, 1])

        # find the new w and h bounds
        bound_w = int(h * abs_sin + w * abs_cos)
        bound_h = int(h * abs_cos + w * abs_sin)

        # subtract old image center (bringing image back to origo) and adding the new image center coordinates
        rotation_mat[0, 2] += bound_w / 2 - image_center[0]
        rotation_mat[1, 2] += bound_h / 2 - image_center[1]

        # rotate image with the new bounds and translated rotation matrix
        rotated_mat = cv2.warpAffine(mat, rotation_mat, (bound_w, bound_h), flags=cv2.INTER_LINEAR)
        return rotated_mat


class FrameReader:
    EVENT_LIST_URL = config['zm']['event_list_url']
    EVENT_DETAILS_URL = config['zm']['event_details_url']
    FRAME_FILE_NAME = config['zm']['frame_jpg_path']

    logger = logging.getLogger("events_processor.FrameReader")

    def _get_past_events_json(self, page):
        events_fetch_from = datetime.datetime.now() - datetime.timedelta(seconds=EVENTS_WINDOW_SECONDS)

        query = self.EVENT_LIST_URL.format(startTime=str(events_fetch_from),
                                           page=page)
        query = query.replace(' ', '%20')

        response = self._get_resource(query)
        if response:
            return json.loads(response.content)

    def get_event_details_json(self, event_id):
        query = self.EVENT_DETAILS_URL.format(eventId=event_id)
        response = self._get_resource(query)
        if response:
            data = json.loads(response.content)['event']
            return (data['Event'], data['Frame'])
        return (None, None)

    def events_iter(self):
        page = 0
        page_count = 1

        while page < page_count:
            event_json = self._get_past_events_json(page=page + 1)
            if not event_json:
                break

            page_count = event_json['pagination']['pageCount']
            page = event_json['pagination']['page']
            yield from (e['Event'] for e in event_json['events'])

    def events_by_id_iter(self, event_ids):
        for event_id in event_ids:
            (event_json, frames_json) = self.get_event_details_json(event_id)
            if not event_json:
                continue
            yield event_json

    def frames_iter(self, event_ids):
        for event_id in event_ids:
            (event_json, frames_json) = self.get_event_details_json(event_id)
            if not event_json:
                continue

            for frame_json in frames_json:
                frame_id = frame_json['FrameId']

                file_name = self._get_frame_file_name(event_id, event_json, frame_id)
                if os.path.isfile(file_name):
                    yield FrameInfo(event_json, frame_json, file_name)
                else:
                    self.logger.error("File {} does not exist, skipping frame".format(file_name))

    def _get_frame_file_name(self, event_id, event_json, frame_id):
        file_name = self.FRAME_FILE_NAME.format(
            monitorId=event_json['MonitorId'],
            startDay=event_json['StartTime'][:10],
            eventId=event_id,
            frameId=frame_id
        )
        return file_name

    def _get_resource(self, url):
        try:
            response = requests.get(url)
            if response.status_code == 200:
                return response
        except requests.exceptions.RequestException as e:
            pass
        self.logger.error("Could not retrieve resource: " + url)


class CoralDetector:
    MODEL_FILE = config['coral']['model_file']
    DETECTION_THRESHOLD = float(config['coral']['detection_threshold'])

    logger = logging.getLogger("events_processor.CoralDetector")

    def __init__(self):
        self._engine = DetectionEngine(self.MODEL_FILE)

    def detect(self, frame_info):
        format_dict = dict(frame_info.event_json)
        format_dict.update(frame_info.frame_json)
        self.logger.info(
            "Processing frame - (monitorId: {MonitorId}, eventId: {EventId}, frameId: {FrameId})".format(**format_dict))

        pil_img = Image.fromarray(frame_info.image)
        detections = self._engine.DetectWithImage(pil_img,
                                                  threshold=self.DETECTION_THRESHOLD,
                                                  keep_aspect_ratio=True,
                                                  relative_coord=False, top_k=10)
        frame_info.detections = detections


class DetectionFilter:
    LABEL_FILE = config['coral']['label_file']
    OBJECT_LABELS = config['coral']['object_labels'].split(',')
    MAX_BOX_AREA_PERCENTAGE = float(config['coral']['max_box_area_percentage'])

    logger = logging.getLogger('events_processor.DetectionFilter')

    def __init__(self):
        self._labels = self._read_labels()

    def _read_labels(self):
        with open(self.LABEL_FILE, 'r', encoding="utf-8") as f:
            lines = f.readlines()
        ret = {}
        for line in lines:
            pair = line.strip().split(maxsplit=1)
            ret[int(pair[0])] = pair[1].strip()
        return ret

    def filter_detections(self, frame_info):
        result = []
        for detection in frame_info.detections:
            box_area_percentage = self._detection_area(detection) / self._frame_area(frame_info) * 100
            if self._labels[detection.label_id] in self.OBJECT_LABELS:
                if box_area_percentage <= self.MAX_BOX_AREA_PERCENTAGE:
                    result.append(detection)
                else:
                    self.logger.debug("Detection discarded, exceeds area: {} > {}%".format(
                        box_area_percentage, self.MAX_BOX_AREA_PERCENTAGE))

        frame_info.detections = result

    def _detection_area(self, detection):
        (x1, y1, x2, y2) = detection.bounding_box.flatten().tolist()
        area = (x2 - x1) * (y2 - y1)
        return area

    def _frame_area(self, frame_info):
        (height, width) = frame_info.image.shape[:2]
        frame_area = width * height
        return frame_area


class DetectionRenderer:
    logger = logging.getLogger('events_processor.DetectionRenderer')

    def annotate_image(self, frame_info):
        image = frame_info.image
        detections = frame_info.detections
        for (i, detection) in enumerate(detections):
            box = tuple(int(x) for x in detection.bounding_box.flatten().tolist())
            point1 = tuple(box[:2])
            point2 = tuple(box[2:])
            cv2.rectangle(image, point1, point2, (255, 0, 0), 1)

            area_percents = self._box_area(point1, point2) / self._box_area((0, 0), frame_info.image.shape[:2])
            detection_info = {
                'Index': i,
                'ScorePercents': 100 * detection.score,
                'AreaPercents': 100 * area_percents
            }
            text = '{ScorePercents:.0f}%'.format(**detection_info)
            self.logger.debug(
                'Detection properties: (index: {Index}, score: {ScorePercents:.0f}%, area: {AreaPercents:.2f}%)'.format(
                    **detection_info))

            self._draw_text(text, box, image)
        return image

    def _draw_text(self, text, box, image):
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.5
        font_thickness = 1
        text_size = cv2.getTextSize(text, font, font_scale, font_thickness)[0]
        y_offset = text_size[1]
        cv2.rectangle(
            image, (box[0], box[1]), (box[0] + text_size[0], box[1] + text_size[1] + 1), (0, 0, 0), cv2.FILLED)
        cv2.putText(image, text, (box[0], box[1] + y_offset), font, font_scale, (0, 255, 0), font_thickness)

    def _box_area(self, point1, point2):
        return abs(point2[0] - point1[0]) * abs(point2[1] - point1[1])


def get_frame_score(frame_info):
    return max([p.score for p in frame_info.detections])


class FSNotificationSender:
    logger = logging.getLogger('events_processor.FSNotificationSender')

    def send_notification(self, event_info, subject, message):
        frame_info = event_info.frame_info
        cv2.imwrite("mailed_{EventId}_{FrameId}.jpg".format(**frame_info.frame_json), frame_info.image)
        self.logger.info("Notification subject: {}".format(subject))
        self.logger.info("Notification message: {}".format(message))
        return True


class MailNotificationSender:
    HOST = config['mail']['host']
    PORT = config['mail']['port']
    USER = config['mail']['user']
    PASSWORD = config['mail']['password']
    TO_ADDR = config['mail']['to_addr']
    FROM_ADDR = config['mail']['from_addr']
    TIMEOUT = float(config['mail']['timeout'])

    logger = logging.getLogger('events_processor.MailNotificationSender')

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
            return True
        except OSError as e:
            self.logger.error("Error encountered when sending mail notification: " + str(e))


class MailDetectionNotifier:
    SUBJECT = config['mail']['subject']
    MESSAGE = config['mail']['message']

    logger = logging.getLogger('events_processor.MailDetectionNotifier')

    def __init__(self, notification_sender):
        self._notification_sender = notification_sender

    def notify(self, event_info):
        format_dict = dict(event_info.frame_info.event_json)
        format_dict.update(event_info.frame_info.frame_json)
        format_dict['Score'] = 100 * event_info.frame_score

        subject = self.SUBJECT.format(**format_dict)
        message = self.MESSAGE.format(**format_dict)

        return self._notification_sender(event_info, subject, message)


class EventController:
    EVENT_LOOP_SECONDS = int(config['timings']['event_loop_seconds'])
    NOTIFICATION_DELAY_SECONDS = int(config['timings']['notification_delay_seconds'])

    logger = logging.getLogger("events_processor.EventController")

    def __init__(self,
                 event_ids=None,
                 image_preprocess=RotatingPreprocessor().preprocess,
                 detect=CoralDetector().detect,
                 filter_detections=DetectionFilter().filter_detections,
                 calculate_score=get_frame_score,
                 annotate_image=DetectionRenderer().annotate_image,
                 send_notification=MailNotificationSender().send_notification):
        self._image_preprocess = image_preprocess
        self._detect = detect
        self._filter_detections = filter_detections
        self._annotate_image = annotate_image
        self._notify = MailDetectionNotifier(send_notification).notify
        self._calculate_score = calculate_score

        self._events_cache = TTLCache(maxsize=1000_0000, ttl=EVENTS_WINDOW_SECONDS + CACHE_SECONDS_BUFFER)
        self._frames_cache = TTLCache(maxsize=1000_0000, ttl=EVENTS_WINDOW_SECONDS + CACHE_SECONDS_BUFFER)

        self._frame_reader = FrameReader()
        if event_ids:
            self._events_iter = lambda: self._frame_reader.events_by_id_iter(event_ids)
        else:
            self._events_iter = self._frame_reader.events_iter

    def run(self):
        while True:
            start = time.time()

            self.logger.info("Fetching event list")
            self._collect_events(start)
            self._process_events()

            wait_time = self.EVENT_LOOP_SECONDS - (time.time() - start)
            if wait_time > 0:
                self.logger.info("Waiting {}".format(wait_time))
                time.sleep(wait_time)

    def _collect_events(self, start):
        for event_json in self._events_iter():
            event_id = event_json['Id']
            event_info = self._events_cache.setdefault(event_id, EventInfo())
            event_info.event_json = event_json

            if event_info.processing_complete:
                continue

            self.logger.info("Reading event (monitorId: {MonitorId}, eventId: {Id})".format(**event_json))

            for frame_info in self._frame_reader.frames_iter(event_ids=(event_id,)):
                if frame_info.frame_json['Type'] != 'Alarm':
                    continue

                key = '{EventId}_{FrameId}'.format(**frame_info.frame_json)
                if key in self._frames_cache:
                    continue
                self._frames_cache[key] = 1

                frame_info.image = cv2.imread(frame_info.file_name)
                if self._image_preprocess:
                    self._image_preprocess(frame_info)

                self._detect(frame_info)
                self._filter_detections(frame_info)
                self._record_event_frame(event_info, frame_info)

                if (time.time() - start > self.EVENT_LOOP_SECONDS):
                    event_info.frame_read_interrupted = True
                    return

    def _record_event_frame(self, event_info, frame_info=None):
        if len(frame_info.detections) > 0:
            score = self._calculate_score(frame_info)
            if score > event_info.frame_score:
                event_info.frame_info = frame_info
                event_info.frame_score = score

                if event_info.first_detection_time is None:
                    event_info.first_detection_time = datetime.datetime.now()

    def _process_events(self):
        for (event_id, event_info) in self._events_cache.items():
            if event_info.processing_complete:
                continue

            all_frames_were_read = (event_info.event_json['EndTime'] and
                                    not event_info.frame_read_interrupted)

            if event_info.first_detection_time is not None:

                now = datetime.datetime.now()
                notification_delay_seconds = datetime.timedelta(seconds=self.NOTIFICATION_DELAY_SECONDS)
                notification_delay_elapsed = now - notification_delay_seconds > event_info.first_detection_time

                if event_info.last_notified is None:
                    if (notification_delay_elapsed or all_frames_were_read):
                        self._annotate_image(event_info.frame_info)
                        notification_succeeded = self._notify(event_info)
                        if notification_succeeded:
                            event_info.last_notified = now
                            event_info.processing_complete = True
                            event_info.frame_info = None
                            self.logger.info(
                                "Sent notification for event (monitorId: {MonitorId}, eventId: {Id})".format(
                                    **event_info.event_json))
                    else:
                        self.logger.info(
                            "Waiting to sent notification for event (monitorId: {MonitorId}, eventId: {Id})".format(
                                **event_info.event_json))
            elif all_frames_were_read:
                event_info.processing_complete = True
                self.logger.info(
                    "All frames were read for event (monitorId: {MonitorId}, eventId: {Id}) - no detections".format(
                        **event_info.event_json))

            event_info.frame_read_interrupted = None


def main():
    argparser = ArgumentParser()
    argparser.add_argument("--fs-notifier", help="write notification images to disk instead of mailing them",
                           action="store_true")
    argparser.add_argument("--read-events",
                           help="analyze specific events instead of fetching recent ones. Specify comma separated list of event ids")
    args = argparser.parse_args()

    event_controller_args = {}
    if args.fs_notifier:
        event_controller_args['send_notification'] = FSNotificationSender().send_notification
    if args.read_events:
        event_controller_args['event_ids'] = args.read_events.split(',')

    EventController(**event_controller_args).run()


if __name__ == '__main__':
    main()
