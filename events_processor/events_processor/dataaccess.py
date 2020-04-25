from typing import Callable, Any, Dict, Iterable

import mysql.connector
from mysql.connector import Error

from events_processor import config
from events_processor.interfaces import ZoneReader, AlarmBoxReader
from events_processor.models import ZoneInfo, Rect

_CONN_POOL_DEFAULTS = {'pool_size': 8,
                       'pool_name': "mysql_conn_pool"}

EXCLUDED_ZONE_PREFIX = config['detection_filter']['excluded_zone_prefix']


def _db_config(int_keywords: Iterable[str] = ('pool_size',)) -> Dict:
    db_config = _CONN_POOL_DEFAULTS

    db_config.update(config['db'])
    for kwd in int_keywords:
        if kwd in db_config:
            db_config[kwd] = int(db_config[kwd])
    return db_config


def _invoke_query(query_callback: Callable[[Any], Any]):
    conn = None
    try:
        conn = mysql.connector.connect(**_db_config())

        if conn.is_connected():
            cursor = conn.cursor()
            return query_callback(cursor)
    except Error as e:
        print("Error when executing query", e)
    finally:
        conn.close()


class DBAlarmBoxReader(AlarmBoxReader):
    def query(self, cursor):
        cursor.execute(
            """select st.MinX, st.MinY, st.MaxX, st.MaxY 
                 from Stats st
                 join Zones zn    on st.ZoneId = zn.Id
                where st.frameId = %(frameId)s and st.eventId = %(eventId)s
                  and zn.Name not like concat(%(prefix)s, '%')""",
            {'eventId': event_id,
             'frameId': frame_id,
             'prefix': EXCLUDED_ZONE_PREFIX})
        return cursor.fetchone()

    def read(self, event_id: str,
             frame_id: str) -> Rect:
        return Rect(*_invoke_query(self.query))


class DBZoneReader(ZoneReader):
    def query(self, cursor):
        cursor.execute(
            """select m.Id, m.Width, m.Height, z.Name, z.Coords 
               from Zones z
               join Monitors m on m.Id = z.MonitorId
               where z.Name like concat(%(prefix)s, '%')""",
            {'prefix': EXCLUDED_ZONE_PREFIX})
        return cursor.fetchall()

    def read(self) -> Iterable[ZoneInfo]:
        return [ZoneInfo(str(m_id), int(w), int(h), name, coords) for (m_id, w, h, name, coords) in
                _invoke_query(self.query)]
