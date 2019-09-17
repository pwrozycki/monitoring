from threading import Lock


class FrameInfo:
    def __init__(self, frame, image_path):
        self.frame_json = frame
        self.detections = None
        self.image_path = image_path
        self.image = None
        self.event_info = None

    def __str__(self):
        log_dict = dict(self.event_info.event_json)
        log_dict.update(self.frame_json)
        return "(monitorId: {MonitorId}, eventId: {EventId}, frameId: {FrameId})".format(**log_dict)


class EventInfo:
    def __init__(self):
        self.event_json = None
        self.frame_info = None
        self.first_detection_time = None
        self.frame_score = 0
        self.planned_notification = None
        self.notification_sent = False
        self.all_frames_were_read = False
        self.lock = Lock()

    def __str__(self):
        return "(monitorId: {MonitorId}, eventId: {Id})".format(**self.event_json)
