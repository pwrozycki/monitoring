from argparse import ArgumentParser

from injector import Injector

from events_processor.bindings import ProcessorModule
from events_processor.controller import MainController


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

    # TODO: prozycki: restore --fs-notifier and --read-events functionality
    #
    # notification_sender = FSNotificationSender() if args.fs_notifier else MailNotificationSender()
    #
    # event_controller_args = {}
    # if args.read_events:
    #     event_controller_args['event_ids'] = args.read_events.split(',')

    injector = Injector([ProcessorModule])
    controller = injector.get(MainController)
    controller.start()

if __name__ == '__main__':
    main()
