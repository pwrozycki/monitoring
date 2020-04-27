from dataclasses import dataclass, field
from queue import Queue
from threading import Lock
from typing import Any, Sequence, Dict, Tuple, NewType, MutableMapping


@dataclass
class Point:
    x: int
    y: int

    @property
    def tuple(self) -> Tuple[int, int]:
        return self.x, self.y

    def moved_by(self, x, y) -> 'Point':
        return Point(self.x + x, self.y + y)


@dataclass
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def top_left(self) -> Point:
        return Point(self.left, self.top)

    @property
    def top_right(self) -> Point:
        return Point(self.right, self.top)

    @property
    def bottom_left(self) -> Point:
        return Point(self.left, self.bottom)

    @property
    def bottom_right(self) -> Point:
        return Point(self.right, self.bottom)

    @property
    def width(self):
        return abs(self.right - self.left)

    @property
    def height(self):
        return abs(self.bottom - self.top)

    @property
    def area(self):
        return self.width * self.height

    @property
    def box_tuple(self) -> Tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom

    def moved_by(self, x, y) -> 'Rect':
        return Rect(*self.top_left.moved_by(x, y).tuple,
                    *self.bottom_right.moved_by(x, y).tuple)


@dataclass
class Detection:
    rect: Rect
    score: float
    label_id: int


@dataclass
class FrameInfo:
    frame_json: Dict
    image_path: str
    detections: Sequence[Detection] = field(init=False)
    image: Any = field(init=False)
    event_info: "EventInfo" = field(init=False)

    def __str__(self):
        log_dict = dict(self.event_info.event_json)
        log_dict.update(self.frame_json)
        return "(monitorId: {MonitorId}, eventId: {EventId}, frameId: {FrameId})".format(**log_dict)


@dataclass(init=True, eq=False)
class EventInfo:
    frame_info: FrameInfo = field(init=False)
    event_json: Dict = field(default_factory=dict)
    first_detection_time: float = 0
    frame_score: float = 0
    planned_notification: float = 0
    notification_sent: bool = False
    all_frames_were_read: bool = False
    lock: Any = field(default_factory=Lock)

    def __str__(self) -> str:
        return "(monitorId: {MonitorId}, eventId: {Id})".format(**self.event_json)


@dataclass
class ZoneInfo:
    monitor_id: str
    width: int
    height: int
    name: str
    coords: str


NotificationQueue = NewType('NotificationQueue', Queue)
FrameQueue = NewType('FrameQueue', Queue)
Config = NewType('Config', MutableMapping)
