from queue import Queue

from injector import Module, Binder, singleton

from events_processor.configtools import ConfigProvider
from events_processor.controller import MainController, DefaultSystemTime
from events_processor.dataaccess import DBZoneReader, DBAlarmBoxReader, DBMonitorReader
from events_processor.detector import CoralDetector, SynchronizedDetectionEngine
from events_processor.filters import DetectionFilter
from events_processor.interfaces import Detector, NotificationSender, ImageReader, SystemTime, ZoneReader, \
    ResourceReader, AlarmBoxReader, Engine, MonitorReader
from events_processor.models import NotificationQueue, FrameQueue
from events_processor.notifications import MailNotificationSender, NotificationWorker, FSNotificationSender, \
    DetectionNotifier, EventUpdater
from events_processor.preprocessor import RotatingPreprocessor
from events_processor.processor import FSImageReader, FrameProcessorWorker
from events_processor.reader import FrameReader, WebResourceReader, FrameReaderWorker
from events_processor.renderer import DetectionRenderer


class AppBindingsModule(Module):

    def configure(self, binder: Binder) -> None:
        binder.bind(FrameReader)
        binder.bind(MainController)
        binder.bind(DetectionFilter)
        binder.bind(RotatingPreprocessor)
        binder.bind(DetectionNotifier)
        binder.bind(EventUpdater)
        binder.bind(DetectionRenderer)

        binder.bind(ConfigProvider, scope=singleton)

        binder.bind(FrameReaderWorker)
        binder.bind(FrameProcessorWorker)
        binder.bind(NotificationWorker)

        binder.bind(NotificationQueue, to=Queue())
        binder.bind(FrameQueue, to=Queue())

        binder.bind(Engine, SynchronizedDetectionEngine, scope=singleton)
        binder.bind(Detector, to=CoralDetector)
        binder.bind(NotificationSender, to=MailNotificationSender)
        binder.bind(ImageReader, to=FSImageReader)
        binder.bind(SystemTime, to=DefaultSystemTime)
        binder.bind(ZoneReader, to=DBZoneReader)
        binder.bind(ResourceReader, to=WebResourceReader)
        binder.bind(AlarmBoxReader, to=DBAlarmBoxReader)
        binder.bind(MonitorReader, to=DBMonitorReader)


class FSNotificationSenderOverride(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(NotificationSender, to=FSNotificationSender)
