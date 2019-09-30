import logging.config

logging.config.fileConfig('events_processor.logging.conf')
from configparser import ConfigParser, ExtendedInterpolation

# TODO: prozycki: list:
# - remove intersecting boxes of smaller priority (? - when almost whole rectangle is contained within another rectangle)

config = ConfigParser(interpolation=ExtendedInterpolation())


def read_config():
    global config
    config.read('events_processor.ini')


read_config()
