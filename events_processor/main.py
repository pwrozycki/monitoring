from argparse import ArgumentParser

from injector import Injector

from events_processor.bindings import AppBindingsModule, FSNotificationSenderOverride
from events_processor.configtools import ConfigProvider
from events_processor.controller import MainController


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

    modules = [AppBindingsModule]
    if args.fs_notifier:
        modules.append(FSNotificationSenderOverride)

    injector = Injector(modules)
    if args.event_ids:
        config = injector.get(ConfigProvider)
        config['debug']['event_ids'] = args.event_ids
        config.reread()

    controller = injector.get(MainController)
    controller.start()


if __name__ == '__main__':
    main()
