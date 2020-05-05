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
    @inject
    def __init__(self,
                 monitor_reader: MonitorReader):
        super(ConfigParser, self).__init__(interpolation=ExtendedInterpolation())
        self.optionxform = str
        self._monitor_id_for_name = {m.name: m.id for m in monitor_reader.read()}

        self.read('events_processor.ini')
        self.reread()

    def reread(self):
        self.event_list_url = self['zm']['event_list_url']
        self.event_details_url = self['zm']['event_details_url']
        self.frame_jpg_path = self['zm']['frame_jpg_path']

        self.notification_delay_seconds = self['timings'].getint('notification_delay_seconds', fallback=0)
        self.events_window_seconds = self['timings'].getint('events_window_seconds', fallback=600)
        self.event_loop_seconds = self['timings'].getint('event_loop_seconds', fallback=5)
        self.frame_read_delay_seconds = self['timings'].getint('frame_read_delay_seconds', fallback=5)
        self.cache_seconds_buffer = self['timings'].getint('cache_seconds_buffer', fallback=120)

        self.rotations = self.extract_config('rotating_preprocessor', 'rotate', int)

        self.detector_model_file = self['coral']['model_file']
        self.min_score = float(self['coral']['min_score'])
        self.detection_chunks = self.extract_config('coral', 'detection_chunks', extract_int_pair)

        self.excluded_zone_prefix = self['detection_filter'].get('excluded_zone_prefix')
        self.object_labels = self['detection_filter'].get('object_labels', fallback='person').split(',')
        self.label_file = self['detection_filter']['label_file']
        self.movement_indifferent_min_score = self.extract_config('detection_filter', 'movement_indifferent_min_score',
                                                                  float)
        self.coarse_movement_min_score = self.extract_config('detection_filter', 'coarse_movement_min_score', float)
        self.precise_movement_min_score = self.extract_config('detection_filter', 'precise_movement_min_score', float)
        self.max_movement_to_intersection_ratio = self.extract_config('detection_filter',
                                                                      'max_movement_to_intersection_ratio', float)
        self.min_box_area_percentage = self.extract_config('detection_filter', 'min_box_area_percentage', float)
        self.max_box_area_percentage = self.extract_config('detection_filter', 'max_box_area_percentage', float)
        self.excluded_points = self.extract_config('detection_filter', 'excluded_points', coords_to_points)
        self.excluded_polygons = self.extract_config('detection_filter', 'excluded_polygons', coords_to_polygons)

        self.host = self['mail']['host']
        self.port = self['mail'].getint('port', fallback=587)
        self.user = self['mail']['user']
        self.password = self['mail']['password']
        self.to_addr = self['mail']['to_addr']
        self.from_addr = self['mail']['from_addr']
        self.timeout = self['mail'].getfloat('timeout', fallback=10)
        self.subject = self['mail']['subject']
        self.message = re.sub(r'\n\|', '\n', self['mail']['message'])

        self.frame_processing_threads = self['threading'].getint('frame_processing_threads', fallback=2)
        self.thread_watchdog_delay = self['threading'].getint('thread_watchdog_delay', fallback=5)

        self.event_ids = [x for x in self['debug'].get('event_ids', fallback='').split(',') if x]
        self.debug_images = [x for x in self['debug'].get('debug_images', fallback='').split(',') if x]

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

    def extract_config(self, section, config_key, transform):
        d = {}
        for (key, value) in self[section].items():
            self._set_config(key, value, config_key, d, transform)
        return d


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
