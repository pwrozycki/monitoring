import logging
import re

from shapely import geometry

from events_processor import config


class DetectionFilter:
    LABEL_FILE = config['detection_filter']['label_file']
    OBJECT_LABELS = config['detection_filter']['object_labels'].split(',')

    log = logging.getLogger('events_processor.DetectionFilter')

    def __init__(self):
        self._labels = self._read_labels()
        self._config_parse()

    def _config_parse(self):
        self._excluded_points = {}
        self._excluded_polygons = {}
        self._min_score = {}
        self._min_box_area_percentage = {}
        self._max_box_area_percentage = {}

        for (key, value) in config['detection_filter'].items():
            self._set_config_dict(key, value, 'min_score', self._min_score, float)
            self._set_config_dict(key, value, 'min_box_area_percentage', self._min_box_area_percentage, float)
            self._set_config_dict(key, value, 'max_box_area_percentage', self._max_box_area_percentage, float)
            self._set_config_dict(key, value, 'excluded_points', self._excluded_points,
                                  lambda x: [geometry.Point(*map(int, m.groups()))
                                             for m in re.finditer('(\d+),(\d+)', x)])
            self._set_config_dict(key, value, 'excluded_polygons', self._excluded_polygons,
                                  lambda x: [geometry.Polygon(self.group(x.group(0)))
                                             for x in re.finditer('((?:\d+,\d+,?)+)', x)])

    def _set_config_dict(self, key, value, config_key, dictionary, transform):
        m = re.match(config_key + r'(\d+)', key)
        if m:
            monitor_id = m.group(1)
            dictionary[monitor_id] = transform(value)

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

                if self._detection_area_not_in_range(box, frame_info):
                    continue

                result.append(detection)

        self.log.debug(f"Frame {frame_info} has {len(result)} accepted detections")
        frame_info.detections = result

    def _detection_area_not_in_range(self, box, frame_info):
        monitor_id = frame_info.event_info.event_json['MonitorId']
        box_area_percentage = self._detection_area(box) / self._frame_area(frame_info) * 100
        min_box_area_percentage = self._min_box_area_percentage.get(monitor_id, 0)
        max_box_area_percentage = self._max_box_area_percentage.get(monitor_id, 100)
        if not min_box_area_percentage <= box_area_percentage <= max_box_area_percentage:
            self.log.debug(
                f"Detection discarded frame {frame_info}, {box} has percentage {box_area_percentage:.2f}% out of range"
                f" <{min_box_area_percentage:.2f}%, {max_box_area_percentage:.2f}%>")
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
