from argparse import ArgumentParser

from events_processor.controller import MainController
from events_processor.notifications import FSNotificationSender


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

    event_controller_args = {}
    if args.fs_notifier:
        event_controller_args['send_notification'] = FSNotificationSender().send_notification
    if args.read_events:
        event_controller_args['event_ids'] = args.read_events.split(',')

    MainController(**event_controller_args).start()


if __name__ == '__main__':
    main()
