import re
from configparser import ConfigParser, ExtendedInterpolation
from typing import Dict, Callable, Any


def get_config(config_map: Dict,
               monitor_id: str,
               default: Any) -> Any:
    for key in (monitor_id, 'default'):
        if key in config_map:
            value = config_map[key]
            if value:
                return value
    return default


def set_config(key: str,
               value: str,
               config_key: str,
               dictionary: Dict[str, Any],
               transform: Callable[[str], Any]):
    m = re.match(config_key + r'(\d*)', key)
    if m:
        monitor_id = m.group(1)
        k = monitor_id if monitor_id else 'default'
        dictionary[k] = transform(value)


class ConfigProvider(ConfigParser):
    def __init__(self, ini):
        super(ConfigParser, self).__init__(interpolation=ExtendedInterpolation())
        self.read(ini)

    @property
    def EVENT_LIST_URL(self): return self['zm']['event_list_url']

    @property
    def EVENT_DETAILS_URL(self): return self['zm']['event_details_url']

    @property
    def FRAME_FILE_NAME(self): return self['zm']['frame_jpg_path']

    @property
    def EVENTS_WINDOW_SECONDS(self): return self['timings'].getint('events_window_seconds')

    @property
    def EVENT_LOOP_SECONDS(self): return self['timings'].getint('event_loop_seconds')

    @property
    def FRAME_READ_DELAY_SECONDS(self): return self['timings'].getint('frame_read_delay_seconds')

    @property
    def CACHE_SECONDS_BUFFER(self): return self['timings'].getint('cache_seconds_buffer')

    @property
    def MODEL_FILE(self): return self['coral']['model_file']

    @property
    def MIN_SCORE(self): return float(self['coral']['min_score'])

    @property
    def EXCLUDED_ZONE_PREFIX(self): return self['detection_filter']['excluded_zone_prefix']

    @property
    def OBJECT_LABELS(self): return self['detection_filter']['object_labels'].split(',')

    @property
    def LABEL_FILE(self): return self['detection_filter']['label_file']

    @property
    def HOST(self): return self['mail']['host']

    @property
    def PORT(self): return int(self['mail']['port'])

    @property
    def USER(self): return self['mail']['user']

    @property
    def NOTIFICATION_DELAY_SECONDS(self): return self['timings'].getint('notification_delay_seconds')

    @property
    def PASSWORD(self): return self['mail']['password']

    @property
    def TO_ADDR(self): return self['mail']['to_addr']

    @property
    def FROM_ADDR(self): return self['mail']['from_addr']

    @property
    def TIMEOUT(self): return float(self['mail']['timeout'])

    @property
    def SUBJECT(self): return self['mail']['subject']

    @property
    def MESSAGE(self): return re.sub(r'\n\|', '\n', self['mail']['message'])

    @property
    def FRAME_PROCESSING_THREADS(self): return self['threading'].getint('frame_processing_threads')

    @property
    def THREAD_WATCHDOG_DELAY(self): return self['threading'].getint('thread_watchdog_delay')

    @property
    def EVENT_IDS(self): return [x for x in self.setdefault('debug', {}).setdefault('event_ids', '').split(',') if x]
