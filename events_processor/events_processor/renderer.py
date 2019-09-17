import logging

import cv2


class DetectionRenderer:
    log = logging.getLogger('events_processor.DetectionRenderer')

    def annotate_image(self, frame_info):
        image = frame_info.image
        detections = frame_info.detections
        for (i, detection) in enumerate(detections):
            box = tuple(int(x) for x in detection.bounding_box.flatten().tolist())
            point1 = tuple(box[:2])
            point2 = tuple(box[2:])
            cv2.rectangle(image, point1, point2, (255, 0, 0), 1)

            area_percents = 100 * self._box_area(point1, point2) / self._box_area((0, 0), frame_info.image.shape[:2])
            score_percents = 100 * detection.score

            self.log.debug(
                f'Rendering detection: (index: {i}, score: {score_percents:.0f}%, area: {area_percents:.2f}%), box: {box}')

            self._draw_text(f'{score_percents:.0f}%', box, image)
        return image

    def _draw_text(self, text, box, image):
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.5
        font_thickness = 1
        text_size = cv2.getTextSize(text, font, font_scale, font_thickness)[0]
        y_offset = text_size[1]
        cv2.rectangle(
            image, (box[0], box[1]), (box[0] + text_size[0], box[1] + text_size[1] + 1), (0, 0, 0), cv2.FILLED)
        cv2.putText(image, text, (box[0], box[1] + y_offset), font, font_scale, (0, 255, 0), font_thickness)

    def _box_area(self, point1, point2):
        return abs(point2[0] - point1[0]) * abs(point2[1] - point1[1])
