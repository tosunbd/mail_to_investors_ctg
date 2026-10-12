"""Run with:  .venv\\Scripts\\python -m unittest discover -s tests -v

No database or real mail server is needed: the jobs run against sample data
and a local in-process SMTP server.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from email import message_from_bytes, policy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from icb_mailer.config import ConfigError, load_settings  # noqa: E402
from icb_mailer.jobs import PORTFOLIO, SALE_PURCHASE, JobRunner, RunOptions  # noqa: E402
from icb_mailer.journal import SentJournal  # noqa: E402
from icb_mailer.locking import AlreadyRunningError, RunLock  # noqa: E402
from icb_mailer.mailer import Mailer, SmtpLoginError, split_addresses  # noqa: E402
from icb_mailer.pdf_portfolio import build_portfolio_pdf  # noqa: E402
from icb_mailer.reporting import Status, write_reports  # noqa: E402
from tests.fakes import (  # noqa: E402
    FOOTER_TEXT, HOLDINGS, PROFILES, PROJECT, FakeRepository, SmtpServer, write_project,
)


def _pages(pdf: bytes) -> int:
    return pdf.count(b"/Type /Page") - pdf.count(b"/Type /Pages")


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def settings(self, port: int = 2525, extra_run: str = ""):
        return load_settings(write_project(self.tmp, port, extra_run))

    def run_job(self, job, settings, options, mailer=None):
        journal = SentJournal(settings.paths.state / "sent_journal.sqlite3")
        try:
            return JobRunner(job, settings, FakeRepository(), journal, mailer, options).run()
        finally:
            journal.close()


class ConfigTests(Base):
    @unittest.skipUnless((PROJECT / "settings.toml").exists() and (PROJECT / ".env").exists(),
                         "private settings.toml / .env not present (fresh clone)")
    def test_real_project_settings_load(self):
        s = load_settings(PROJECT)
        self.assertEqual(s.database.mode, "thick")
        self.assertTrue(s.smtp.user)
        self.assertTrue((s.paths.templates / "pv_stat_template.html").exists())
        self.assertTrue(s.paths.logo.exists())

    def test_missing_secret_is_reported(self):
        write_project(self.tmp)
        (self.tmp / ".env").write_text("DB_USER=u\n", encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_settings(self.tmp)


class AddressTests(unittest.TestCase):
    def test_split(self):
        ok, bad = split_addresses(" A@x.com; b@y.org , junk ,a@x.com")
        self.assertEqual(ok, ["a@x.com", "b@y.org"])
        self.assertEqual(bad, ["junk"])
        self.assertEqual(split_addresses(None), ([], []))


class PdfTests(Base):
    def test_single_and_multi_page(self):
        one = build_portfolio_pdf(PROFILES[0], HOLDINGS[1001], FOOTER_TEXT, logo=PROJECT / "assets" / "icb_logo.jpg")
        many = build_portfolio_pdf(PROFILES[1], HOLDINGS[1002], FOOTER_TEXT)
        self.assertTrue(one.startswith(b"%PDF"))
        self.assertEqual(_pages(one), 1)
        self.assertGreater(_pages(many), 1)

    def test_missing_footer_values_do_not_crash(self):
        pdf = build_portfolio_pdf(PROFILES[0], HOLDINGS[1001], (None,) * 7)
        self.assertTrue(pdf.startswith(b"%PDF"))


class DryRunTests(Base):
    def test_portfolio_dry_run_writes_eml_and_pdf(self):
        s = self.settings()
        result = self.run_job(PORTFOLIO, s, RunOptions(dry_run=True))
        by_acc = {r.account: r for r in result.rows}
        self.assertEqual(by_acc["1001"].status, Status.DRY_RUN)
        self.assertEqual(by_acc["1002"].status, Status.DRY_RUN)
        self.assertEqual(by_acc["1003"].status, Status.NO_DATA)
        self.assertEqual(by_acc["1004"].status, Status.INVALID_EMAIL)
        self.assertEqual(result.failures, 1)

        emls = list(s.paths.dry_run.rglob("1001.eml"))
        self.assertEqual(len(emls), 1)
        msg = message_from_bytes(emls[0].read_bytes(), policy=policy.default)
        self.assertEqual(msg["Subject"], "Portfolio Statement From ICB")
        self.assertEqual(msg["From"], "No Reply <no-reply@icb.gov.bd>")
        self.assertIsNotNone(msg["Date"])
        self.assertIsNotNone(msg["Message-ID"])
        html = msg.get_body(("html",)).get_content()
        self.assertIn("INVESTMENT CORPORATION OF BANGLADESH", html)
        self.assertIn("MD. RAHIM UDDIN", html)
        self.assertIn("BEXIMCO PHARMACEUTICALS LTD.", html)
        self.assertIn("cid:", html)
        attachments = [p for p in msg.iter_attachments() if p.get_content_type() == "application/pdf"]
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), "Portfolio.pdf")
        self.assertTrue(attachments[0].get_content().startswith(b"%PDF"))
        # logo travels as an inline image
        images = [p for p in msg.walk() if p.get_content_type() == "image/jpeg"]
        self.assertEqual(len(images), 1)
        self.assertTrue(list(s.paths.pdf.rglob("1001.pdf")))

        msg2 = message_from_bytes(next(s.paths.dry_run.rglob("1002.eml")).read_bytes(), policy=policy.default)
        self.assertEqual(msg2["To"], "fatema@example.com, f.begum@example.org")

        txt, xlsx = write_reports(result, s.paths.reports, PORTFOLIO)
        self.assertIn("DRY RUN", txt.read_text(encoding="utf-8"))
        self.assertTrue(xlsx and xlsx.exists())

    def test_sale_purchase_dry_run(self):
        s = self.settings()
        result = self.run_job(SALE_PURCHASE, s, RunOptions(dry_run=True))
        by_acc = {r.account: r for r in result.rows}
        self.assertEqual(by_acc["1001"].status, Status.DRY_RUN)
        self.assertEqual(by_acc["1004"].status, Status.INVALID_EMAIL)
        msg = message_from_bytes(next(s.paths.dry_run.rglob("1001.eml")).read_bytes(), policy=policy.default)
        html = msg.get_body(("html",)).get_content()
        self.assertIn("Sale/Purchase Confirmation Statement is given bellow", html)
        self.assertIn("GRAMEENPHONE LTD.", html)
        self.assertIn("SALE", msg.get_body(("plain",)).get_content())

    def test_accounts_filter_reports_unknown_accounts(self):
        s = self.settings()
        result = self.run_job(PORTFOLIO, s, RunOptions(dry_run=True, accounts={"1001", "9999"}))
        self.assertEqual({r.account: r.status for r in result.rows},
                         {"1001": Status.DRY_RUN, "9999": Status.NO_DATA})


class SendTests(Base):
    def test_send_journal_prevents_duplicates(self):
        with SmtpServer() as server:
            s = self.settings(server.port)
            with Mailer(s.smtp) as mailer:
                first = self.run_job(PORTFOLIO, s, RunOptions(), mailer)
            self.assertEqual(first.counts[Status.SENT], 2)
            self.assertEqual(len(server.messages), 2)
            self.assertEqual(server.connections, 1, "one SMTP connection must be reused for the run")
            sender, rcpts, _ = server.messages[0]
            self.assertEqual(sender, "mailer@example.com")
            self.assertEqual(rcpts, ["rahim@example.com"])

            with Mailer(s.smtp) as mailer:
                second = self.run_job(PORTFOLIO, s, RunOptions(), mailer)
            self.assertEqual(second.counts[Status.ALREADY_SENT], 2)
            self.assertEqual(len(server.messages), 2, "nothing may be sent twice on the same day")

            with Mailer(s.smtp) as mailer:
                forced = self.run_job(PORTFOLIO, s, RunOptions(force=True, accounts={"1001"}), mailer)
            self.assertEqual(forced.counts[Status.SENT], 1)

    def test_test_recipient_redirects_and_is_not_journaled(self):
        with SmtpServer() as server:
            s = self.settings(server.port)
            with Mailer(s.smtp) as mailer:
                r = self.run_job(PORTFOLIO, s, RunOptions(test_recipient="tester@icb.gov.bd"), mailer)
            self.assertTrue(all(rcpts == ["tester@icb.gov.bd"] for _, rcpts, _ in server.messages))
            # without --accounts, test mode sends only the first statement, then stops
            self.assertEqual(len(server.messages), 1)
            self.assertEqual([row.account for row in r.rows], ["1001"])
            journal = SentJournal(s.paths.state / "sent_journal.sqlite3")
            self.assertFalse(journal.already_sent(PORTFOLIO, "1001"))
            journal.close()

    def test_test_mode_skips_accounts_without_data(self):
        class NoDataFirst(FakeRepository):
            def portfolio_profiles(self):
                return [PROFILES[2]] + super().portfolio_profiles()

        with SmtpServer() as server:
            s = self.settings(server.port)
            journal = SentJournal(s.paths.state / "sent_journal.sqlite3")
            with Mailer(s.smtp) as mailer:
                r = JobRunner(PORTFOLIO, s, NoDataFirst(), journal, mailer,
                              RunOptions(test_recipient="tester@icb.gov.bd")).run()
            journal.close()
            self.assertEqual([(row.account, row.status) for row in r.rows],
                             [("1003", Status.NO_DATA), ("1001", Status.SENT)])
            self.assertEqual(len(server.messages), 1)

    def test_null_means_live_mode(self):
        for value in ('""', '"null"', '"None"'):
            s = self.settings(extra_run=f"test_recipient = {value}")
            self.assertEqual(s.run.test_recipient, "")
        s = self.settings(extra_run='test_recipient = "test@example.com"')
        self.assertEqual(s.run.test_recipient, "test@example.com")
        self.assertEqual(s.run.test_max_emails, 1)

    def test_throttling_is_retried_on_a_new_connection(self):
        with SmtpServer() as server:
            server.script = [("DATA_END", "421 4.7.0 Service unavailable - try again later")]
            s = self.settings(server.port)
            with Mailer(s.smtp) as mailer:
                r = self.run_job(PORTFOLIO, s, RunOptions(accounts={"1001"}), mailer)
            self.assertEqual(r.counts[Status.SENT], 1)
            self.assertEqual(len(server.messages), 1)
            self.assertEqual(server.connections, 2)

    def test_permanently_refused_recipient_does_not_stop_the_run(self):
        with SmtpServer() as server:
            server.script = [("RCPT", "550 5.1.1 mailbox unavailable")]
            s = self.settings(server.port)
            with Mailer(s.smtp) as mailer:
                r = self.run_job(PORTFOLIO, s, RunOptions(accounts={"1001", "1002"}), mailer)
            statuses = {row.account: row.status for row in r.rows}
            self.assertEqual(statuses["1001"], Status.FAILED)
            self.assertEqual(statuses["1002"], Status.SENT)

    def test_wrong_password_fails_fast(self):
        with SmtpServer(password="other") as server:
            s = self.settings(server.port)
            with self.assertRaises(SmtpLoginError):
                Mailer(s.smtp).connect()
            self.assertLessEqual(server.connections, 3, "must not hammer the server with bad logins")


class SampleCommandTests(Base):
    def test_sample_sends_one_marked_email_to_test_recipient(self):
        from icb_mailer.cli import _sample

        with SmtpServer() as server:
            s = self.settings(server.port, 'test_recipient = "test@example.com"')
            self.assertEqual(_sample(s, ""), 0)
            self.assertEqual(len(server.messages), 1)
            _, rcpts, msg = server.messages[0]
            self.assertEqual(rcpts, ["test@example.com"])
            self.assertTrue(msg["Subject"].startswith("[SAMPLE - test data]"))


class LoginDropTests(Base):
    def test_disconnect_during_login_is_capped_at_three_tries(self):
        with SmtpServer() as server:
            server.script = [("AUTH", "DROP")] * 10
            s = self.settings(server.port)
            with self.assertRaises(SmtpLoginError):
                Mailer(s.smtp).connect()
            self.assertEqual(server.connections, 3)

    def test_one_dropped_login_then_success(self):
        with SmtpServer() as server:
            server.script = [("AUTH", "DROP")]
            s = self.settings(server.port)
            with Mailer(s.smtp):
                pass
            self.assertEqual(server.logins, 1)


class LockTests(Base):
    def test_second_instance_is_refused(self):
        lock_path = self.tmp / "job.lock"
        with RunLock(lock_path):
            with self.assertRaises(AlreadyRunningError):
                with RunLock(lock_path):
                    pass
        with RunLock(lock_path):  # released again
            pass


if __name__ == "__main__":
    unittest.main()
