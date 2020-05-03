import logging
import re
from typing import Dict, Iterable

from injector import inject
from shapely import geometry

from events_processor.configtools import get_config, ConfigProvider, extract_config, coords_to_points, \
    coords_to_polygons
from events_processor.interfaces import ZoneReader, AlarmBoxReader
from events_processor.models import Point, FrameInfo, Detection, Rect, ZonePolygon, Polygon, ZoneInfo
from events_processor.preprocessor import RotatingPreprocessor

INTERSECTION_DISCARDED_THRESHOLD = 1E-6


class DetectionFilter:
    log = logging.getLogger('events_processor.DetectionFilter')

    @inject
    def __init__(self,
                 preprocessor: RotatingPreprocessor,
                 alarm_box_reader: AlarmBoxReader,
                 zone_reader: ZoneReader,
                 config: ConfigProvider):
        self._config = config
        self._labels = self._read_labels()
        self._transform_coords = preprocessor.transform_coords
        self._alarm_box_reader = alarm_box_reader
        self._zone_reader = zone_reader
        self._config = config
        self._config_parse()

    def _config_parse(self) -> None:
        self._excluded_zone_polygons = {}
        for zone in self._zone_reader.read():
            polys = coords_to_polygons(zone.coords.replace(' ', ','))
            zone_polys = [ZonePolygon(zone, poly) for poly in polys]
            self._excluded_zone_polygons.setdefault(zone.monitor_id, []).extend(zone_polys)

    def _read_labels(self) -> Dict[int, str]:
        with open(self._config.label_file, 'r', encoding="utf-8") as f:
            lines = f.readlines()
        ret = {}
        for line in lines:
            pair = line.strip().split(maxsplit=1)
            ret[int(pair[0])] = pair[1].strip()
        return ret

    def filter_detections(self, frame_info: FrameInfo):
        result = []
        for detection in frame_info.detections:
            if not self._labels[detection.label_id] in self._config.object_labels:
                continue

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
        if detection.score >= get_config(self._config.movement_indifferent_min_score, monitor_id, 0):
            return False

        alarm_box = self._alarm_box_reader.read(frame_info.frame_json['EventId'],
                                                frame_info.frame_json['FrameId'])
        if alarm_box:
            (detection_box, movement_box, intersection_box) = self._calculate_boxes(alarm_box, detection, frame_info)

            if intersection_box.area > INTERSECTION_DISCARDED_THRESHOLD:
                movement_ratio = movement_box.area / intersection_box.area
                details = f"movement_ratio: {movement_ratio:.2f}, detection_box: {detection_box.area:.2f}, " \
                          f"movement_box: {movement_box.area:.2f}, intersection_box: {intersection_box.area:.2f}"

                if detection.score >= get_config(self._config.coarse_movement_min_score, monitor_id, 0):
                    self.log.debug(f"Detection accepted for frame {frame_info} - coarse movement - {details}")
                    return False

                if (movement_ratio < get_config(self._config.max_movement_to_intersection_ratio, monitor_id, 0)
                        and detection.score >= get_config(self._config.precise_movement_min_score, monitor_id, 0)):
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
        min_box_area_percentage = get_config(self._config.min_box_area_percentage, monitor_id, 0)
        max_box_area_percentage = get_config(self._config.max_box_area_percentage, monitor_id, 100)
        if not min_box_area_percentage <= box_area_percentage <= max_box_area_percentage:
            self.log.debug(
                f"Detection discarded frame {frame_info}, {box} has percentage {box_area_percentage:.2f}% out of range"
                f" <{min_box_area_percentage:.2f}%, {max_box_area_percentage:.2f}%>")
            return True
        return False

    def _detection_intersects_excluded_polygon(self, box: Rect, frame_info: FrameInfo) -> bool:
        monitor_id = frame_info.event_info.event_json['MonitorId']
        detection_box = geometry.box(*box.box_tuple)

        excluded_polygons = self._config.excluded_polygons.get(monitor_id, [])
        shapely_polygons = [poly.shapely_poly for poly in excluded_polygons]
        if tuple(filter(detection_box.intersects, shapely_polygons)):
            self.log.debug(f"Detection discarded frame {frame_info}, {box} intersects one of excluded polygons")
            return True

        return False

    def _detection_intersects_excluded_zone_polygon(self, box: Rect, frame_info: FrameInfo) -> bool:
        monitor_id = frame_info.event_info.event_json['MonitorId']
        detection_box = geometry.box(*box.box_tuple)

        zone_polygons = self._excluded_zone_polygons.get(monitor_id, [])
        for zone_poly in zone_polygons:
            polygon = self._transformed_poly(zone_poly.zone, zone_poly.polygon)
            if detection_box.intersects(polygon.shapely_poly):
                self.log.debug(
                    f"Detection discarded frame {frame_info}, {box} intersects excluded polygon: {zone_poly.zone.name}")
                return True

        return False

    def _transformed_poly(self, zone: ZoneInfo, poly: Polygon):
        return Polygon(self._transform_coords(zone.monitor_id, zone.width, zone.height, pt) for pt in poly.points)

    def _detection_contains_excluded_point(self, box: Rect, frame_info: FrameInfo) -> bool:
        monitor_id = frame_info.event_info.event_json['MonitorId']
        detection_box = geometry.box(*box.box_tuple)

        excluded_points = self._config.excluded_points.get(monitor_id, [])
        geom_points = [p.shapely_point for p in excluded_points]
        if tuple(filter(detection_box.contains, geom_points)):
            self.log.debug(f"Detection discarded frame {frame_info}, {box} contains one of excluded points")
            return True
        return False

    def _frame_area(self, frame_info: FrameInfo) -> int:
        (height, width) = (int(frame_info.event_info.event_json['Height']),
                           int(frame_info.event_info.event_json['Width']))
        frame_area = width * height
        return frame_area
