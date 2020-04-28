import logging.config

from configparser import ConfigParser, ExtendedInterpolation

logging.config.fileConfig('events_processor.logging.conf')

# TODO: prozycki: list:
# remove intersecting boxes of smaller priority (? - when almost whole rectangle is contained within another rectangle)
