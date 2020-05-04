from abc import abstractmethod, ABC
from typing import Any, Iterable, Optional

from requests import Response

from events_processor.models import FrameInfo, EventInfo, ZoneInfo, Rect


class Detector(ABC):
    @abstractmethod
    def detect(self, frame_info: FrameInfo) -> None:
        raise NotImplemented()


class SecondPassDetector(Detector, ABC):
    pass


class ImageReader(ABC):
    @abstractmethod
    def read(self, file_name: str) -> Any:
        raise NotImplemented()


class NotificationSender(ABC):
    @abstractmethod
    def send(self, event_info: EventInfo, subject: str, message: str) -> bool:
        raise NotImplemented()


class SystemTime(ABC):
    @abstractmethod
    def sleep(self, seconds: float) -> None:
        raise NotImplemented()


class ZoneReader(ABC):
    @abstractmethod
    def read(self) -> Iterable[ZoneInfo]:
        raise NotImplemented()


class AlarmBoxReader(ABC):
    @abstractmethod
    def read(self, event_id: str,
             frame_id: str) -> Optional[Rect]:
        raise NotImplemented()


class ResourceReader(ABC):
    @abstractmethod
    def read(self, url: str) -> Optional[Response]:
        raise NotImplemented()


class Engine(ABC):
    @abstractmethod
    def detect(selfself, img, threshold):
        raise NotImplemented

    @abstractmethod
    def get_pending_processing_seconds(self) -> float:
        raise NotImplemented
