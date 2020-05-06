from dataclasses import dataclass, field
from queue import Queue
from threading import Lock
from typing import Any, Sequence, Dict, Tuple, NewType, Iterable, List

from shapely import geometry


@dataclass
class Point:
    x: int
    y: int

    @property
    def tuple(self) -> Tuple[int, int]:
        return self.x, self.y

    def moved_by(self, x, y) -> 'Point':
        return Point(self.x + x, self.y + y)

    @property
    def shapely_point(self):
        return geometry.Point(self.tuple)


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
        return abs(self.right - self.left + 1)

    @property
    def height(self):
        return abs(self.bottom - self.top + 1)

    @property
    def area(self):
        return self.width * self.height

    @property
    def box_tuple(self) -> Tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom

    @property
    def points(self):
        return (self.top_left, self.top_right, self.bottom_right, self.bottom_left)

    def moved_by(self, x, y) -> 'Rect':
        return Rect(*self.top_left.moved_by(x, y).tuple,
                    *self.bottom_right.moved_by(x, y).tuple)


@dataclass
class Polygon:
    points: Iterable[Point]

    @property
    def shapely_poly(self):
        return geometry.Polygon(p.tuple for p in self.points)


@dataclass
class Detection:
    rect: Rect
    score: float
    label_id: int


@dataclass
class FrameInfo:
    frame_json: Dict
    monitor_json: Dict
    image_path: str
    event_info: "EventInfo"
    detections: Sequence[Detection] = field(init=False)
    image: Any = field(init=False)
    alarm_box: Rect = field(init=False)
    chunk_rects: List[Rect] = field(default_factory=list)

    def __str__(self):
        return f"(m: {self.monitor_json['Name']}, eid: {self.event_id}, fid: {self.frame_id})"

    @property
    def frame_id(self):
        return self.frame_json['FrameId']

    @property
    def event_id(self):
        return self.frame_json['EventId']

    @property
    def timestamp(self):
        return self.frame_json['TimeStamp']

    @property
    def type(self):
        return self.frame_json['Type']


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
        return f"(mid: {self.monitor_id}, eid: {self.event_id})"

    @property
    def event_id(self):
        return self.event_json['Id']

    @property
    def monitor_id(self):
        return self.event_json['MonitorId']

    @property
    def height(self):
        return int(self.event_json['Height'])

    @property
    def width(self):
        return int(self.event_json['Width'])

    @property
    def end_time(self):
        return self.event_json['EndTime']

    @property
    def emailed(self):
        return self.event_json['Emailed'] == '1'


@dataclass
class ZoneInfo:
    monitor_id: str
    width: int
    height: int
    name: str
    coords: str


@dataclass
class MonitorInfo:
    id: str
    name: str


@dataclass
class ZonePolygon:
    zone: ZoneInfo
    polygon: Polygon


NotificationQueue = NewType('NotificationQueue', Queue)
FrameQueue = NewType('FrameQueue', Queue)
