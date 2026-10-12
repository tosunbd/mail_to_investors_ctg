"""The two email jobs: Portfolio Statement and Sale/Purchase Confirmation."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path
from typing import Any, Callable

import jinja2

from icb_mailer.config import JobSettings, Settings
from icb_mailer.journal import SentJournal
from icb_mailer.mailer import Mailer, PermanentSendError, SmtpLoginError, new_message, split_addresses
from icb_mailer.pdf_portfolio import build_portfolio_pdf
from icb_mailer.reporting import ResultRow, RunResult, Status
from icb_mailer.repository import Repository

log = logging.getLogger(__name__)

PORTFOLIO = "portfolio"
SALE_PURCHASE = "sale_purchase"
JOB_TITLES = {
    PORTFOLIO: "Portfolio Statement",
    SALE_PURCHASE: "Sale/Purchase Confirmation",
}

# Stop the run when this many accounts in a row fail: something systemic is
# wrong (database gone, relay blocking us) and hammering on helps nobody.
MAX_CONSECUTIVE_FAILURES = 10


class RunAborted(Exception):
    pass


@dataclass
class RunOptions:
    dry_run: bool = False
    test_recipient: str = ""
    accounts: set[str] = field(default_factory=set)
    limit: int = 0
    force: bool = False
    # Test mode without an account list: stop after this many emails
    # (accounts with no data do not count, so the first real statement goes out).
    test_max_emails: int = 1


@dataclass
class _Outgoing:
    """One email ready to send."""

    msg: EmailMessage
    recipients: list[str]
    attachment: str = ""


class JobRunner:
    def __init__(
        self,
        job: str,
        settings: Settings,
        repo: Repository,
        journal: SentJournal,
        mailer: Mailer | None,
        options: RunOptions,
    ):
        self.job = job
        self.settings = settings
        self.repo = repo
        self.journal = journal
        self.mailer = mailer
        self.options = options
        self.started = datetime.now()
        self.result = RunResult(
            job_title=JOB_TITLES[job],
            started=self.started,
            dry_run=options.dry_run,
            test_recipient=options.test_recipient,
        )
        self._templates = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(settings.paths.templates)),
            autoescape=jinja2.select_autoescape(["html"]),
            undefined=jinja2.ChainableUndefined,
        )
        logo = settings.paths.logo
        self._logo_bytes = logo.read_bytes() if logo.exists() else None
        self._sent = 0
        self._produced = 0  # emails sent or saved (dry run) in this run
        self._consecutive_failures = 0

    @property
    def job_settings(self) -> JobSettings:
        return self.settings.portfolio if self.job == PORTFOLIO else self.settings.sale_purchase

    # ------------------------------------------------------------- driver --
    def run(self) -> RunResult:
        try:
            if self.job == PORTFOLIO:
                self._run_portfolio()
            else:
                self._run_sale_purchase()
        except (SmtpLoginError, RunAborted) as exc:
            self.result.aborted = str(exc)
            log.error("RUN ABORTED: %s", exc)
        except KeyboardInterrupt:
            self.result.aborted = "Stopped by the operator (Ctrl+C)."
            log.warning("Stopped by the operator - already sent emails are recorded; run again to continue.")
        finally:
            self.result.finished = datetime.now()
        return self.result

    def _select(self, rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
        if self.options.accounts:
            rows = [r for r in rows if _key(r[0]) in self.options.accounts]
            missing = self.options.accounts - {_key(r[0]) for r in rows}
            for account in sorted(missing):
                self.result.add(ResultRow(account, "", "", Status.NO_DATA,
                                          "Account not found among accounts with an email address"))
        if self.options.limit:
            rows = rows[: self.options.limit]
        if self._test_cap:
            log.warning("TEST MODE: stopping after %s email(s) to %s.",
                        self._test_cap, self.options.test_recipient)
        return rows

    @property
    def _test_cap(self) -> int:
        o = self.options
        if o.test_recipient and not o.accounts and not o.limit:
            return max(1, o.test_max_emails)
        return 0

    def _test_cap_reached(self) -> bool:
        if self._test_cap and self._produced >= self._test_cap:
            self.result.stopped_early = (
                f"TEST MODE: run stopped after {self._produced} test email(s) on purpose; "
                "the remaining accounts were not processed.")
            log.warning("TEST MODE: %s test email(s) done - stopping. "
                        "Set test_recipient = \"\" in settings.toml to send to all investors.",
                        self._produced)
            return True
        return False

    # ---------------------------------------------------------- portfolio --
    def _run_portfolio(self) -> None:
        profiles = self._select(self.repo.portfolio_profiles())
        log.info("Portfolio Statement: %s account(s) with an email address to process", len(profiles))
        template = self._templates.get_template("pv_stat_template.html")
        pdf_dir = self.settings.paths.pdf / self.started.strftime("%Y-%m-%d")

        for index, profile in enumerate(profiles, 1):
            account, name, raw_email = _key(profile[0]), str(profile[1] or ""), str(profile[3] or "")

            def build(to: list[str]) -> _Outgoing | None:
                holdings = self.repo.portfolio_holdings(profile[0])
                if not holdings:
                    return None
                footer_raw, footer_text = self.repo.portfolio_footer(profile[0])
                sums = self.repo.portfolio_sums(profile[0])

                logo = self.settings.paths.logo if self.job_settings.include_logo else None
                pdf = build_portfolio_pdf(profile, holdings, footer_text, logo=logo)
                pdf_dir.mkdir(parents=True, exist_ok=True)
                (pdf_dir / f"{account}.pdf").write_bytes(pdf)

                logo_cid = self._logo_cid()
                html = template.render(
                    title="Portfolio Statement",
                    transactions=holdings,
                    profiles=profile,
                    foters=footer_raw,
                    sums=sums,
                    logo_cid=logo_cid[1:-1] if logo_cid else None,
                )
                plain = (
                    "Dear Account Holder,\n\n"
                    f"Please find attached the Portfolio Statement of Account No. {account} "
                    f"as on {profile[2]}.\n\n"
                    "This is an automatically generated message, please do not reply.\n"
                    f"Investment Corporation of Bangladesh\nInvestors' Department, {profile[4] or ''}\n"
                )
                msg = self._message(to, account, plain, html, logo_cid)
                name_ = self.job_settings.attachment_name or f"{account}.pdf"
                msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=name_)
                return _Outgoing(msg, to, attachment=str(pdf_dir / f"{account}.pdf"))

            self._deliver(index, len(profiles), account, name, raw_email, build)
            if self._test_cap_reached():
                break

    # ------------------------------------------------------ sale/purchase --
    def _run_sale_purchase(self) -> None:
        recipients = self._select(self.repo.sale_purchase_recipients())
        log.info("Sale/Purchase Confirmation: %s account(s) to process", len(recipients))
        template = self._templates.get_template("sp_confirm_template.html")

        for index, row in enumerate(recipients, 1):
            account, raw_email = _key(row[0]), str(row[1] or "")

            def build(to: list[str]) -> _Outgoing | None:
                transactions = self.repo.sale_purchase_transactions(row[0])
                if not transactions:
                    return None
                logo_cid = self._logo_cid()
                html = template.render(
                    title="Sale/Purchase Confirmation",
                    transactions=transactions,
                    logo_cid=logo_cid[1:-1] if logo_cid else None,
                )
                lines = [
                    "Dear Account Holder,",
                    "Sale/Purchase Confirmation Statement is given below.",
                    "",
                    f"{'Account':<10}{'Company':<40}{'Date':<12}{'Trans.':<8}{'Shares':>10}{'Rate':>12}",
                ]
                for t in transactions:
                    when = t[2].strftime("%d/%m/%Y") if hasattr(t[2], "strftime") else str(t[2])
                    lines.append(f"{t[0]!s:<10}{str(t[1])[:39]:<40}{when:<12}{t[3]!s:<8}{t[5]!s:>10}{t[4]!s:>12}")
                lines += ["", "This is an automatically generated message, please do not reply.",
                          "Investment Corporation of Bangladesh"]
                msg = self._message(to, account, "\n".join(lines) + "\n", html, logo_cid)
                return _Outgoing(msg, to)

            self._deliver(index, len(recipients), account, "", raw_email, build)
            if self._test_cap_reached():
                break

    # ------------------------------------------------------------- shared --
    def _logo_cid(self) -> str | None:
        if self._logo_bytes and self.job_settings.include_logo:
            return make_msgid(domain="icb.gov.bd")
        return None

    def _message(self, to: list[str], account: str, plain: str, html: str, logo_cid: str | None) -> EmailMessage:
        subject = self.job_settings.subject.replace("{account}", account)
        msg = new_message(self.settings.smtp.from_address, to, subject)
        msg.set_content(plain)
        msg.add_alternative(html, subtype="html")
        if logo_cid and self._logo_bytes:
            html_part = msg.get_payload()[1]
            html_part.add_related(self._logo_bytes, "image", "jpeg", cid=logo_cid, filename="icb_logo.jpg")
        return msg

    def _deliver(
        self,
        index: int,
        total: int,
        account: str,
        name: str,
        raw_email: str,
        build: Callable[[list[str]], _Outgoing | None],
    ) -> None:
        prefix = f"[{index}/{total}] {account}"
        real_to, invalid = split_addresses(raw_email)
        shown_email = ", ".join(real_to) or raw_email

        if (not self.options.dry_run and not self.options.test_recipient and not self.options.force
                and self.settings.run.skip_already_sent_today
                and self.journal.already_sent(self.job, account)):
            self.result.add(ResultRow(account, name, shown_email, Status.ALREADY_SENT,
                                      "Already emailed today (see sent journal) - not sent twice. "
                                      "Use --force to resend."))
            log.info("%s : already sent today - skipped", prefix)
            return

        if self.options.test_recipient:
            to = [self.options.test_recipient]
        elif real_to:
            to = real_to
        else:
            reason = f"Not a valid email address: {raw_email!r}" if raw_email.strip() else "No email address registered"
            self.result.add(ResultRow(account, name, raw_email, Status.INVALID_EMAIL, reason))
            log.warning("%s : %s - skipped", prefix, reason)
            return

        try:
            outgoing = build(to)
            if outgoing is None:
                self.result.add(ResultRow(account, name, shown_email, Status.NO_DATA,
                                          "No data for this account - nothing to send"))
                log.info("%s : no data - skipped", prefix)
                return

            detail = f"Invalid address(es) ignored: {', '.join(invalid)}" if invalid else ""
            if self.options.test_recipient:
                detail = f"TEST: sent to {self.options.test_recipient} instead. {detail}".strip()

            if self.options.dry_run:
                eml_dir = self.settings.paths.dry_run / f"{self.job}_{self.started:%Y%m%d_%H%M%S}"
                eml_dir.mkdir(parents=True, exist_ok=True)
                (eml_dir / f"{account}.eml").write_bytes(outgoing.msg.as_bytes())
                self.result.add(ResultRow(account, name, shown_email, Status.DRY_RUN,
                                          f"Dry run switch is on - email built and saved as "
                                          f"{eml_dir.name}/{account}.eml, not sent. {detail}".strip(),
                                          outgoing.attachment))
                log.info("%s -> %s : DRY RUN, saved .eml", prefix, ", ".join(to))
                self._produced += 1
                self._consecutive_failures = 0
                return

            assert self.mailer is not None
            self.mailer.send(outgoing.msg, outgoing.recipients)
            self._sent += 1
            self._produced += 1
            self._consecutive_failures = 0
            if not self.options.test_recipient:
                self.journal.record(self.job, account, ", ".join(to), outgoing.msg["Message-ID"])
            self.result.add(ResultRow(account, name, ", ".join(to), Status.SENT, detail, outgoing.attachment))
            log.info("%s -> %s : SENT", prefix, ", ".join(to))
            self.mailer.maybe_pause_for_batch(self._sent)

        except (SmtpLoginError, KeyboardInterrupt):
            raise
        except PermanentSendError as exc:
            # The address is bad, not the system: this does not count toward the abort limit.
            self.result.add(ResultRow(account, name, shown_email, Status.FAILED,
                                      f"Mail server rejected the email: {exc}"))
            log.error("%s : NOT SENT - %s", prefix, exc)
        except Exception as exc:
            self._consecutive_failures += 1
            self.result.add(ResultRow(account, name, shown_email, Status.FAILED,
                                      f"Error while building/sending - {type(exc).__name__}: {exc}"
                                      .splitlines()[0]))
            log.exception("%s : FAILED", prefix)
            if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                raise RunAborted(
                    f"{MAX_CONSECUTIVE_FAILURES} accounts failed in a row - last error: {exc}"
                ) from exc


def _key(account: Any) -> str:
    return str(account).strip()


def purge_old_output(settings: Settings) -> None:
    """Delete dated PDF folders and dry-run folders older than keep_output_days."""
    days = settings.run.keep_output_days
    if days <= 0:
        return
    cutoff = date.today() - timedelta(days=days)
    for base in (settings.paths.pdf, settings.paths.dry_run):
        if not base.is_dir():
            continue
        for folder in base.iterdir():
            if folder.is_dir() and _folder_date(folder) and _folder_date(folder) < cutoff:
                shutil.rmtree(folder, ignore_errors=True)
                log.debug("Purged old output folder %s", folder)


def _folder_date(folder: Path) -> date | None:
    stamp = folder.name.rsplit("_", 2)
    for candidate in (folder.name, stamp[-2] if len(stamp) >= 2 else ""):
        for fmt in ("%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None
