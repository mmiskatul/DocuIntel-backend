"""get_conn waits for a free connection instead of failing at once when the pool is exhausted."""
import threading
import time

import pytest
from psycopg2 import pool

from app.core import database


class _FakePool:
    """Two connections; getconn raises PoolError like psycopg2 when none is free."""

    def __init__(self):
        self.free = ["c1", "c2"]
        self.lock = threading.Lock()

    def getconn(self):
        with self.lock:
            if not self.free:
                raise pool.PoolError("connection pool exhausted")
            return self.free.pop()

    def putconn(self, conn):
        with self.lock:
            self.free.append(conn)


def test_get_conn_waits_until_a_connection_is_released(monkeypatch):
    fake = _FakePool()
    monkeypatch.setattr(database, "db_pool", fake)
    monkeypatch.setattr(database, "DB_POOL_WAIT_SECONDS", 5)
    a, b = database.get_conn(), database.get_conn()
    threading.Timer(0.3, lambda: database.release_conn(a)).start()
    start = time.monotonic()
    got = database.get_conn()
    assert got == a and 0.2 < time.monotonic() - start < 3
    database.release_conn(b)
    database.release_conn(got)


def test_get_conn_still_fails_after_the_wait_limit(monkeypatch):
    fake = _FakePool()
    monkeypatch.setattr(database, "db_pool", fake)
    monkeypatch.setattr(database, "DB_POOL_WAIT_SECONDS", 0.3)
    database.get_conn()
    database.get_conn()
    with pytest.raises(pool.PoolError):
        database.get_conn()
