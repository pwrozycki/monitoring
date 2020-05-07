import logging
from typing import Dict

from injector import inject
from shapely import geometry

from events_processor.configtools import get_config, ConfigProvider, coords_to_polygons
from events_processor.interfaces import ZoneReader
from events_processor.models import FrameInfo, Detection, Rect, ZonePolygon, Polygon, ZoneInfo
from events_processor.preprocessor import RotatingPreprocessor

INTERSECTION_DISCARDED_THRESHOLD = 1E-6


class DetectionFilter:
    log = logging.getLogger('events_processor.DetectionFilter')

    @inject
    def __init__(self,
                 preprocessor: RotatingPreprocessor,
                 zone_reader: ZoneReader,
                 config: ConfigProvider):
        self._config = config
        self._labels = self._read_labels()
        self._transform_coords = preprocessor.transform_coords
        self._zone_reader = zone_reader
        self._config = config
        self._config_parse()

    def _config_parse(self) -> None:
        self._excluded_zone_polygons = {}
        for zone in self._zone_reader.read(self._config.excluded_zone_prefix):
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
        for detection in frame_info.detections:
            self._frame_score_insufficient(detection, frame_info)
            self._label_incorrect(detection)
            self._detection_contains_excluded_point(detection, frame_info)
            self._detection_intersects_excluded_polygon(detection, frame_info)
            self._detection_intersects_excluded_zone_polygon(detection, frame_info)
            self._detection_area_not_in_range(detection, frame_info)

        self.log.debug(frame_info.detection_str)

    def _label_incorrect(self, detection: Detection):
        detection.label = self._labels[detection.label_id]
        if detection.label not in self._config.object_labels:
            detection.discard_reasons.append(f"wrong label {detection.label}")

    def _frame_score_insufficient(self, detection: Detection, frame_info: FrameInfo):
        monitor_id = frame_info.event_info.monitor_id
        details = ""

        alarm_rect = frame_info.alarm_box
        if alarm_rect:
            (detection_box, alarm_box, intersection_box) = self._calculate_boxes(alarm_rect, detection)
            if intersection_box.area > INTERSECTION_DISCARDED_THRESHOLD:
                detection.alarm_diff = self.relative_difference_percents(alarm_box.area, intersection_box.area)
                detection.detection_diff = self.relative_difference_percents(detection_box.area, intersection_box.area)

                if (detection.alarm_diff < get_config(self._config.max_alarm_to_intersect_diff, monitor_id, 50) and
                        detection.detection_diff < get_config(self._config.max_detect_to_intersect_diff, monitor_id,
                                                              50) and
                        detection.score >= get_config(self._config.precise_movement_min_score, monitor_id, 1)):
                    detection.threshold_acceptance_type = "precise"
                    return

                if detection.score >= get_config(self._config.coarse_movement_min_score, monitor_id, 1):
                    detection.threshold_acceptance_type = "coarse"
                    return

        if detection.score >= get_config(self._config.movement_indifferent_min_score, monitor_id, 0):
            detection.threshold_acceptance_type = "indifferent"
            return

        detection.discard_reasons.append("score insufficient")

    def relative_difference_percents(self, a, b):
        return int(abs(a - b) / max(a, b) * 100)

    def _calculate_boxes(self, alarm_box: Rect, detection: Detection):
        movement_poly = geometry.Polygon([pt.tuple for pt in alarm_box.points])
        detection_box = geometry.box(*detection.rect.box_tuple)
        intersection_box = movement_poly.intersection(detection_box)

        return detection_box, movement_poly, intersection_box

    def _detection_area_not_in_range(self, detection: Detection, frame_info: FrameInfo):
        monitor_id = frame_info.event_info.monitor_id
        detection.detection_area_percent = detection.rect.area / self._frame_area(frame_info) * 100
        min_box_area_percentage = get_config(self._config.min_box_area_percentage, monitor_id, 0)
        max_box_area_percentage = get_config(self._config.max_box_area_percentage, monitor_id, 100)
        if not min_box_area_percentage <= detection.detection_area_percent <= max_box_area_percentage:
            detection.discard_reasons.append(f"detection box not in range")

    def _detection_intersects_excluded_polygon(self, detection: Detection, frame_info: FrameInfo):
        monitor_id = frame_info.event_info.monitor_id
        detection_box = geometry.box(*detection.rect.box_tuple)

        excluded_polygons = self._config.excluded_polygons.get(monitor_id, [])
        shapely_polygons = [poly.shapely_poly for poly in excluded_polygons]
        if tuple(filter(detection_box.intersects, shapely_polygons)):
            detection.discard_reasons.append(f"intersects excluded polygon")

    def _detection_intersects_excluded_zone_polygon(self, detection: Detection, frame_info: FrameInfo):
        monitor_id = frame_info.event_info.monitor_id
        detection_box = geometry.box(*detection.rect.box_tuple)

        zone_polygons = self._excluded_zone_polygons.get(monitor_id, [])
        for zone_poly in zone_polygons:
            polygon = self._transformed_poly(zone_poly.zone, zone_poly.polygon)
            if detection_box.intersects(polygon.shapely_poly):
                detection.discard_reasons.append(f"intersects excluded polygon: {zone_poly.zone.name}")
                return

    def _transformed_poly(self, zone: ZoneInfo, poly: Polygon):
        return Polygon(self._transform_coords(zone.monitor_id, zone.width, zone.height, pt) for pt in poly.points)

    def _detection_contains_excluded_point(self, detection: Detection, frame_info: FrameInfo):
        monitor_id = frame_info.event_info.monitor_id
        detection_box = geometry.box(*detection.rect.box_tuple)

        excluded_points = self._config.excluded_points.get(monitor_id, [])
        geom_points = [p.shapely_point for p in excluded_points]
        if tuple(filter(detection_box.contains, geom_points)):
            detection.discard_reasons.append(f"{detection.rect} contains one of excluded points")

    def _frame_area(self, frame_info: FrameInfo) -> int:
        (height, width) = (frame_info.event_info.width,
                           frame_info.event_info.height)
        frame_area = width * height
        return frame_area
