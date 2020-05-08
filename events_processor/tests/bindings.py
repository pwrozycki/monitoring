from injector import Module, Binder, singleton

from events_processor.interfaces import Detector, NotificationSender, ImageReader, SystemTime, ZoneReader, \
    ResourceReader, AlarmBoxReader, Engine, MonitorReader
from tests.mocks import TestDetector, TestResourceReader, TestSender, TestImageReader, TestTime, TestZoneReader, \
    TestAlarmBoxReader, TestNoOpEngine, TestMonitorReader


class TestBindingsModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(Engine, to=TestNoOpEngine, scope=singleton)
        binder.bind(Detector, to=TestDetector, scope=singleton)
        binder.bind(NotificationSender, to=TestSender, scope=singleton)
        binder.bind(ImageReader, to=TestImageReader, scope=singleton)
        binder.bind(SystemTime, to=TestTime, scope=singleton)
        binder.bind(ZoneReader, to=TestZoneReader, scope=singleton)
        binder.bind(ResourceReader, to=TestResourceReader, scope=singleton)
        binder.bind(AlarmBoxReader, to=TestAlarmBoxReader, scope=singleton)
        binder.bind(MonitorReader, to=TestMonitorReader, scope=singleton)
