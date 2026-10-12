"""Per-run report: a printable TXT and an Excel sheet, one row per account."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

log = logging.getLogger(__name__)


class Status(str, Enum):
    SENT = "SENT"
    DRY_RUN = "DRY RUN (not sent)"
    FAILED = "FAILED"
    INVALID_EMAIL = "INVALID EMAIL"
    NO_DATA = "NO DATA"
    ALREADY_SENT = "ALREADY SENT TODAY"


@dataclass
class ResultRow:
    account: str
    name: str
    email: str
    status: Status
    detail: str = ""
    attachment: str = ""
    time: datetime = field(default_factory=datetime.now)


@dataclass
class RunResult:
    job_title: str
    started: datetime = field(default_factory=datetime.now)
    finished: datetime | None = None
    dry_run: bool = False
    test_recipient: str = ""
    rows: list[ResultRow] = field(default_factory=list)
    aborted: str = ""
    # Set when test mode stopped the run early on purpose.
    stopped_early: str = ""

    def add(self, row: ResultRow) -> None:
        self.rows.append(row)

    @property
    def counts(self) -> Counter[Status]:
        return Counter(r.status for r in self.rows)

    @property
    def failures(self) -> int:
        c = self.counts
        return c[Status.FAILED] + c[Status.INVALID_EMAIL]

    def summary_line(self) -> str:
        c = self.counts
        parts = [f"Accounts: {len(self.rows)}"]
        for status in Status:
            if c[status]:
                parts.append(f"{status.value}: {c[status]}")
        return "   ".join(parts)


_AUDIT_REASON = {
    Status.FAILED: "SEND FAILED",
    Status.INVALID_EMAIL: "BAD ADDRESS",
    Status.NO_DATA: "NO DATA",
    Status.ALREADY_SENT: "DUPLICATE BLOCKED",
    Status.DRY_RUN: "DRY RUN",
}


def audit_lines(result: RunResult, from_address: str, startup_error: str = "") -> list[str]:
    """The auditor's table: one row for every email that did NOT reach the investor,
    with the time, account, From, To and the reason."""
    missed = [r for r in result.rows
              if r.status != Status.SENT or result.test_recipient]
    sep = "=" * 150
    thin = "-" * 150
    lines = [
        "",
        sep,
        f" AUDIT - EMAILS NOT SENT TO INVESTORS : {result.job_title}",
        f" Run started : {result.started:%d-%b-%Y %H:%M:%S}"
        f"    Finished : {(result.finished or datetime.now()):%d-%b-%Y %H:%M:%S}",
        f" Mode        : {_mode(result)}",
        sep,
    ]
    if startup_error:
        lines += [" THE RUN COULD NOT START - NO EMAIL WAS SENT TO ANY INVESTOR.",
                  f" Reason : {startup_error}", sep, ""]
        return lines

    lines += [
        f" {'#':>5}  {'TIME':<8}  {'ACCOUNT':<10}  {'FROM':<32}  {'TO':<38}  REASON NOT SENT",
        thin,
    ]
    for n, r in enumerate(missed, 1):
        lines.append(
            f" {n:>5}  {r.time:%H:%M:%S}  {r.account:<10.10}  {from_address:<32.32}  "
            f"{(r.email or '(none)'):<38.38}  {_reason(r, result)}"
        )
    if not missed:
        lines.append(" (none - every processed account was sent its email)")
    lines.append(thin)
    if result.aborted:
        lines.append(f" RUN ABORTED: {result.aborted}")
        lines.append(" Accounts after the last row above were NOT processed and NOT sent.")
    if result.stopped_early:
        lines.append(f" {result.stopped_early}")
    sent = 0 if result.test_recipient else result.counts[Status.SENT]
    lines += [
        f" Accounts processed: {len(result.rows)}    Sent to investor: {sent}    Not sent: {len(missed)}",
        f" ({result.summary_line()})",
        sep,
        "",
    ]
    return lines


def _mode(result: RunResult) -> str:
    if result.dry_run:
        return "DRY RUN - nothing was sent; emails were saved as .eml files"
    if result.test_recipient:
        return f"TEST MODE - emails went to {result.test_recipient}, not to investors"
    return "LIVE"


def _reason(r: ResultRow, result: RunResult) -> str:
    if r.status == Status.SENT:  # only listed in test mode
        return f"TEST MODE: delivered to {result.test_recipient} instead of the investor"
    label = _AUDIT_REASON.get(r.status, r.status.value)
    return f"{label}: {r.detail}" if r.detail else label


def write_reports(result: RunResult, folder: Path, job_key: str) -> tuple[Path, Path | None]:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = result.started.strftime("%Y%m%d_%H%M%S")
    txt_path = folder / f"{job_key}_{stamp}.txt"
    txt_path.write_text(_as_text(result), encoding="utf-8")
    xlsx_path: Path | None = folder / f"{job_key}_{stamp}.xlsx"
    try:
        _write_xlsx(result, xlsx_path)  # type: ignore[arg-type]
    except Exception:  # the Excel copy is a convenience; never fail the run for it
        log.exception("Could not write the Excel report")
        xlsx_path = None
    return txt_path, xlsx_path


def _as_text(result: RunResult) -> str:
    finished = result.finished or datetime.now()
    lines = [
        "INVESTMENT CORPORATION OF BANGLADESH - Investors' Department, Chattogram Branch",
        f"{result.job_title} - email report",
        f"Started : {result.started:%d-%b-%Y %H:%M:%S}    Finished : {finished:%d-%b-%Y %H:%M:%S}",
    ]
    if result.dry_run:
        lines.append("NOTE: DRY RUN - no email was sent; messages were saved as .eml files.")
    if result.test_recipient:
        lines.append(f"NOTE: TEST MODE - every email was redirected to {result.test_recipient}.")
    if result.aborted:
        lines.append(f"RUN ABORTED: {result.aborted}")
    sep = "-" * 120
    lines += [sep, f"{'ACCOUNT':<10}{'STATUS':<21}{'EMAIL':<40}{'NAME':<30}DETAIL", sep]
    for r in result.rows:
        lines.append(f"{r.account:<10}{r.status.value:<21}{r.email[:39]:<40}{r.name[:29]:<30}{r.detail}")
    lines += [sep, result.summary_line(), ""]
    return "\n".join(lines)


def _write_xlsx(result: RunResult, path: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Emails"
    header = ["Account", "Name", "Email", "Status", "Detail", "Attachment"]
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E79")
    bad = PatternFill("solid", fgColor="F8D7DA")
    for r in result.rows:
        ws.append([r.account, r.name, r.email, r.status.value, r.detail, r.attachment])
        if r.status in (Status.FAILED, Status.INVALID_EMAIL):
            for cell in ws[ws.max_row]:
                cell.fill = bad
    widths = [12, 34, 38, 22, 60, 24]
    for i, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(path)
