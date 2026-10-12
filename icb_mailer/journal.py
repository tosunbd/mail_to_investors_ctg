"""Sent journal (SQLite, standard library).

Every successful send is recorded per (job, account, day). If a run is stopped
half way (power cut, network drop, someone closes the window) simply run it
again: accounts that already got today's email are skipped, so nobody receives
a duplicate. Use --force to resend anyway.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path


class SentJournal:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, timeout=30)
        self._db.execute("pragma journal_mode=wal")
        self._db.execute(
            """create table if not exists sent (
                   job        text not null,
                   account    text not null,
                   run_date   text not null,
                   email      text not null,
                   message_id text,
                   sent_at    text not null,
                   primary key (job, account, run_date)
               )"""
        )
        self._db.commit()

    def already_sent(self, job: str, account: str, day: date | None = None) -> bool:
        day = day or date.today()
        row = self._db.execute(
            "select 1 from sent where job = ? and account = ? and run_date = ?",
            (job, account, day.isoformat()),
        ).fetchone()
        return row is not None

    def record(self, job: str, account: str, email: str, message_id: str | None) -> None:
        now = datetime.now()
        self._db.execute(
            "insert or replace into sent values (?, ?, ?, ?, ?, ?)",
            (job, account, now.date().isoformat(), email, message_id, now.isoformat(timespec="seconds")),
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()
