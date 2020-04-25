from injector import Module, Binder

from events_processor.controller import MainController
from events_processor.dataaccess import DBZoneReader, DBAlarmBoxReader
from events_processor.detector import CoralDetector
from events_processor.interfaces import Detector, NotificationSender, ImageReader, SystemTime, ZoneReader, \
    ResourceReader, AlarmBoxReader
from events_processor.notifications import MailNotificationSender
from events_processor.processor import FSImageReader
from events_processor.reader import FrameReader, WebResourceReader
from main import DefaultSystemTime


class ProcessorModule(Module):

    def configure(self, binder: Binder) -> None:
        binder.bind(FrameReader, to=FrameReader)
        binder.bind(MainController, to=MainController)

        binder.bind(Detector, to=CoralDetector)
        binder.bind(NotificationSender, to=MailNotificationSender)
        binder.bind(ImageReader, to=FSImageReader)
        binder.bind(SystemTime, to=DefaultSystemTime)
        binder.bind(ZoneReader, to=DBZoneReader)
        binder.bind(ResourceReader, to=WebResourceReader)
        binder.bind(AlarmBoxReader, to=DBAlarmBoxReader)
