from events_processor.models import Rect


def bounding_box(points):
    left = right = top = bottom = None
    for pt in points:
        left = min(left, pt.x) if left else pt.x
        right = max(right, pt.x) if right else pt.x
        top = min(top, pt.y) if top else pt.y
        bottom = max(bottom, pt.y) if bottom else pt.y
    return Rect(left, top, right, bottom)
