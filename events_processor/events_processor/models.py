from dataclasses import dataclass, field
from enum import Enum, auto
from queue import Queue
from threading import Lock
from typing import Any, Sequence, Dict, Tuple, NewType, Iterable, List, Optional

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

    def __str__(self):
        return f"Rect({self.left}, {self.top}, {self.right}, {self.bottom})"

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
    label: Optional[str] = None
    alarm_diff: Optional[float] = None
    detection_diff: Optional[float] = None
    detection_area_percent: Optional[float] = None
    threshold_acceptance_type: Optional[str] = None
    discard_reasons: List[str] = field(default_factory=list)

    @property
    def resolution(self):
        if self.discard_reasons:
            return f"discarded({','.join(self.discard_reasons)})"
        else:
            return f"accepted({self.threshold_acceptance_type})"

    @property
    def alarm_measurements_str(self):
        return f"al+{self.alarm_diff}% det+{self.detection_diff}%" if self.alarm_diff is not None else ""


@dataclass
class FrameInfo:
    frame_json: Dict
    monitor_json: Dict
    image_path: str
    event_info: "EventInfo"
    image: Any = None
    chunk_rects: List[Rect] = field(default_factory=list)
    detections: Sequence[Detection] = field(default_factory=list)
    alarm_box: Optional[Rect] = None
    score: float = 0

    def __str__(self):
        return f"(m: {self.monitor_json['Name']}, eid: {self.event_id}, fid: {self.frame_id})"

    @property
    def detections_str(self):
        return f"Frame {self.verdict} {self}, " + (
            f"{[self._detection_str(d) for d in self.detections]}" if self.detections else "no detections")

    def _detection_str(self, det: Detection):
        return f"{det.resolution} {det.label} {det.score * 100:.0f}% det_box:{det.detection_area_percent:.2f}%" \
               f" {self._det_alarm_str(det)}".strip()

    def _det_alarm_str(self, det):
        return f"al_box:{self._alarm_box_perc():.2f}% al+{det.alarm_diff}% det+{det.detection_diff}%" if det.alarm_diff is not None else ""

    def _alarm_box_perc(self):
        (h, w, _) = self.image.shape
        return self.alarm_box.area / (h * w) * 100

    @property
    def verdict(self):
        return "accepted" if self.accepted_detections else "discarded"

    @property
    def accepted_detections(self):
        return [d for d in self.detections if not d.discard_reasons]

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


class NotificationStatus(Enum):
    NONE = auto()
    SUBMITTED = auto()
    SENDING = auto()
    SENT = auto()

    @property
    def was_submitted(self):
        return self in [self.SUBMITTED, self.SENDING, self.SENT]

    @property
    def was_sending(self):
        return self in [ self.SENDING, self.SENT]

    @property
    def was_sent(self):
        return self == self.SENT


@dataclass(init=True, eq=False)
class EventInfo:
    event_json: Dict = field(default_factory=dict)
    notification_submission_time: float = 0
    frame_score: float = 0
    planned_notification: float = 0
    notification_status: NotificationStatus = NotificationStatus.NONE
    all_frames_were_read: bool = False
    lock: Any = field(default_factory=Lock)
    candidate_frames: List[FrameInfo] = field(default_factory=list)

    def __str__(self) -> str:
        return f"(mid: {self.monitor_id}, eid: {self.event_id})"

    def max_score_frame(self) -> FrameInfo:
        return max(self.candidate_frames, key=lambda frame: frame.score)

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

    def release_frame_images(self):
        for frame in self.candidate_frames:
            frame.image = None


    def release_less_scored_frames_images(self):
        max_score_frame = self.max_score_frame()
        for frame in self.candidate_frames:
            if frame != max_score_frame:
                frame.image = None

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
