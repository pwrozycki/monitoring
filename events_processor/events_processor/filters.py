import logging
import re
from collections import namedtuple
from typing import Callable, Tuple, Dict, Iterable, Any, List

from shapely import geometry

from events_processor import config, dataaccess
from events_processor.configtools import set_config, get_config
from events_processor.interfaces import ZoneReader, AlarmBoxReader
from events_processor.models import Point, FrameInfo, Detection, Rect, ZoneInfo

INTERSECTION_DISCARDED_THRESHOLD = 1E-6

ZonePolygon = namedtuple('ZonePolygon', ['name', 'polygon'])


class DetectionFilter:
    LABEL_FILE = config['detection_filter']['label_file']
    OBJECT_LABELS = config['detection_filter']['object_labels'].split(',')

    log = logging.getLogger('events_processor.DetectionFilter')

    def __init__(self,
                 transform_coords: Callable[[str, int, int, Point], Point],
                 alarm_box_reader: AlarmBoxReader,
                 zone_reader: ZoneReader):
        self._labels = self._read_labels()
        self._transform_coords = transform_coords
        self._alarm_box_reader = alarm_box_reader
        self._zone_reader = zone_reader
        self._config_parse()

    def _config_parse(self) -> None:
        self._excluded_points: Dict[str, Any] = {}
        self._excluded_polygons: Dict[str, Any] = {}
        self._excluded_zone_polygons: Dict[str, List[ZonePolygon]] = {}
        self._movement_indifferent_min_score: Dict[str, float] = {}
        self._coarse_movement_min_score: Dict[str, float] = {}
        self._precise_movement_min_score: Dict[str, float] = {}
        self._max_movement_to_intersection_ratio: Dict[str, float] = {}
        self._min_box_area_percentage: Dict[str, float] = {}
        self._max_box_area_percentage: Dict[str, float] = {}

        for (key, value) in config['detection_filter'].items():
            set_config(key, value, 'movement_indifferent_min_score', self._movement_indifferent_min_score, float)
            set_config(key, value, 'coarse_movement_min_score', self._coarse_movement_min_score, float)
            set_config(key, value, 'precise_movement_min_score', self._precise_movement_min_score, float)
            set_config(key, value, 'max_movement_to_intersection_ratio', self._max_movement_to_intersection_ratio,
                       float)
            set_config(key, value, 'min_box_area_percentage', self._min_box_area_percentage, float)
            set_config(key, value, 'max_box_area_percentage', self._max_box_area_percentage, float)
            set_config(key, value, 'excluded_points', self._excluded_points, self._coords_to_points)
            set_config(key, value, 'excluded_polygons', self._excluded_polygons, self._coords_to_polygons)

        for zone in self._zone_reader.read():
            def transform(x: int, y: int) -> Point:
                return self._transform_coords(zone.monitor_id, zone.width, zone.height, Point(x, y))

            polygons = self._coords_to_polygons(zone.coords.replace(' ', ','), transform=transform)
            self._excluded_zone_polygons.setdefault(zone.monitor_id, []).append(ZonePolygon(zone.name, polygons[0]))

    def _coords_to_points(self, string):
        return [geometry.Point(*map(int, m.groups())) for m in re.finditer(r'(\d+),(\d+)', string)]

    def _coords_to_polygons(self, string, transform: Callable[[int, int], Point] = lambda x, y: Point(x, y)):
        polygon_pattern = rf'((?:\d+,\d+,?)+)'
        return [geometry.Polygon(list(self._transformed_points(m.group(0), transform)))
                for m in re.finditer(polygon_pattern, string)]

    def _transformed_points(self, lst, transform: Callable[[int, int], Point]) -> Iterable[Tuple[int, int]]:
        i = iter(int(x) for x in lst.split(','))
        for e in i:
            yield transform(e, next(i)).tuple

    def _read_labels(self) -> Dict[int, str]:
        with open(self.LABEL_FILE, 'r', encoding="utf-8") as f:
            lines = f.readlines()
        ret = {}
        for line in lines:
            pair = line.strip().split(maxsplit=1)
            ret[int(pair[0])] = pair[1].strip()
        return ret

    def filter_detections(self, frame_info: FrameInfo):
        result = []
        for detection in frame_info.detections:
            if self._labels[detection.label_id] in self.OBJECT_LABELS:
                if self._frame_score_insufficient(detection, frame_info):
                    continue

                if self._detection_contains_excluded_point(detection.rect, frame_info):
                    continue

                if self._detection_intersects_excluded_polygon(detection.rect, frame_info):
                    continue

                if self._detection_intersects_excluded_polygon(detection.rect, frame_info):
                    continue

                if self._detection_intersects_excluded_zone_polygon(detection.rect, frame_info):
                    continue

                if self._detection_area_not_in_range(detection.rect, frame_info):
                    continue

                result.append(detection)

        self.log.debug(f"Frame {frame_info} has {len(result)} accepted detections")
        frame_info.detections = result

    def _frame_score_insufficient(self, detection: Detection, frame_info: FrameInfo) -> bool:
        monitor_id = frame_info.event_info.event_json['MonitorId']
        if detection.score >= get_config(self._movement_indifferent_min_score, monitor_id, 0):
            return False

        alarm_box = self._alarm_box_reader.read(frame_info.frame_json['EventId'],
                                                frame_info.frame_json['FrameId'])
        if alarm_box:
            (detection_box, movement_box, intersection_box) = self._calculate_boxes(alarm_box, detection, frame_info)

            if intersection_box.area > INTERSECTION_DISCARDED_THRESHOLD:
                movement_ratio = movement_box.area / intersection_box.area
                details = f"movement_ratio: {movement_ratio:.2f}, detection_box: {detection_box.area:.2f}, " \
                          f"movement_box: {movement_box.area:.2f}, intersection_box: {intersection_box.area:.2f}"

                if detection.score >= get_config(self._coarse_movement_min_score, monitor_id, 0):
                    self.log.debug(f"Detection accepted for frame {frame_info} - coarse movement - {details}")
                    return False

                if (movement_ratio < get_config(self._max_movement_to_intersection_ratio, monitor_id, 0)
                        and detection.score >= get_config(self._precise_movement_min_score, monitor_id, 0)):
                    self.log.debug(f"Detection accepted for frame {frame_info} - precise movement - {details}")
                    return False

        return True

    def _calculate_boxes(self, alarm_box: Rect, detection: Detection, frame_info: FrameInfo):
        original_points = [alarm_box.top_left, alarm_box.top_right, alarm_box.bottom_right, alarm_box.bottom_left]

        w = int(frame_info.event_info.event_json['Width'])
        h = int(frame_info.event_info.event_json['Height'])
        monitor_id = frame_info.event_info.event_json['MonitorId']

        transformed_points = (self._transform_coords(monitor_id, w, h, pt).tuple for pt in original_points)

        movement_poly = geometry.Polygon(transformed_points)
        detection_box = geometry.box(*detection.rect.box_tuple)
        intersection_box = movement_poly.intersection(detection_box)

        return detection_box, movement_poly, intersection_box

    def _detection_area_not_in_range(self, box: Rect, frame_info: FrameInfo) -> bool:
        monitor_id = frame_info.event_info.event_json['MonitorId']
        box_area_percentage = box.area / self._frame_area(frame_info) * 100
        min_box_area_percentage = get_config(self._min_box_area_percentage, monitor_id, 0)
        max_box_area_percentage = get_config(self._max_box_area_percentage, monitor_id, 100)
        if not min_box_area_percentage <= box_area_percentage <= max_box_area_percentage:
            self.log.debug(
                f"Detection discarded frame {frame_info}, {box} has percentage {box_area_percentage:.2f}% out of range"
                f" <{min_box_area_percentage:.2f}%, {max_box_area_percentage:.2f}%>")
            return True
        return False

    def _detection_intersects_excluded_polygon(self, box: Rect, frame_info: FrameInfo) -> bool:
        monitor_id = frame_info.event_info.event_json['MonitorId']
        detection_box = geometry.box(*box.box_tuple)

        excluded_polygons = self._excluded_polygons.get(monitor_id)
        if excluded_polygons:
            polygons: Iterable[Any] = tuple(filter(detection_box.intersects, excluded_polygons))
            if polygons:
                self.log.debug(f"Detection discarded frame {frame_info}, {box} intersects one of excluded polygons")
                return True

        return False

    def _detection_intersects_excluded_zone_polygon(self, box: Rect, frame_info: FrameInfo) -> bool:
        monitor_id = frame_info.event_info.event_json['MonitorId']
        detection_box = geometry.box(*box.box_tuple)

        polygons = self._excluded_zone_polygons.get(monitor_id, [])
        if polygons:
            for poly in polygons:
                if detection_box.intersects(poly.polygon):
                    self.log.debug(
                        f"Detection discarded frame {frame_info}, {box} intersects excluded polygon: {poly.name}")
                    return True

        return False

    def _detection_contains_excluded_point(self, box: Rect, frame_info: FrameInfo) -> bool:
        monitor_id = frame_info.event_info.event_json['MonitorId']
        detection_box = geometry.box(*box.box_tuple)

        excluded_points = self._excluded_points.get(monitor_id, [])
        if excluded_points:
            points: Iterable[Any] = tuple(filter(detection_box.contains, excluded_points))
            if points:
                self.log.debug(f"Detection discarded frame {frame_info}, {box} contains one of excluded points")
                return True
        return False

    def _frame_area(self, frame_info: FrameInfo) -> int:
        (height, width) = (int(frame_info.event_info.event_json['Height']),
                           int(frame_info.event_info.event_json['Width']))
        frame_area = width * height
        return frame_area
