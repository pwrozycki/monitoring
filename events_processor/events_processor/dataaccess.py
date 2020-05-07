from configparser import ConfigParser, ExtendedInterpolation
from typing import Callable, Any, Dict, Iterable, Optional

import mysql.connector
from mysql.connector import Error

from events_processor.interfaces import ZoneReader, AlarmBoxReader, MonitorReader
from events_processor.models import ZoneInfo, Rect, MonitorInfo

_CONN_POOL_DEFAULTS = {'pool_size': 8,
                       'pool_name': "mysql_conn_pool"}


class QuerySupport:
    def __init__(self):
        self._config = ConfigParser(interpolation=ExtendedInterpolation())
        self._config.read('db.ini')

    def _db_config(self, int_keywords: Iterable[str] = ('pool_size',)) -> Dict:
        db_config = _CONN_POOL_DEFAULTS

        db_config.update(self._config['db'])
        for kwd in int_keywords:
            if kwd in db_config:
                db_config[kwd] = int(db_config[kwd])
        return db_config

    def invoke_query(self, query_callback: Callable[[Any], Any]):
        conn = None
        try:
            conn = mysql.connector.connect(**self._db_config())

            if conn.is_connected():
                cursor = conn.cursor()
                return query_callback(cursor)
        except Error as e:
            print("Error when executing query", e)
        finally:
            conn.close()


class DBAlarmBoxReader(AlarmBoxReader, QuerySupport):
    def read(self, event_id: str, frame_id: str, excl_zone_prefix) -> Optional[Rect]:
        def query(cursor):
            cursor.execute(
                """select st.MinX, st.MinY, st.MaxX, st.MaxY 
                     from Stats st
                     join Zones zn    on st.ZoneId = zn.Id
                    where st.frameId = %(frameId)s and st.eventId = %(eventId)s
                      and zn.Name not like concat(%(prefix)s, '%')""",
                {'eventId': event_id,
                 'frameId': frame_id,
                 'prefix': excl_zone_prefix})
            return cursor.fetchone()

        res = self.invoke_query(query)
        return Rect(*res) if res else None


class DBZoneReader(ZoneReader, QuerySupport):
    def read(self, excl_zone_prefix) -> Iterable[ZoneInfo]:
        def query(cursor):
            cursor.execute(
                """select m.Id, m.Width, m.Height, z.Name, z.Coords 
                   from Zones z
                   join Monitors m on m.Id = z.MonitorId
                   where z.Name like concat(%(prefix)s, '%')""",
                {'prefix': excl_zone_prefix})
            return cursor.fetchall()

        return [ZoneInfo(str(m_id), int(w), int(h), name, coords) for (m_id, w, h, name, coords) in
                self.invoke_query(query)]


class DBMonitorReader(MonitorReader, QuerySupport):
    def read(self) -> Iterable[MonitorInfo]:
        def query(cursor):
            cursor.execute(
                """select m.Id, m.Name
                   from Monitors m """)
            return cursor.fetchall()

        return [MonitorInfo(str(id), name) for (id, name) in self.invoke_query(query)]
