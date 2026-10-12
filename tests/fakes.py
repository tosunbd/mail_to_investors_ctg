"""Test doubles: sample data in the shape of the Oracle views, and a tiny SMTP server."""

from __future__ import annotations

import socketserver
import threading
from datetime import datetime
from email import message_from_bytes, policy
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

PROFILES = [
    # accnt_no, name, as-on, email_no, branch
    (1001, "MD. RAHIM UDDIN", "12/10/2026", "rahim@example.com", "Chattogram"),
    (1002, "FATEMA BEGUM (JOINT)", "12/10/2026", "fatema@example.com; f.begum@example.org", "Chattogram"),
    (1003, "NO HOLDINGS LTD", "12/10/2026", "empty@example.com", "Chattogram"),
    (1004, "BAD ADDRESS", "12/10/2026", "not-an-email", "Chattogram"),
]

HOLDINGS = {
    1001: [
        ("BEXIMCO PHARMACEUTICALS LTD.", "A", 1500, 145.2333, 152.1, 217850.0, 228150.0, 10300.0),
        ("BRAC BANK LTD.", " ", 2000, 38.5, 36.9, 77000.0, 73800.0, -3200.0),
        ("GRAMEENPHONE LTD.", "A", 120, 290.0, 301.25, 34800.0, 36150.0, 1350.0),
    ],
    1002: [
        (f"COMPANY NUMBER {i:03d} LIMITED", "B", 10 * i, 10.0 + i, 11.0 + i,
         10 * i * (10.0 + i), 10 * i * (11.0 + i), 10 * i * 1.0)
        for i in range(1, 121)  # long enough to need several pages
    ],
}

FOOTER_RAW = (500000, 12345.5, 338100.0, 330000.0, 150000.0, 52.25, 0)
FOOTER_TEXT = ("     500,000.00", "      12,345.50", "     338,100.00", "     330,000.00",
               "     150,000.00", "          52.25", "           0.00")

SP_RECIPIENTS = [("1001", "rahim@example.com"), ("1004", "")]
SP_TRANSACTIONS = {
    1001: [
        ("1001", "BEXIMCO PHARMACEUTICALS LTD.", datetime(2026, 10, 12), "BUY", 151.5, 500),
        ("1001", "GRAMEENPHONE LTD.", datetime(2026, 10, 12), "SALE", 302.0, 100),
    ],
}


class FakeRepository:
    def portfolio_profiles(self):
        return list(PROFILES)

    def portfolio_holdings(self, account):
        return list(HOLDINGS.get(int(account), []))

    def portfolio_footer(self, account):
        return FOOTER_RAW, FOOTER_TEXT

    def portfolio_sums(self, account):
        rows = HOLDINGS.get(int(account), [])
        return (sum(r[2] for r in rows), sum(r[5] for r in rows), sum(r[6] for r in rows), sum(r[7] for r in rows))

    def sale_purchase_recipients(self):
        return list(SP_RECIPIENTS)

    def sale_purchase_transactions(self, account):
        return list(SP_TRANSACTIONS.get(int(account), []))


def write_project(tmp: Path, smtp_port: int = 2525, extra_run: str = "") -> Path:
    """A throw-away project root using the real templates and logo."""
    (tmp / "settings.toml").write_text(f"""
[database]
host = "127.0.0.1"
service_name = "ORCL"
mode = "thin"
[smtp]
host = "127.0.0.1"
port = {smtp_port}
security = "none"
from_address = "No Reply <no-reply@icb.gov.bd>"
timeout_seconds = 10
delay_between_emails_ms = 0
batch_size = 0
max_retries = 3
retry_initial_delay_seconds = 0.01
retry_max_delay_seconds = 0.05
[portfolio]
subject = "Portfolio Statement From ICB"
attachment_name = "Portfolio.pdf"
include_logo = true
[sale_purchase]
subject = "Sale/Purchase Confirmation From ICB"
[run]
keep_output_days = 60
{extra_run}
[paths]
templates = '{PROJECT / "templates"}'
assets = '{PROJECT / "assets"}'
output = '{tmp / "output"}'
logs = '{tmp / "logs"}'
""", encoding="utf-8")
    (tmp / ".env").write_text(
        "DB_USER=u\nDB_PASSWORD=p\nSMTP_USER=mailer@example.com\nSMTP_PASSWORD=secret\n", encoding="utf-8"
    )
    return tmp


# ------------------------------------------------------------- SMTP server --
class SmtpServer:
    """Minimal SMTP server (EHLO, AUTH PLAIN/LOGIN, MAIL, RCPT, DATA, RSET, QUIT).

    `script` lets a test inject failures: a list of (command, reply) consumed in
    order, e.g. [("DATA_END", "421 4.7.0 try again later")] makes the first
    message fail with a temporary error and drop the connection.
    """

    def __init__(self, password: str = "secret"):
        self.password = password
        self.messages: list[tuple[str, list[str], object]] = []
        self.logins = 0
        self.connections = 0
        self.script: list[tuple[str, str]] = []
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def reply(self, line: str) -> None:
                self.wfile.write((line + "\r\n").encode())

            def injected(self, command: str) -> str | None:
                if outer.script and outer.script[0][0] == command:
                    return outer.script.pop(0)[1]
                return None

            def handle(self) -> None:
                outer.connections += 1
                self.reply("220 fake ESMTP")
                sender, rcpts = "", []
                while True:
                    raw = self.rfile.readline()
                    if not raw:
                        return
                    line = raw.decode().rstrip("\r\n")
                    cmd = line.split(" ")[0].upper()
                    if cmd in ("EHLO", "HELO"):
                        self.wfile.write(b"250-fake\r\n250-AUTH PLAIN LOGIN\r\n250 8BITMIME\r\n")
                    elif cmd == "AUTH":
                        import base64
                        parts = line.split(" ")
                        if parts[1].upper() == "PLAIN":
                            blob = parts[2] if len(parts) > 2 else None
                            if blob is None:
                                self.reply("334 ")
                                blob = self.rfile.readline().decode().strip()
                            pw = base64.b64decode(blob).split(b"\0")[2].decode()
                        else:
                            self.reply("334 VXNlcm5hbWU6")
                            self.rfile.readline()
                            self.reply("334 UGFzc3dvcmQ6")
                            pw = base64.b64decode(self.rfile.readline().strip()).decode()
                        forced = self.injected("AUTH")
                        if forced == "DROP":
                            return  # hang up mid-login, like the BCC relay does
                        if forced:
                            self.reply(forced)
                        elif pw == outer.password:
                            outer.logins += 1
                            self.reply("235 ok")
                        else:
                            self.reply("535 5.7.8 authentication failed")
                    elif cmd == "MAIL":
                        sender, rcpts = line.split(":", 1)[1].strip().split(" ")[0].strip("<>"), []
                        self.reply("250 ok")
                    elif cmd == "RCPT":
                        addr = line.split(":", 1)[1].strip().strip("<>")
                        forced = self.injected("RCPT")
                        if forced:
                            self.reply(forced)
                        else:
                            rcpts.append(addr)
                            self.reply("250 ok")
                    elif cmd == "DATA":
                        self.reply("354 go")
                        data = b""
                        while True:
                            chunk = self.rfile.readline()
                            if chunk in (b".\r\n", b".\n", b""):
                                break
                            data += chunk[1:] if chunk.startswith(b"..") else chunk
                        forced = self.injected("DATA_END")
                        if forced:
                            self.reply(forced)
                            if forced.startswith("421"):
                                return  # 421 = server closes the connection
                        else:
                            outer.messages.append(
                                (sender, rcpts, message_from_bytes(data, policy=policy.default))
                            )
                            self.reply("250 queued")
                    elif cmd == "RSET":
                        self.reply("250 ok")
                    elif cmd == "NOOP":
                        self.reply("250 ok")
                    elif cmd == "QUIT":
                        self.reply("221 bye")
                        return
                    else:
                        self.reply("502 unknown")

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._server = Server(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "SmtpServer":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
