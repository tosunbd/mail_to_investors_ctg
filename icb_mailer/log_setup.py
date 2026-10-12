from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

LOG_PREFIX = "email_to_investors"

# Pass extra=PLAIN to write a line without the time/level prefix (used for the
# audit table so its columns stay readable).
PLAIN = {"plain": True}


class _Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if getattr(record, "plain", False):
            return record.getMessage()
        return super().format(record)


def setup_logging(log_dir: Path, verbose: bool = False) -> Path:
    """Console + one log file per run: logs/email_to_investors_YYYY-MM-DD_HH-MM-SS.log"""
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = log_dir / f"{LOG_PREFIX}_{stamp}.log"
    n = 1
    while log_file.exists():  # two runs started in the same second
        n += 1
        log_file = log_dir / f"{LOG_PREFIX}_{stamp}_{n}.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(_Formatter("%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"))
    root.addHandler(file_handler)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(_Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    root.addHandler(console)

    for noisy in ("PIL", "reportlab"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return log_file
