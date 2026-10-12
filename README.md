# ICB Investor Email Service — Investors' Department, Chattogram Branch

Python 3.12 replacement for the legacy `mail_to_investors/modules` (Python 3.7 + cx_Oracle).
Sends investors their **Portfolio Statement** (HTML body + PDF) and **Sale/Purchase
Confirmation** from the branch Oracle 10g database with one click or on a schedule.

**Testing / going live:** `TEST_MODE_INSTRUCTIONS.txt` — test mode is ON (`test_recipient` in `settings.toml`).

**How the code works / how to change it:** `CODE_MAP.txt`.

**Start here:** `SETUP_AND_SCHEDULER_GUIDE.txt` (setup, daily use, Windows Task Scheduler, Linux cron/systemd).

## Quick use (Windows)

| Double-click | Does |
|---|---|
| `setup_windows.bat` | one-time install + connection check |
| `send_portfolio_statements.bat` | send Portfolio Statements |
| `send_sale_purchase_confirmations.bat` | send Sale/Purchase Confirmations |
| `check_connections.bat` | test DB + mail login, send nothing |
| `send_sample_email.bat` | mail one statement from built-in sample data to the test address |
| `dry_run_portfolio.bat` / `dry_run_sale_purchase.bat` | build everything, send nothing (.eml files) |

## Layout

```
icb_mailer/            application package (python -m icb_mailer ...)
  cli.py               commands, exit codes
  config.py            settings.toml + .env loading
  db.py                Oracle connection (python-oracledb thick mode, 11.2 client)
  repository.py        all SQL (same queries as the legacy module, bind variables)
  pdf_portfolio.py     Portfolio Statement PDF (identical layout to legacy + logo)
  mailer.py            SMTP: one reused connection, pacing, retry/back-off
  jobs.py              the two jobs, per-account error isolation
  journal.py           SQLite sent-journal (no duplicates on re-run)
  locking.py           single-instance lock
  reporting.py         TXT + Excel run reports
  vendor/PollyReports.py  BSD-licensed report engine used by the legacy PDF
templates/             email HTML templates (legacy format + logo)
assets/icb_logo.jpg    ICB logo
tests/                 unit tests with sample data and a local SMTP server
settings.example.toml  configuration template -> copy to settings.toml (private)
.env.example           secrets template       -> copy to .env (private)
```

**Not in this repository** (git-ignored, keep them on the branch PC):

* `settings.toml`, `.env` - real configuration and passwords; `setup_windows.bat`
  creates them from the templates on first run.
* `oracle_client/instantclient_11_2/` - Oracle Instant Client 11.2 Basic, 32-bit
  (download from Oracle; licensed by Oracle).
* `installers/python-3.12.10.exe` - optional, Python 3.12 32-bit from python.org.
* `wheels/` - optional offline packages; without it setup installs from the internet.
* `output/`, `logs/` - investor PDFs, reports and run logs.

## Key technical decisions

* **Oracle 10g:** python-oracledb thin mode needs 12.1+, so thick mode with Instant Client
  11.2 is used. `oracledb` is pinned to **3.4.2**, the last line that accepts an 11.2
  client (26.x requires client 19+, which cannot connect to 10g). The bundled client
  is 32-bit, so Windows uses 32-bit Python 3.12.
* **Same output as before:** SQL, HTML templates and the PDF layout are carried over
  unchanged. The PDF drawing operations were verified to be identical to the legacy
  code for the same data. The only visible addition is the ICB logo.
* **Reliability** (ideas from the head-office .NET service): one SMTP connection per run,
  delay between messages, batch pauses, retries with back-off for temporary errors,
  no retries for permanent rejections, at most 3 login attempts, per-account error
  isolation, abort after 10 failures in a row, sent-journal to prevent duplicates,
  single-instance lock, dry-run and test-redirect modes, TXT/Excel reports, daily logs.

## Tests

```
.venv\Scripts\python -m unittest discover -s tests -t . -v
```
No database or real mail server is needed for these tests.
