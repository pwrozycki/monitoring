from injector import Module, Binder

from events_processor.interfaces import Detector, NotificationSender, ImageReader, SystemTime, ZoneReader, \
    ResourceReader, AlarmBoxReader, Engine
from tests.mocks import TestDetector, TestResourceReader, TestSender, TestImageReader, TestTime, TestZoneReader, \
    TestAlarmBoxReader, TestNoOpEngine


class TestBindingsModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(Engine, TestNoOpEngine)
        binder.bind(Detector, to=TestDetector)
        binder.bind(NotificationSender, to=TestSender)
        binder.bind(ImageReader, to=TestImageReader)
        binder.bind(SystemTime, to=TestTime)
        binder.bind(ZoneReader, to=TestZoneReader)
        binder.bind(ResourceReader, to=TestResourceReader)
        binder.bind(AlarmBoxReader, to=TestAlarmBoxReader)
