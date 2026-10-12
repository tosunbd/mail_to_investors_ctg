"""SMTP sending.

Lessons carried over from the head-office .NET service (MailKitEmailService):
the BCC relay (smtp.bcc.gov.bd) rate-limits per client. Opening a new
connection + STARTTLS + AUTH for every message trips its limiter, which then
answers "4.7.1 try again later", rejects a correct login with 535, or drops
the connection. So:

  * ONE authenticated connection is opened per run and reused;
  * a minimum gap is kept between two messages (delay_between_emails_ms);
  * transient failures are retried with exponential back-off + jitter, after
    dropping the (now unusable) connection;
  * permanent 5xx rejections of a recipient are NOT retried.
"""

from __future__ import annotations

import logging
import random
import re
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from icb_mailer.config import SmtpSettings

log = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


class SmtpLoginError(Exception):
    """The relay keeps rejecting the configured account - abort the run."""


class PermanentSendError(Exception):
    """The message can never be delivered as addressed (bad recipient etc.)."""


def is_valid_email(address: str) -> bool:
    return bool(_EMAIL_RE.match(address.strip()))


def split_addresses(raw: str | None) -> tuple[list[str], list[str]]:
    """Split the EMAIL_NO column into (valid, invalid) addresses.

    Some investors have several addresses in one field, separated by
    comma, semicolon or spaces.
    """
    if not raw:
        return [], []
    valid: list[str] = []
    invalid: list[str] = []
    for part in re.split(r"[,;\s]+", raw.strip()):
        if not part:
            continue
        address = part.strip().strip("<>").lower()
        if is_valid_email(address):
            if address not in valid:
                valid.append(address)
        else:
            invalid.append(part)
    return valid, invalid


def new_message(sender: str, to: list[str], subject: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg["Date"] = formatdate(localtime=True)
    domain = parseaddr(sender)[1].partition("@")[2] or "icb.gov.bd"
    msg["Message-ID"] = make_msgid(domain=domain)
    msg["Auto-Submitted"] = "auto-generated"
    return msg


class Mailer:
    """Context manager holding one SMTP connection for the whole run."""

    def __init__(self, settings: SmtpSettings):
        self.s = settings
        self._client: smtplib.SMTP | None = None
        self._last_send = 0.0
        self._in_login = False

    # ---------------------------------------------------------- connection --
    def __enter__(self) -> "Mailer":
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def connect(self) -> None:
        """Open + authenticate, retrying transient failures. Fail fast on a bad login."""
        attempts = self.s.max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                self._open()
                return
            except smtplib.SMTPAuthenticationError as exc:
                self._drop()
                # 535 is sometimes the relay throttling, so give it the retries,
                # but never more than 3 tries: repeated bad logins can lock the account.
                if attempt >= min(attempts, 3):
                    raise SmtpLoginError(
                        f"SMTP login rejected for {self.s.user} at {self.s.host}:{self.s.port} "
                        f"({exc.smtp_code} {exc.smtp_error!r}). Check SMTP_USER / SMTP_PASSWORD in .env."
                    ) from exc
                self._backoff(attempt, f"SMTP login rejected ({exc.smtp_code})")
            except (smtplib.SMTPException, OSError) as exc:
                self._drop()
                # A connection dropped *during* AUTH may be the relay refusing the
                # login, so it gets the same 3-try cap as a 535.
                limit = min(attempts, 3) if self._in_login else attempts
                if attempt >= limit:
                    stage = "during login" if self._in_login else "while connecting"
                    raise SmtpLoginError(
                        f"SMTP server {self.s.host}:{self.s.port} closed the connection {stage}: {exc}. "
                        "If this repeats, check SMTP_PASSWORD in .env, wait 15-30 minutes "
                        "(the relay may be blocking this PC for too many attempts) and try again."
                    ) from exc
                self._backoff(attempt, f"SMTP connect failed {stage_of(self._in_login)}: {exc}")

    def _open(self) -> None:
        self._in_login = False
        context = ssl.create_default_context()
        if self.s.security == "ssl":
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                self.s.host, self.s.port, timeout=self.s.timeout_seconds, context=context
            )
        else:
            client = smtplib.SMTP(self.s.host, self.s.port, timeout=self.s.timeout_seconds)
        try:
            client.ehlo()
            if self.s.security == "starttls":
                client.starttls(context=context)
                client.ehlo()
            if self.s.user:
                self._in_login = True
                client.login(self.s.user, self.s.password)
        except BaseException:
            client.close()
            raise
        self._client = client
        log.info("SMTP connected to %s:%s as %s", self.s.host, self.s.port, self.s.user)

    def _drop(self) -> None:
        if self._client is None:
            return
        try:
            self._client.quit()
        except (smtplib.SMTPException, OSError):
            try:
                self._client.close()
            except OSError:
                pass
        self._client = None

    def close(self) -> None:
        self._drop()

    def _backoff(self, attempt: int, reason: str) -> None:
        delay = min(
            self.s.retry_max_delay_seconds,
            self.s.retry_initial_delay_seconds * (2 ** (attempt - 1)),
        )
        delay *= random.uniform(0.8, 1.2)
        log.warning("%s - retry %s in %.1fs", reason, attempt, delay)
        time.sleep(delay)

    # --------------------------------------------------------------- send --
    def _pace(self) -> None:
        gap = self.s.delay_between_emails_ms / 1000.0
        wait = self._last_send + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def send(self, msg: EmailMessage, recipients: list[str]) -> None:
        """Send one message; raises PermanentSendError or the last transient error."""
        attempts = self.s.max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                if self._client is None:
                    self.connect()
                self._pace()
                assert self._client is not None
                refused = self._client.send_message(msg, from_addr=self.s.user, to_addrs=recipients)
                self._last_send = time.monotonic()
                if refused:
                    log.warning("Some recipients were refused: %s", refused)
                return
            except smtplib.SMTPRecipientsRefused as exc:
                codes = {code for code, _ in exc.recipients.values()}
                if all(400 <= c < 500 for c in codes) and attempt < attempts:
                    self._backoff(attempt, f"recipient temporarily refused {exc.recipients}")
                    continue
                raise PermanentSendError(f"Recipient refused: {_describe(exc.recipients)}") from exc
            except (smtplib.SMTPSenderRefused, smtplib.SMTPDataError) as exc:
                if 400 <= exc.smtp_code < 500 and attempt < attempts:
                    self._drop()
                    self._backoff(attempt, f"temporary SMTP error {exc.smtp_code}")
                    continue
                raise PermanentSendError(f"{exc.smtp_code} {_decode(exc.smtp_error)}") from exc
            except SmtpLoginError:
                raise
            except (smtplib.SMTPException, OSError) as exc:
                # Disconnected, timeout, reset, auth-throttling ... the
                # connection is unusable: drop it and log in again.
                self._drop()
                if attempt >= attempts:
                    raise
                self._backoff(attempt, f"SMTP error {type(exc).__name__}: {exc}")

    def maybe_pause_for_batch(self, sent_count: int) -> None:
        if self.s.batch_size and sent_count and sent_count % self.s.batch_size == 0:
            log.info(
                "Sent %s emails - pausing %ss so the relay does not throttle us",
                sent_count, self.s.batch_pause_seconds,
            )
            # Close the connection during the pause rather than letting it time out.
            self._drop()
            time.sleep(self.s.batch_pause_seconds)


def stage_of(in_login: bool) -> str:
    return "during login" if in_login else "while connecting"


def _decode(value: bytes | str) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def _describe(recipients: dict[str, tuple[int, bytes]]) -> str:
    return "; ".join(f"{addr}: {code} {_decode(msg)}" for addr, (code, msg) in recipients.items())
