import re
from configparser import ConfigParser, ExtendedInterpolation
from typing import Dict, Callable, Any


def properties_config(ini):
    config = ConfigParser(interpolation=ExtendedInterpolation())
    config.read(ini)
    return config


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
