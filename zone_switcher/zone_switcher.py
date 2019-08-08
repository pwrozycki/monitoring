import os
from datetime import datetime

import mysql.connector
from astral import Astral

ZONE_UPDATES = (
    (
        """update Zones set MinPixelThreshold = %(threshold)s
            where Type = 'Active'
              and MinPixelThreshold != %(threshold)s""",
        lambda: {'threshold': by_day_or_night(17, 12)}
    ),
)

CONN_SETTINGS = {
    "host": "localhost",
    "user": "zmuser",
    "passwd": "zmpass",
    "database": "zm"
}


def update_zones():
    conn = mysql.connector.connect(**CONN_SETTINGS)
    cursor = conn.cursor()

    updated = 0
    for (query, param_func) in ZONE_UPDATES:
        cursor.execute(query, param_func())
        updated += cursor.rowcount

    conn.commit()

    if updated > 0:
        print("Updated zones, restarting zoneminder")
        os.system("systemctl restart zoneminder")
    else:
        print("No zones changed. Not restarting zoneminder.")

    conn.close()
    cursor.close()


def sun_info():
    a = Astral()
    a.solar_depression = 'civil'
    sun = a['Warsaw'].sun(date=datetime.today())
    return (nullify_tz(x) for x in (sun['sunrise'], sun['sunset']))


def nullify_tz(t):
    return t.replace(tzinfo=None)


def by_day_or_night(by_day, by_night):
    (sunrise, sunset) = sun_info()
    now = datetime.now()
    new_threshold = by_day if (sunrise < now < sunset) else by_night
    return new_threshold


if __name__ == '__main__':
    update_zones()
