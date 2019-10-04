import logging
import re
from collections import namedtuple

from shapely import geometry

from events_processor import config, dataaccess

ZonePolygon = namedtuple('ZonePolygon', ['name', 'polygon'])


class DetectionFilter:
    LABEL_FILE = config['detection_filter']['label_file']
    OBJECT_LABELS = config['detection_filter']['object_labels'].split(',')

    log = logging.getLogger('events_processor.DetectionFilter')

    def __init__(self, transform_coords, retrieve_alarm_stats=None, retrieve_zones=None):
        self._labels = self._read_labels()
        self._transform_coords = transform_coords
        self._retrieve_alarm_stats = retrieve_alarm_stats if retrieve_alarm_stats else dataaccess.retrieve_alarm_stats
        self._retrieve_zones = retrieve_zones if retrieve_zones else dataaccess.retrieve_zones
        self._config_parse()

    def _config_parse(self):
        self._excluded_points = {}
        self._excluded_polygons = {}
        self._excluded_zone_polygons = {}
        self._movement_indifferent_min_score = {}
        self._coarse_movement_min_score = {}
        self._precise_movement_min_score = {}
        self._max_movement_to_intersection_ratio = {}
        self._min_box_area_percentage = {}
        self._max_box_area_percentage = {}

        for (key, value) in config['detection_filter'].items():
            self._set_config(key, value, 'movement_indifferent_min_score', self._movement_indifferent_min_score, float)
            self._set_config(key, value, 'coarse_movement_min_score', self._coarse_movement_min_score, float)
            self._set_config(key, value, 'precise_movement_min_score', self._precise_movement_min_score, float)
            self._set_config(key, value, 'max_movement_to_intersection_ratio', self._max_movement_to_intersection_ratio,
                             float)
            self._set_config(key, value, 'min_box_area_percentage', self._min_box_area_percentage, float)
            self._set_config(key, value, 'max_box_area_percentage', self._max_box_area_percentage, float)
            self._set_config(key, value, 'excluded_points', self._excluded_points, self._coords_to_points)
            self._set_config(key, value, 'excluded_polygons', self._excluded_polygons, self._coords_to_polygons)

        for (m_id, w, h, name, coords) in self._retrieve_zones():
            def transform(x, y):
                return self._transform_coords(m_id, w, h, (x, y))

            polygons = self._coords_to_polygons(coords.replace(' ', ','), transform=transform)
            self._excluded_zone_polygons.setdefault(m_id, []).append(ZonePolygon(name, polygons[0]))

    def _get_config(self, config_map, monitor_id, default):
        for key in (monitor_id, 'default'):
            if key in config_map:
                return config_map[key]
        return default

    def _set_config(self, key, value, config_key, dictionary, transform):
        m = re.match(config_key + r'(\d*)', key)
        if m:
            monitor_id = m.group(1)
            key = monitor_id if monitor_id else 'default'
            dictionary[key] = transform(value)

    def _coords_to_points(self, string):
        return [geometry.Point(*map(int, m.groups())) for m in re.finditer(r'(\d+),(\d+)', string)]

    def _coords_to_polygons(self, string, transform=lambda *x: x):
        polygon_pattern = rf'((?:\d+,\d+,?)+)'
        return [geometry.Polygon(self._transformed_points(m.group(0), transform))
                for m in re.finditer(polygon_pattern, string)]

    def _transformed_points(self, lst, transform):
        i = iter(int(x) for x in lst.split(','))
        for e in i:
            yield transform(e, next(i))

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
                if self._frame_score_insufficient(detection, frame_info):
                    continue

                box = tuple(int(x) for x in detection.bounding_box.flatten().tolist())
                if self._detection_contains_excluded_point(box, frame_info):
                    continue

                if self._detection_intersects_excluded_polygon(box, frame_info):
                    continue

                if self._detection_intersects_excluded_polygon(box, frame_info):
                    continue

                if self._detection_intersects_excluded_zone_polygon(box, frame_info):
                    continue

                if self._detection_area_not_in_range(box, frame_info):
                    continue

                result.append(detection)

        self.log.debug(f"Frame {frame_info} has {len(result)} accepted detections")
        frame_info.detections = result

    def _frame_score_insufficient(self, detection, frame_info):
        monitor_id = frame_info.event_info.event_json['MonitorId']
        if detection.score >= self._get_config(self._movement_indifferent_min_score, monitor_id, 0):
            return False

        alarm_box = self._retrieve_alarm_stats(frame_info.frame_json['EventId'],
                                               frame_info.frame_json['FrameId'])
        if alarm_box:
            (detection_box, movement_box, intersection_box) = self._calculate_boxes(alarm_box, detection, frame_info)

            if not intersection_box.is_empty:
                movement_ratio = movement_box.area / intersection_box.area
                details = f"movement_ratio: {movement_ratio:.2f}, detection_box: {detection_box.area:.2f}, " \
                          f"movement_box: {movement_box.area:.2f}, intersection_box: {intersection_box.area:.2f}"

                if detection.score >= self._get_config(self._coarse_movement_min_score, monitor_id, 0):
                    self.log.debug(f"Detection accepted for frame {frame_info} - coarse movement - {details}")
                    return False

                if (movement_ratio < self._get_config(self._max_movement_to_intersection_ratio, monitor_id, 0)
                        and detection.score >= self._get_config(self._precise_movement_min_score, monitor_id, 0)):
                    self.log.debug(f"Detection accepted for frame {frame_info} - precise movement - {details}")
                    return False

        return True

    def _calculate_boxes(self, alarm_box, detection, frame_info):
        (minX, minY, maxX, maxY) = alarm_box
        original_points = [(minX, minY), (maxX, minY), (maxX, maxY), (minX, maxY)]

        w = int(frame_info.event_info.event_json['Width'])
        h = int(frame_info.event_info.event_json['Height'])
        monitor_id = frame_info.event_info.event_json['MonitorId']

        transformed_points = (self._transform_coords(monitor_id, w, h, pt) for pt in original_points)

        movement_poly = geometry.Polygon(transformed_points)
        detection_box = geometry.box(*detection.bounding_box.flatten().tolist())
        intersection_box = movement_poly.intersection(detection_box)

        return detection_box, movement_poly, intersection_box

    def _detection_area_not_in_range(self, box, frame_info):
        monitor_id = frame_info.event_info.event_json['MonitorId']
        box_area_percentage = self._detection_area(box) / self._frame_area(frame_info) * 100
        min_box_area_percentage = self._get_config(self._min_box_area_percentage, monitor_id, 0)
        max_box_area_percentage = self._get_config(self._max_box_area_percentage, monitor_id, 100)
        if not min_box_area_percentage <= box_area_percentage <= max_box_area_percentage:
            self.log.debug(
                f"Detection discarded frame {frame_info}, {box} has percentage {box_area_percentage:.2f}% out of range"
                f" <{min_box_area_percentage:.2f}%, {max_box_area_percentage:.2f}%>")
            return True
        return False

    def _detection_intersects_excluded_polygon(self, box, frame_info):
        monitor_id = frame_info.event_info.event_json['MonitorId']
        detection_box = geometry.box(*box)

        excluded_polygons = self._excluded_polygons.get(monitor_id)
        if excluded_polygons:
            polygons = tuple(filter(detection_box.intersects, excluded_polygons))
            if polygons:
                self.log.debug(f"Detection discarded frame {frame_info}, {box} intersects one of excluded polygons")
                return True

        return False

    def _detection_intersects_excluded_zone_polygon(self, box, frame_info):
        monitor_id = frame_info.event_info.event_json['MonitorId']
        detection_box = geometry.box(*box)

        polygons = self._excluded_zone_polygons.get(monitor_id, [])
        if polygons:
            for poly in polygons:
                if detection_box.intersects(poly.polygon):
                    self.log.debug(
                        f"Detection discarded frame {frame_info}, {box} intersects excluded polygon: {poly.name}")
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
        area = abs((x2 - x1) * (y2 - y1))
        return area

    def _frame_area(self, frame_info):
        (height, width) = (int(frame_info.event_info.event_json['Height']),
                           int(frame_info.event_info.event_json['Width']))
        frame_area = width * height
        return frame_area
