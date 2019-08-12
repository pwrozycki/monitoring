import json
import logging.config
import os
import re
import smtplib
import time
from argparse import ArgumentParser
from configparser import ConfigParser, ExtendedInterpolation
from datetime import datetime, timedelta
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from queue import Queue
from threading import Thread, Lock, Condition

import cv2
import requests
from PIL import Image
from cachetools import TTLCache
from shapely import geometry

logging.config.fileConfig('events_processor.logging.conf')

# TODO: prozycki: list:
# - remove intersecting boxes of smaller priority (? - when almost whole rectangle is contained within another rectangle)
# - send events api POST and update event .mailed to true when mailed, and
#       when fetching events (possibly) skip mailed events - unless multiple mails per event are foreseen

config = ConfigParser(interpolation=ExtendedInterpolation())
config.read('events_processor.ini')

EVENTS_WINDOW_SECONDS = config['timings'].getint('events_window_seconds')
CACHE_SECONDS_BUFFER = config['timings'].getint('cache_seconds_buffer')


class FrameInfo:
    def __init__(self, frame, image):
        self.frame_json = frame
        self.detections = None
        self.image = image
        self.event_info = None

    def __str__(self):
        log_dict = dict(self.event_info.event_json)
        log_dict.update(self.frame_json)
        return "(monitorId: {MonitorId}, eventId: {EventId}, frameId: {FrameId})".format(**log_dict)


class EventInfo:
    def __init__(self):
        self.event_json = None
        self.frame_info = None
        self.first_detection_time = None
        self.frame_score = 0
        self.planned_notification = None
        self.notification_sent = False
        self.all_frames_were_read = False
        self.lock = Lock()

    def __str__(self):
        return "(monitorId: {MonitorId}, eventId: {Id})".format(**self.event_json)


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
        monitor_id = frame_info.event_info.event_json['MonitorId']
        rotation = int(self._rotations.get(monitor_id, '0'))
        if rotation != 0:
            frame_info.image = self.rotate_and_expand_image(frame_info.image, rotation)

    @staticmethod
    def rotate_and_expand_image(mat, angle):
        h, w = mat.shape[:2]
        image_center = (w / 2, h / 2)
        rotation_mat = cv2.getRotationMatrix2D(image_center, angle, 1.)

        abs_cos = abs(rotation_mat[0, 0])
        abs_sin = abs(rotation_mat[0, 1])

        bound_w = int(h * abs_sin + w * abs_cos)
        bound_h = int(h * abs_cos + w * abs_sin)

        rotation_mat[0, 2] += bound_w / 2 - image_center[0]
        rotation_mat[1, 2] += bound_h / 2 - image_center[1]

        rotated_mat = cv2.warpAffine(mat, rotation_mat, (bound_w, bound_h), flags=cv2.INTER_CUBIC)
        return rotated_mat


class FrameReader:
    EVENT_LIST_URL = config['zm']['event_list_url']
    EVENT_DETAILS_URL = config['zm']['event_details_url']
    FRAME_FILE_NAME = config['zm']['frame_jpg_path']

    log = logging.getLogger("events_processor.FrameReader")

    def __init__(self, get_resource=None, read_image=None):
        self._get_resource = get_resource if get_resource else self._get_resource_by_request
        self._read_image = read_image if read_image else self._read_image_from_fs

    def _get_past_events_json(self, page):
        events_fetch_from = datetime.now() - timedelta(seconds=EVENTS_WINDOW_SECONDS)

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
                image = self._read_image(file_name)

                if image is not None:
                    frame_info = FrameInfo(frame_json, image)
                    yield frame_info

    def _read_image_from_fs(self, file_name):
        if os.path.isfile(file_name):
            return cv2.imread(file_name)
        else:
            self.log.error(f"File {file_name} does not exist, skipping frame")

    def _get_frame_file_name(self, event_id, event_json, frame_id):
        file_name = self.FRAME_FILE_NAME.format(
            monitorId=event_json['MonitorId'],
            startDay=event_json['StartTime'][:10],
            eventId=event_id,
            frameId=frame_id
        )
        return file_name

    def _get_resource_by_request(self, url):
        try:
            response = requests.get(url)
            if response.status_code == 200:
                return response
        except requests.exceptions.RequestException as e:
            pass
        self.log.error(f"Could not retrieve resource: {url}")


class CoralDetector:
    MODEL_FILE = config['coral']['model_file']
    MIN_SCORE = float(config['coral']['min_score'])

    log = logging.getLogger("events_processor.CoralDetector")

    def __init__(self):
        from edgetpu.detection.engine import DetectionEngine
        self._engine = DetectionEngine(self.MODEL_FILE)
        self._engine_lock = Lock()

    def detect(self, frame_info):
        pil_img = Image.fromarray(frame_info.image)
        with self._engine_lock:
            detections = self._engine.DetectWithImage(pil_img,
                                                      threshold=self.MIN_SCORE,
                                                      keep_aspect_ratio=True,
                                                      relative_coord=False, top_k=10)
        frame_info.detections = detections


class DetectionFilter:
    LABEL_FILE = config['detection_filter']['label_file']
    OBJECT_LABELS = config['detection_filter']['object_labels'].split(',')
    MAX_BOX_AREA_PERCENTAGE = float(config['detection_filter']['max_box_area_percentage'])

    log = logging.getLogger('events_processor.DetectionFilter')

    def __init__(self):
        self._labels = self._read_labels()
        self._config_parse()

    def _config_parse(self):
        self._excluded_points = {}
        self._excluded_polygons = {}
        self._min_score = {}
        for (key, value) in config['detection_filter'].items():
            m = re.match(r'min_score(\d+)', key)
            if m:
                monitor_id = m.group(1)
                self._min_score[monitor_id] = float(value)

            m = re.match(r'excluded_points(\d+)', key)
            if m:
                monitor_id = m.group(1)
                self._excluded_points[monitor_id] = [
                    geometry.Point(*map(int, m.groups())) for m in re.finditer('(\d+),(\d+)', value)]

            m = re.match(r'excluded_polygons(\d+)', key)
            if m:
                monitor_id = m.group(1)
                self._excluded_polygons[monitor_id] = [
                    geometry.Polygon(self.group(x.group(0))) for x in re.finditer('((?:\d+,\d+,?)+)', value)]

    def group(self, lst):
        i = iter(int(x) for x in lst.split(','))
        for e in i:
            yield (e, next(i))

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
            if self._labels[detection.label_id] in self.OBJECT_LABELS:
                box = tuple(int(x) for x in detection.bounding_box.flatten().tolist())

                monitor_id = frame_info.event_info.event_json['MonitorId']
                if detection.score < self._min_score.get(monitor_id, 0):
                    continue

                if self._detection_contains_excluded_point(box, frame_info):
                    continue

                if self._detection_intersects_excluded_polygon(box, frame_info):
                    continue

                if self._detection_area_exceeded(box, frame_info):
                    continue

                result.append(detection)

        self.log.debug(f"Frame {frame_info} has {len(result)} accepted detections")
        frame_info.detections = result

    def _detection_area_exceeded(self, box, frame_info):
        box_area_percentage = self._detection_area(box) / self._frame_area(frame_info) * 100
        if box_area_percentage > self.MAX_BOX_AREA_PERCENTAGE:
            self.log.debug(
                f"Detection discarded frame {frame_info}, {box} exceeds area: {box_area_percentage} > {self.MAX_BOX_AREA_PERCENTAGE}%")
            return True
        return False

    def _detection_intersects_excluded_polygon(self, box, frame_info):
        monitor_id = frame_info.event_info.event_json['MonitorId']
        detection_box = geometry.box(*box)

        excluded_polygons = self._excluded_polygons.get(monitor_id, [])
        if excluded_polygons:
            polygons = tuple(filter(detection_box.intersects, excluded_polygons))
            if polygons:
                self.log.debug(f"Detection discarded frame {frame_info}, {box} intersects one of excluded polygons")
                return True

        return False

    def _detection_contains_excluded_point(self, box, frame_info):
        monitor_id = frame_info.event_info.event_json['MonitorId']
        detection_box = geometry.box(*box)

        excluded_points = self._excluded_points.get(monitor_id, [])
        if excluded_points:
            points = tuple(filter(detection_box.contains, excluded_points))
            if points:
                self.log.debug(f"Detection discarded frame {frame_info}, {box} contains one of excluded points")
                return True
        return False

    def _detection_area(self, box):
        (x1, y1, x2, y2) = box
        area = (x2 - x1) * (y2 - y1)
        return area

    def _frame_area(self, frame_info):
        (height, width) = frame_info.image.shape[:2]
        frame_area = width * height
        return frame_area


class DetectionRenderer:
    log = logging.getLogger('events_processor.DetectionRenderer')

    def annotate_image(self, frame_info):
        image = frame_info.image
        detections = frame_info.detections
        for (i, detection) in enumerate(detections):
            box = tuple(int(x) for x in detection.bounding_box.flatten().tolist())
            point1 = tuple(box[:2])
            point2 = tuple(box[2:])
            cv2.rectangle(image, point1, point2, (255, 0, 0), 1)

            area_percents = 100 * self._box_area(point1, point2) / self._box_area((0, 0), frame_info.image.shape[:2])
            score_percents = 100 * detection.score

            self.log.debug(
                f'Rendering detection: (index: {i}, score: {score_percents:.0f}%, area: {area_percents:.1f}%), box: {box}')

            self._draw_text(f'{score_percents:.0f}%', box, image)
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
    if len(frame_info.detections) > 0:
        return max([p.score for p in frame_info.detections])
    else:
        return 0


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


class MailNotificationSender:
    HOST = config['mail']['host']
    PORT = config['mail']['port']
    USER = config['mail']['user']
    PASSWORD = config['mail']['password']
    TO_ADDR = config['mail']['to_addr']
    FROM_ADDR = config['mail']['from_addr']
    TIMEOUT = float(config['mail']['timeout'])
    EVENT_DETAILS_URL = config['zm']['event_details_url']

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

            return self.mark_event_as_mailed(event_info)
        except Exception as e:
            self.log.error(f"Error encountered when sending mail notification: {e}")

    def mark_event_as_mailed(self, event_info):
        url = self.EVENT_DETAILS_URL.format(eventId=event_info.event_json['Id'])
        mark_as_mailed_json = {'Event': {'Emailed': '1'}}
        response = requests.post(url, json=mark_as_mailed_json)
        response_json = json.loads(response.content)
        return 200 == response.status_code and 'Saved' == response_json.get('message', '')


class DetectionNotifier:
    SUBJECT = config['mail']['subject']
    MESSAGE = config['mail']['message']

    log = logging.getLogger('events_processor.DetectionNotifier')

    def __init__(self, notification_sender):
        self._notification_sender = notification_sender

    def notify(self, event_info):
        mail_dict = dict(event_info.event_json)
        mail_dict.update(event_info.frame_info.frame_json)
        mail_dict['Score'] = 100 * event_info.frame_score

        subject = self.SUBJECT.format(**mail_dict)
        message = self.MESSAGE.format(**mail_dict)

        return self._notification_sender(event_info, subject, message)


class FrameReaderWorker(Thread):
    EVENT_LOOP_SECONDS = config['timings'].getint('event_loop_seconds')

    log = logging.getLogger("events_processor.FrameReaderWorker")

    def __init__(self, frame_queue, event_ids=None, frame_reader=None, reshedule_notification=None):
        super().__init__()
        self._frame_queue = frame_queue
        self._events_cache = TTLCache(maxsize=10000000, ttl=EVENTS_WINDOW_SECONDS + CACHE_SECONDS_BUFFER)
        self._frames_cache = TTLCache(maxsize=10000000, ttl=EVENTS_WINDOW_SECONDS + CACHE_SECONDS_BUFFER)

        self._reshedule_notification = reshedule_notification
        self._frame_reader = frame_reader if frame_reader else FrameReader()
        if event_ids:
            self._events_iter = lambda: self._frame_reader.events_by_id_iter(event_ids)
        else:
            self._events_iter = self._frame_reader.events_iter

    def run(self):
        while True:
            self._collect_events()
            time.sleep(self.EVENT_LOOP_SECONDS)

    def _collect_events(self):
        self.log.info("Fetching event list")
        for event_json in self._events_iter():
            event_id = event_json['Id']
            event_info = self._events_cache.setdefault(event_id, EventInfo())

            with event_info.lock:
                event_info.event_json = event_json
                if event_info.all_frames_were_read or event_info.notification_sent:
                    continue
                if not event_info.all_frames_were_read and event_info.event_json['EndTime'] is not None:
                    event_info.all_frames_were_read = True
                    if event_info.planned_notification and not event_info.notification_sent:
                        self._reshedule_notification(event_info)

            self.log.info(f"Reading event {event_info}")

            for frame_info in self._frame_reader.frames_iter(event_ids=(event_id,)):
                if frame_info.frame_json['Type'] != 'Alarm':
                    continue

                key = '{EventId}_{FrameId}'.format(**frame_info.frame_json)
                if key in self._frames_cache:
                    continue
                self._frames_cache[key] = 1

                frame_info.event_info = event_info
                self._frame_queue.put(frame_info)


class FrameProcessorWorker(Thread):
    log = logging.getLogger("events_processor.FrameProcessorWorker")

    def __init__(self, frame_queue, detect, register_notification, preprocess_image=None,
                 filter_detections=None, calculate_score=get_frame_score):
        super().__init__()
        self._frame_queue = frame_queue
        self._preprocess_image = preprocess_image if preprocess_image else RotatingPreprocessor().preprocess
        self._detect = detect
        self._filter_detections = filter_detections if filter_detections else DetectionFilter().filter_detections
        self._calculate_score = calculate_score
        self._register_notification = register_notification

    def run(self):
        while True:
            frame_info = self._frame_queue.get()

            if frame_info.event_info.notification_sent:
                self.log.info(
                    f"Notification already sent for event: {frame_info.event_info}, skipping processing of frame: {frame_info}")
                continue

            for action in (self._preprocess_image, self._detect, self._filter_detections, self._record_event_frame):
                if action:
                    action(frame_info)

    def _record_event_frame(self, frame_info=None):
        event_info = frame_info.event_info

        score = self._calculate_score(frame_info)

        with event_info.lock:
            if score > event_info.frame_score:
                event_info.frame_info = frame_info
                event_info.frame_score = score

                if event_info.first_detection_time is None:
                    event_info.first_detection_time = time.monotonic()
                self._register_notification(event_info)


class NotificationWorker(Thread):
    NOTIFICATION_DELAY_SECONDS = config['timings'].getint('notification_delay_seconds')

    log = logging.getLogger("events_processor.NotificationWorker")

    def __init__(self, notify, annotate_image=None):
        super().__init__()
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

    def run(self):
        while True:
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
                time.sleep(5)

    def _get_closest_notification_event(self) -> EventInfo:
        event_info = None
        for notification in self._notifications:
            if event_info is None or event_info.planned_notification < event_info.planned_notification:
                event_info = notification
        return event_info


class MainController:
    FRAME_PROCESSING_THREADS = config['threading'].getint('frame_processing_threads')
    THREAD_WATCHDOG_DELAY = config['threading'].getint('thread_watchdog_delay')

    log = logging.getLogger("events_processor.EventController")

    def __init__(self,
                 event_ids=None,
                 detect=None,
                 send_notification=None,
                 frame_reader=None):
        self._frame_queue = Queue()

        send_notification = send_notification if send_notification else MailNotificationSender().send_notification
        self._notification_worker = NotificationWorker(notify=(DetectionNotifier(send_notification).notify))
        detect = detect if detect else CoralDetector().detect

        self._frame_processor_workers = []
        for a in range(self.FRAME_PROCESSING_THREADS):
            processor_worker = FrameProcessorWorker(self._frame_queue,
                                                    detect=detect,
                                                    register_notification=self._notification_worker.register_notification)
            self._frame_processor_workers.append(processor_worker)

        self._frame_reader_worker = FrameReaderWorker(self._frame_queue,
                                                      event_ids=event_ids,
                                                      frame_reader=frame_reader,
                                                      reshedule_notification=self._notification_worker.reshedule_notification)

    def run(self):
        threads = self._frame_processor_workers + [self._frame_reader_worker, self._notification_worker]
        for thread in threads:
            thread.daemon = True
            thread.start()

        self._exit_when_any_thread_terminates(threads)

    def _exit_when_any_thread_terminates(self, threads):
        while all(t.is_alive() for t in threads):
            time.sleep(self.THREAD_WATCHDOG_DELAY)


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

    MainController(**event_controller_args).run()


if __name__ == '__main__':
    main()
