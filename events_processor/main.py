from argparse import ArgumentParser

from injector import Injector

from events_processor.bindings import ProcessorModule
from events_processor.controller import MainController
from events_processor.models import Config


def main():
    argparser = ArgumentParser()
    argparser.add_argument(
        "--fs-notifier",
        help="write notification images to disk instead of mailing them",
        action="store_true")
    argparser.add_argument(
        "--event-ids",
        help="analyze specific events instead of fetching recent ones. Specify comma separated list of event ids")
    args = argparser.parse_args()

    injector = Injector([ProcessorModule])
    if args.event_ids:
        config = injector.get(Config)
        config.setdefault('debug', {})['event_ids'] = args.event_ids
    controller = injector.get(MainController)
    controller.start()


if __name__ == '__main__':
    main()
