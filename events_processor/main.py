import time
from argparse import ArgumentParser

from events_processor.controller import MainController
from events_processor.detector import CoralDetector
from events_processor.interfaces import SystemTime
from events_processor.notifications import FSNotificationSender, MailNotificationSender
from events_processor.processor import FSImageReader
from events_processor.reader import FrameReader, WebResourceReader


class DefaultSystemTime(SystemTime):
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def main():
    argparser = ArgumentParser()
    argparser.add_argument(
        "--fs-notifier",
        help="write notification images to disk instead of mailing them",
        action="store_true")
    argparser.add_argument(
        "--read-events",
        help="analyze specific events instead of fetching recent ones. Specify comma separated list of event ids")
    args = argparser.parse_args()

    notification_sender = FSNotificationSender() if args.fs_notifier else MailNotificationSender()

    event_controller_args = {}
    if args.read_events:
        event_controller_args['event_ids'] = args.read_events.split(',')

    MainController(detector=CoralDetector(),
                   image_reader=FSImageReader(),
                   notification_sender=notification_sender,
                   frame_reader=FrameReader(resource_reader=WebResourceReader()),
                   system_time=DefaultSystemTime(),
                   **event_controller_args).start()


if __name__ == '__main__':
    main()
