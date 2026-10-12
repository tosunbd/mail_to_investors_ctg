"""Command line entry point.

    python -m icb_mailer portfolio        send Portfolio Statements
    python -m icb_mailer sale-purchase    send Sale/Purchase Confirmations
    python -m icb_mailer check            test database + mail server, send nothing
    python -m icb_mailer sample           mail ONE statement built from built-in sample
                                          data to the test address (no database needed)

Common options (portfolio / sale-purchase):
    --dry-run            build everything, save emails as .eml files, send nothing
    --test-to ADDRESS    send every email to ADDRESS instead of the investor
    --accounts 101,102   only these account numbers
    --limit N            only the first N accounts
    --force              resend even if the account already got today's email

Exit codes (useful for schedulers): 0 = all good, 1 = some emails failed,
2 = run aborted / configuration or connection error, 3 = already running.
"""

from __future__ import annotations

import argparse
import logging
import platform
import struct
import sys
from pathlib import Path

from icb_mailer import __version__
from icb_mailer.config import ConfigError, Settings, load_settings
from icb_mailer.log_setup import PLAIN, setup_logging

log = logging.getLogger("icb_mailer")

EXIT_OK, EXIT_PARTIAL, EXIT_FATAL, EXIT_LOCKED = 0, 1, 2, 3


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="icb_mailer",
        description="ICB Investors' Department (Chattogram) - investor email service",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def job_options(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--dry-run", action="store_true", help="send nothing, save .eml files instead")
        sp.add_argument("--test-to", metavar="EMAIL", default="", help="redirect every email to this address")
        sp.add_argument("--accounts", default="", help="comma separated account numbers to process")
        sp.add_argument("--limit", type=int, default=0, help="process at most N accounts")
        sp.add_argument("--force", action="store_true", help="resend even if already sent today")
        sp.add_argument("--auto", action="store_true", help="non-interactive (used by the scheduler)")
        sp.add_argument("-v", "--verbose", action="store_true")

    job_options(sub.add_parser("portfolio", help="send Portfolio Statements (HTML + PDF)"))
    job_options(sub.add_parser("sale-purchase", help="send Sale/Purchase Confirmations"))
    smp = sub.add_parser("sample", help="email one sample statement (sample data, no database)")
    smp.add_argument("--to", default="", help="address (default: test_recipient in settings.toml)")
    smp.add_argument("--auto", action="store_true")
    smp.add_argument("-v", "--verbose", action="store_true")
    chk = sub.add_parser("check", help="test the database and mail server connections")
    chk.add_argument("--auto", action="store_true")
    chk.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"CONFIGURATION ERROR: {exc}", file=sys.stderr)
        return EXIT_FATAL

    log_file = setup_logging(settings.paths.logs, verbose=args.verbose)
    log.info(
        "ICB investor mailer %s | Python %s (%s-bit) on %s | log: %s",
        __version__, platform.python_version(), struct.calcsize("P") * 8, platform.system(), log_file,
    )
    try:
        if args.command == "check":
            return _check(settings)
        if args.command == "sample":
            return _sample(settings, args.to)
        job = "portfolio" if args.command == "portfolio" else "sale_purchase"
        return _run_job(job, settings, args)
    except Exception:
        log.exception("Unexpected error")
        return EXIT_FATAL


# ---------------------------------------------------------------- commands --
def _run_job(job: str, settings: Settings, args: argparse.Namespace) -> int:
    from icb_mailer.db import DatabaseError, open_connection
    from icb_mailer.jobs import JOB_TITLES, JobRunner, RunOptions, purge_old_output
    from icb_mailer.journal import SentJournal
    from icb_mailer.locking import AlreadyRunningError, RunLock
    from icb_mailer.mailer import Mailer, SmtpLoginError, is_valid_email
    from icb_mailer.reporting import RunResult, write_reports
    from icb_mailer.repository import OracleRepository

    options = RunOptions(
        dry_run=args.dry_run or settings.run.dry_run,
        test_recipient=(args.test_to or settings.run.test_recipient).strip().lower(),
        accounts={a.strip() for a in args.accounts.split(",") if a.strip()},
        limit=max(0, args.limit),
        force=args.force,
        test_max_emails=settings.run.test_max_emails,
    )

    def cannot_start(reason: str) -> None:
        log.error("%s", reason)
        result = RunResult(job_title=JOB_TITLES[job], dry_run=options.dry_run,
                           test_recipient=options.test_recipient)
        _log_audit(result, settings, startup_error=reason)

    if options.test_recipient and not is_valid_email(options.test_recipient):
        cannot_start(f"Test address {options.test_recipient!r} is not a valid email address")
        return EXIT_FATAL
    if options.dry_run:
        log.warning("DRY RUN - nothing will be sent; emails are saved as .eml files in %s",
                    settings.paths.dry_run)
    if options.test_recipient:
        log.warning("TEST MODE - every email goes to %s, not to investors", options.test_recipient)

    try:
        with RunLock(settings.paths.state / f"{job}.lock"):
            purge_old_output(settings)
            journal = SentJournal(settings.paths.state / "sent_journal.sqlite3")
            try:
                with open_connection(settings.database) as conn:
                    repo = OracleRepository(conn)
                    if options.dry_run:
                        result = JobRunner(job, settings, repo, journal, None, options).run()
                    else:
                        with Mailer(settings.smtp) as mailer:
                            result = JobRunner(job, settings, repo, journal, mailer, options).run()
            finally:
                journal.close()
    except AlreadyRunningError as exc:
        cannot_start(f"ALREADY RUNNING: {exc}")
        return EXIT_LOCKED
    except DatabaseError as exc:
        cannot_start(f"DATABASE: {exc}")
        return EXIT_FATAL
    except SmtpLoginError as exc:
        cannot_start(f"MAIL SERVER: {exc}")
        return EXIT_FATAL

    _log_audit(result, settings)
    txt, xlsx = write_reports(result, settings.paths.reports, job)
    log.info("=" * 70)
    log.info("%s finished. %s", result.job_title, result.summary_line())
    log.info("Report: %s", txt)
    if xlsx:
        log.info("Excel : %s", xlsx)
    if result.aborted:
        log.error("The run was ABORTED: %s", result.aborted)
    log.info("=" * 70)

    if settings.run.admin_email and not options.dry_run:
        _send_admin_summary(settings, result, txt)

    if result.aborted:
        return EXIT_FATAL
    return EXIT_PARTIAL if result.failures else EXIT_OK


def _log_audit(result, settings: Settings, startup_error: str = "") -> None:
    """Write the auditor's 'not sent' table into this run's log file."""
    from icb_mailer.reporting import audit_lines

    audit = logging.getLogger("icb_mailer.audit")
    for line in audit_lines(result, settings.smtp.from_address, startup_error):
        audit.info(line, extra=PLAIN)


def _send_admin_summary(settings: Settings, result, report_path: Path) -> None:
    """Best effort: mail the run report to the administrator."""
    from icb_mailer.mailer import Mailer, new_message

    try:
        status = "ABORTED" if result.aborted else ("with failures" if result.failures else "OK")
        msg = new_message(
            settings.smtp.from_address,
            [settings.run.admin_email],
            f"[ICB CTG mailer] {result.job_title} {status} - {result.started:%d-%b-%Y %H:%M}",
        )
        msg.set_content(result.summary_line() + ("\n\nABORTED: " + result.aborted if result.aborted else "")
                        + "\n\nThe full report is attached.\n")
        msg.add_attachment(report_path.read_bytes(), maintype="text", subtype="plain",
                           filename=report_path.name)
        with Mailer(settings.smtp) as mailer:
            mailer.send(msg, [settings.run.admin_email])
        log.info("Summary sent to %s", settings.run.admin_email)
    except Exception as exc:
        log.warning("Could not send the summary to %s: %s", settings.run.admin_email, exc)


def _check(settings: Settings) -> int:
    from icb_mailer.db import DatabaseError, open_connection
    from icb_mailer.mailer import Mailer, SmtpLoginError
    from icb_mailer.repository import count_rows

    ok = True
    log.info("Checking database %s ...", settings.database.dsn_display)
    try:
        with open_connection(settings.database) as conn:
            counts = count_rows(conn, [
                "invest.inv_profile",
                "invest.accnt_mobile_info",
                "invest.EMAIL_PV_STAT_DATA",
                "invest.EMAIL_PV_STAT_FOOTER",
                "invest.V_SALE_BUY_CONFIRMATION",
            ])
            for table, value in counts.items():
                log.info("   %-34s %s", table, value)
                ok = ok and not str(value).startswith("ERROR")
        log.info("Database: OK")
    except DatabaseError as exc:
        ok = False
        log.error("Database: FAILED - %s", exc)

    log.info("Checking mail server %s:%s as %s ...", settings.smtp.host, settings.smtp.port, settings.smtp.user)
    try:
        with Mailer(settings.smtp):
            pass
        log.info("Mail server: OK (login accepted, nothing sent)")
    except SmtpLoginError as exc:
        ok = False
        log.error("Mail server: FAILED - %s", exc)

    log.info("RESULT: %s", "ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED")
    return EXIT_OK if ok else EXIT_FATAL


def _sample(settings: Settings, to: str) -> int:
    """Send one Portfolio Statement built from sample data - proves the mail path,
    the layout and the PDF without touching the database or any investor."""
    import dataclasses

    from icb_mailer.jobs import PORTFOLIO, JobRunner, RunOptions
    from icb_mailer.journal import SentJournal
    from icb_mailer.mailer import Mailer, SmtpLoginError, is_valid_email
    from icb_mailer.reporting import Status
    from tests.fakes import FakeRepository

    address = (to or settings.run.test_recipient).strip()
    if not is_valid_email(address):
        log.error("No valid address: use --to someone@example.com or set test_recipient in settings.toml")
        return EXIT_FATAL
    settings = dataclasses.replace(
        settings,
        portfolio=dataclasses.replace(
            settings.portfolio, subject="[SAMPLE - test data] " + settings.portfolio.subject
        ),
        paths=dataclasses.replace(settings.paths, output=settings.paths.output / "sample"),
    )
    journal = SentJournal(settings.paths.state / "sample_journal.sqlite3")
    try:
        with Mailer(settings.smtp) as mailer:
            result = JobRunner(PORTFOLIO, settings, FakeRepository(), journal, mailer,
                               RunOptions(test_recipient=address, test_max_emails=1)).run()
    except SmtpLoginError as exc:
        log.error("MAIL SERVER: %s", exc)
        return EXIT_FATAL
    finally:
        journal.close()
    if result.counts[Status.SENT]:
        log.info("Sample statement sent to %s - check the inbox (and the Spam folder).", address)
        return EXIT_OK
    log.error("Sample was NOT sent: %s", result.aborted or (result.rows[0].detail if result.rows else "unknown"))
    return EXIT_FATAL
