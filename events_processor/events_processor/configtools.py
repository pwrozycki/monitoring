import logging
import re
from configparser import ConfigParser, ExtendedInterpolation
from typing import Dict, Callable, Any, Iterable

from injector import inject

from events_processor.interfaces import MonitorReader
from events_processor.models import Point, Polygon


def get_config(config_map: Dict,
               monitor_id: str,
               default: Any) -> Any:
    for key in (monitor_id, 'default'):
        if key in config_map:
            value = config_map[key]
            if value:
                return value
    return default


class ConfigProvider(ConfigParser):
    log = logging.getLogger("events_processor.ConfigProvider")

    @inject
    def __init__(self,
                 monitor_reader: MonitorReader):
        super(ConfigParser, self).__init__(interpolation=ExtendedInterpolation())
        self.optionxform = str
        self._monitor_id_for_name = {m.name: m.id for m in monitor_reader.read()}
        self._recognized_config_map = {}

        self.read('events_processor.ini')
        self.reread()

    def reread(self):
        self._recognized_config_map.clear()

        self.event_list_url = self._read_property('zm', 'event_list_url')
        self.event_details_url = self._read_property('zm', 'event_details_url')
        self.frame_jpg_path = self._read_property('zm', 'frame_jpg_path')

        self.notification_delay_seconds = self._read_property('timings', 'notification_delay_seconds', '0', int)
        self.events_window_seconds = self._read_property('timings', 'events_window_seconds', '600', int)
        self.event_loop_seconds = self._read_property('timings', 'event_loop_seconds', '5', int)
        self.frame_read_delay_seconds = self._read_property('timings', 'frame_read_delay_seconds', '5', int)
        self.cache_seconds_buffer = self._read_property('timings', 'cache_seconds_buffer', '120', int)

        self.rotations = self._read_map('rotating_preprocessor', 'rotate', int)

        self.detector_model_file = self._read_property('coral', 'model_file')
        self.min_score = self._read_property('coral', 'min_score', float)
        self.detection_chunks = self._read_map('coral', 'detection_chunks', extract_int_pair)

        self.excluded_zone_prefix = self._read_property('detection_filter', 'excluded_zone_prefix')
        self.object_labels = self._read_property('detection_filter', 'object_labels', 'person').split(',')
        self.label_file = self._read_property('detection_filter', 'label_file')
        self.coarse_movement_min_score = self._read_map('detection_filter', 'coarse_movement_min_score', float)
        self.precise_movement_min_score = self._read_map('detection_filter', 'precise_movement_min_score', float)
        self.max_alarm_to_intersect_diff = self._read_map('detection_filter', 'max_alarm_to_intersect_diff', float)
        self.max_detect_to_intersect_diff = self._read_map('detection_filter', 'max_detect_to_intersect_diff', float)
        self.min_box_area_percentage = self._read_map('detection_filter', 'min_box_area_percentage', float)
        self.max_box_area_percentage = self._read_map('detection_filter', 'max_box_area_percentage', float)
        self.excluded_points = self._read_map('detection_filter', 'excluded_points', coords_to_points)
        self.excluded_polygons = self._read_map('detection_filter', 'excluded_polygons', coords_to_polygons)
        self.movement_indifferent_min_score = \
            self._read_map('detection_filter', 'movement_indifferent_min_score', float)

        self.host = self._read_property('mail', 'host')
        self.port = self._read_property('mail', 'port', '587', int)
        self.user = self._read_property('mail', 'user')
        self.password = self._read_property('mail', 'password')
        self.to_addr = self._read_property('mail', 'to_addr')
        self.from_addr = self._read_property('mail', 'from_addr')
        self.timeout = self._read_property('mail', 'timeout', '10', float)
        self.subject = self._read_property('mail', 'subject')
        self.message = re.sub(r'\n\|', '\n', self._read_property('mail', 'message'))

        self.frame_processing_threads = self._read_property('threading', 'frame_processing_threads', '2', int)
        self.thread_watchdog_delay = self._read_property('threading', 'thread_watchdog_delay', '5', int)

        self.event_ids = [x for x in self._read_property('debug', 'event_ids', '').split(',') if x]
        self.debug_images = [x for x in self._read_property('debug', 'debug_images', '').split(',') if x]

        self._sanity_check_config()

    def _read_property(self, section, property, fallback=None, transform=lambda x: x):
        self._add_recognized_property(property, section)
        return transform(self[section].get(property, fallback=fallback))

    def _set_config(self,
                    key: str,
                    value: str,
                    config_key: str,
                    dictionary: Dict[str, Any],
                    transform: Callable[[str], Any]):
        monitor_id = ''
        m = re.match(fr'{config_key}(\d*)', key)
        if m:
            monitor_id = m.group(1)
            monitor_id = monitor_id or 'default'

        m = re.match(fr'{config_key}_(\w+)', key)
        if m:
            monitor_id = self._monitor_id_for_name[m.group(1)]

        if monitor_id:
            dictionary[monitor_id] = transform(value)
            return True
        return False

    def _read_map(self, section, config_key, transform):
        recognized_section = self._get_recognized_section(section)

        d = {}
        for (key, value) in self[section].items():
            matched = self._set_config(key, value, config_key, d, transform)
            if matched:
                recognized_section.add(key)

        return d

    def _sanity_check_config(self):
        for section in self.sections():
            parsed_section = self._recognized_config_map.get(section)
            if parsed_section is None:
                self.log.warning(f"Unrecognized section: {section}")
                continue

            for (key, val) in self.items(section):
                if not key.startswith('_') and key not in parsed_section:
                    self.log.warning(f"Unrecognized property: {key} in section {section}")

    def _add_recognized_property(self, property, section):
        self._get_recognized_section(section).add(property)

    def _get_recognized_section(self, section):
        return self._recognized_config_map.setdefault(section, set())


def coords_to_points(string) -> Iterable[Point]:
    return [Point(*map(int, m.groups())) for m in re.finditer(r'(\d+),(\d+)', string)]


def coords_to_polygons(string) -> Iterable[Polygon]:
    polygon_pattern = rf'((?:\d+,\d+,?)+)'
    return [Polygon(coords_to_points(m.group(0)))
            for m in re.finditer(polygon_pattern, string)]


def extract_int_pair(value: str) -> Iterable[int]:
    m = re.search(r'(\d+)x(\d+)', value)
    if m:
        return [int(x) for x in m.groups()]
    return []
