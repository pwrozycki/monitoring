#!/bin/sh

WATCHDOG_FILE=/run/watchdog-pinger.run
# initially give watchdog impression that eveyrhing is fine
touch $WATCHDOG_FILE

while :
do
	ping 8.8.8.8 -w 1 -c 1 1>/dev/null 2>&1 && touch $WATCHDOG_FILE
	sleep 10
done
