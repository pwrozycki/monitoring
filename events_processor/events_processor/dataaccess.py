import threading

import mysql.connector

from events_processor import config

_conn = None
_conn_lock = threading.Lock()


def get_conn():
    global _conn, _conn_lock
    if not _conn:
        _conn = mysql.connector.connect(**config['db'])
    return _conn, _conn_lock


def retrieve_alarm_stats(event_id, frame_id):
    (conn, lock) = get_conn()
    with lock:
        cursor = conn.cursor()
        cursor.execute(
            """select MinX, MinY, MaxX, MaxY 
              from Stats
              where frameId = %(frameId)s and eventId = %(eventId)s""",
            {'eventId': event_id, 'frameId': frame_id})
        return cursor.fetchone()


def retrieve_zones(zone_name_prefix):
    (conn, lock) = get_conn()
    with lock:
        cursor = conn.cursor()
        cursor.execute(
            """select m.Id, m.Width, m.Height, z.Name, z.Coords 
               from Zones z
               join Monitors m on m.Id = z.MonitorId
               where z.Name like concat(%(prefix)s, '%')""",
            {'prefix': zone_name_prefix})
        return ((m_id, int(w), int(h), name, coords) for (m_id, w, h, name, coords) in cursor.fetchall())
