"""Oracle connection handling (python-oracledb, the successor of cx_Oracle).

The branch database is Oracle 10g. python-oracledb "thin" mode needs 12.1+, so
10g/11g must use "thick" mode with Oracle Instant Client 11.2 (the newest client
that still talks to a 10g server). python-oracledb 3.x is the last line that
accepts an 11.2 client, which is why requirements.txt pins it.

The client bitness must match Python: the bundled client is 32-bit, so Windows
needs 32-bit Python (see SETUP_AND_SCHEDULER_GUIDE.txt).
"""

from __future__ import annotations

import logging
import os
import struct
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import oracledb

from icb_mailer.config import DatabaseSettings

log = logging.getLogger(__name__)

_client_initialised = False


class DatabaseError(Exception):
    """Raised for connection problems, with an operator-friendly message."""


def _dll_bitness(dll: Path) -> int | None:
    """Return 32 or 64 for a Windows PE file, None when it cannot be read."""
    try:
        with dll.open("rb") as fh:
            header = fh.read(4096)
        pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
        machine = struct.unpack_from("<H", header, pe_offset + 4)[0]
    except (OSError, struct.error):
        return None
    return {0x14C: 32, 0x8664: 64}.get(machine)


def init_client(settings: DatabaseSettings) -> None:
    """Switch python-oracledb into thick mode once per process."""
    global _client_initialised
    if settings.mode == "thin" or _client_initialised:
        return

    python_bits = struct.calcsize("P") * 8
    lib_dir = None
    # On Linux the client is found through LD_LIBRARY_PATH, set by run.sh before
    # Python starts (lib_dir is not supported there), so client_dir is Windows-only.
    if sys.platform == "win32" and settings.client_dir is not None:
        if not (settings.client_dir / "oci.dll").is_file():
            raise DatabaseError(f"Oracle Instant Client not found in {settings.client_dir}")
        client_bits = _dll_bitness(settings.client_dir / "oci.dll")
        if client_bits and client_bits != python_bits:
            raise DatabaseError(
                f"Oracle client in {settings.client_dir} is {client_bits}-bit but Python is "
                f"{python_bits}-bit. Use {client_bits}-bit Python (re-run setup_windows.bat) "
                f"or put a {python_bits}-bit Instant Client 11.2 in that folder."
            )
        lib_dir = str(settings.client_dir)
        os.environ["PATH"] = lib_dir + os.pathsep + os.environ.get("PATH", "")

    try:
        oracledb.init_oracle_client(lib_dir=lib_dir)
    except oracledb.Error as exc:
        raise DatabaseError(f"Could not load the Oracle client ({exc}).") from exc
    _client_initialised = True
    log.info(
        "Oracle client %s loaded (thick mode, %s-bit)",
        ".".join(map(str, oracledb.clientversion()[:4])),
        python_bits,
    )


def _make_dsn(settings: DatabaseSettings) -> str:
    if settings.service_name:
        return oracledb.makedsn(settings.host, settings.port, service_name=settings.service_name)
    return oracledb.makedsn(settings.host, settings.port, sid=settings.sid)


def connect(settings: DatabaseSettings) -> oracledb.Connection:
    init_client(settings)
    dsn = _make_dsn(settings)
    attempts = settings.connect_retries + 1
    for attempt in range(1, attempts + 1):
        try:
            conn = oracledb.connect(user=settings.user, password=settings.password, dsn=dsn)
            # call_timeout needs Oracle Client 18+; the 11.2 client has no per-call limit.
            if settings.mode == "thin" or oracledb.clientversion()[0] >= 18:
                conn.call_timeout = 10 * 60 * 1000
            log.info("Connected to Oracle %s (%s)", conn.version, settings.dsn_display)
            return conn
        except oracledb.Error as exc:
            (error,) = exc.args if exc.args else (None,)
            code = getattr(error, "code", None)
            # Wrong user/password or locked account: retrying will not help
            # and may lock the account.
            if code in (1017, 28000, 28001):
                raise DatabaseError(f"Database login rejected for {settings.dsn_display}: {exc}") from exc
            if attempt == attempts:
                raise DatabaseError(f"Cannot connect to {settings.dsn_display}: {exc}") from exc
            log.warning(
                "Database connection attempt %s/%s failed: %s - retrying in %ss",
                attempt, attempts, exc, settings.connect_retry_delay_seconds,
            )
            time.sleep(settings.connect_retry_delay_seconds)
    raise AssertionError("unreachable")


@contextmanager
def open_connection(settings: DatabaseSettings) -> Iterator[oracledb.Connection]:
    conn = connect(settings)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except oracledb.Error:
            pass
