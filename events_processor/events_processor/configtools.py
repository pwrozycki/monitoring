import re


def get_config(config_map, monitor_id, default):
    for key in (monitor_id, 'default'):
        if key in config_map:
            value = config_map[key]
            if value:
                return value
    return default


def set_config(key, value, config_key, dictionary, transform):
    m = re.match(config_key + r'(\d*)', key)
    if m:
        monitor_id = m.group(1)
        key = monitor_id if monitor_id else 'default'
        dictionary[key] = transform(value)