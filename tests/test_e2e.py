"""End-to-end smoke test: a new author goes from the landing page to a downloaded poster PDF.

This drives a real browser against a real server process, with real Docling
extraction and real OpenAI analysis, so it is slow (several minutes) and costs a
little OpenAI usage. It is opt-in and skipped unless E2E_DATABASE_URL is set:

    E2E_DATABASE_URL  PostgreSQL URL for a role that can CREATE DATABASE; the
                      test creates a throwaway database from schema/schema.sql
                      (which needs the pgvector extension) and drops it after.
    E2E_REDIS_URL     Redis URL for sessions (default redis://localhost:6379/15).
                      Use a database number nothing else uses.
    E2E_DOCUMENT      Source document to upload (default: PDFs/Attention is All You Need.pdf).
    E2E_ARTIFACTS     Directory for the server log, trace, screenshots, and the PDF
                      (default: a temporary directory, printed at the end).
    E2E_HEADED        Set to 1 to show the browser window while the test runs.
    E2E_SLOWMO        Milliseconds to pause before each browser action (e.g. 500),
                      to make a headed run easy to follow.

Every run records a Playwright trace (trace.zip in the artifacts directory).
Replay it step by step, with screenshots, DOM, and network for each action:

    bin/python -m playwright show-trace <artifacts>/trace.zip

OPENAI_API_KEY and DOCLING_SERVICE_URL come from the environment or .env, as
for the app. Run with:

    E2E_DATABASE_URL=postgresql://root:...@localhost:5433/postgres \\
        bin/python -m unittest tests.test_e2e -v
"""

import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = {**dotenv_values(ROOT / ".env"), **os.environ}
MINUTE = 60_000


def _database_url(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{name}"))


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(os.environ.get("E2E_DATABASE_URL"), "Set E2E_DATABASE_URL to run the end-to-end test")
@unittest.skipUnless(SETTINGS.get("OPENAI_API_KEY"), "The end-to-end test needs OPENAI_API_KEY")
@unittest.skipUnless(SETTINGS.get("DOCLING_SERVICE_URL"), "The end-to-end test needs DOCLING_SERVICE_URL")
class AuthorFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import psycopg
        from psycopg import sql

        cls.artifacts = Path(os.environ.get("E2E_ARTIFACTS") or tempfile.mkdtemp(prefix="presto-e2e-"))
        cls.artifacts.mkdir(parents=True, exist_ok=True)
        cls.document = Path(os.environ.get("E2E_DOCUMENT") or ROOT / "PDFs/Attention is All You Need.pdf")
        if not cls.document.is_file():
            raise unittest.SkipTest(f"E2E document not found: {cls.document}")

        # A throwaway database, so the run never touches real data.
        admin_url = os.environ["E2E_DATABASE_URL"]
        cls.database = f"presto_e2e_{uuid4().hex[:12]}"
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.database)))
        cls.addClassCleanup(cls._drop_database, admin_url)
        cls.database_url = _database_url(admin_url, cls.database)
        with psycopg.connect(cls.database_url, autocommit=True) as connection:
            connection.execute((ROOT / "schema/schema.sql").read_text())

        cls.port = _free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.sources = tempfile.mkdtemp(prefix="presto-e2e-sources-")
        environment = {
            **os.environ,
            "DATABASE_URL": cls.database_url,
            "REDIS_URL": os.environ.get("E2E_REDIS_URL", "redis://localhost:6379/15"),
            "SESSION_BACKEND": "redis",
            "SESSION_COOKIE_SECURE": "false",
            "SESSION_MAX_AGE": "7200",
            "SECRET_KEY": SETTINGS.get("SECRET_KEY") or uuid4().hex + uuid4().hex,
            "PUBLIC_BASE_URL": cls.base,
            "MAIL_BACKEND": "console",
            "SOURCES_DIR": cls.sources,
            "GCS_BUCKET": "",
            "POSTER_PRICE": SETTINGS.get("POSTER_PRICE") or "0",
            "CHAT_WORD_INTERVAL": "0",
            "CHAT_WORD_DEVIATION": "0",
            "CHAT_WINDOW_TRANSITION": "0",
            "CHAT_INPUT_TRANSITION": "0",
            "SYSTEM_PURGE_ENABLED": "false",
            "PYTHONUNBUFFERED": "1",
        }
        cls.log_path = cls.artifacts / "server.log"
        cls.log = cls.log_path.open("w")
        cls.server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(cls.port)],
            cwd=ROOT, env=environment, stdout=cls.log, stderr=subprocess.STDOUT,
        )
        cls.addClassCleanup(cls._stop_server)
        deadline = time.monotonic() + 60
        while True:
            try:
                urllib.request.urlopen(f"{cls.base}/account/signin", timeout=2)
                break
            except OSError:
                if cls.server.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(f"The server did not start; see {cls.log_path}") from None
                time.sleep(0.25)

    @classmethod
    def _stop_server(cls) -> None:
        cls.server.terminate()
        try:
            cls.server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            cls.server.kill()
        cls.log.close()

    @classmethod
    def _drop_database(cls, admin_url: str) -> None:
        import psycopg
        from psycopg import sql

        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(cls.database)))

    def confirmation_link(self, email: str, timeout: float = 30) -> str:
        """Read the console mailer's confirmation email for this address from the server log."""
        pattern = re.compile(rf"To: {re.escape(email)}.*?({re.escape(self.base)}/account/confirmation\?\S+)", re.S)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            match = pattern.search(self.log_path.read_text(errors="replace"))
            if match:
                return match.group(1)
            time.sleep(0.25)
        self.fail(f"No confirmation email for {email} in {self.log_path}")

    def step(self, name: str) -> None:
        self.current = name
        print(f"  e2e: {name}", file=sys.stderr, flush=True)

    def test_new_author_creates_and_downloads_a_poster(self) -> None:
        from playwright.sync_api import expect, sync_playwright

        suffix = uuid4().hex[:8]
        username, email, password = f"e2e_{suffix}", f"e2e+{suffix}@example.test", f"Pw-{uuid4().hex}"
        errors: list[str] = []
        self.current = "start"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=os.environ.get("E2E_HEADED", "") not in ("1", "true"),
                slow_mo=float(os.environ.get("E2E_SLOWMO") or 0),
            )
            try:
                self.walk_through(browser, username, email, password, errors, expect)
            finally:
                browser.close()

    def walk_through(self, browser, username: str, email: str, password: str,
                     errors: list[str], expect) -> None:
        """Drive one fresh browser context through the whole authoring flow."""
        # At Playwright's default 1280x720 the floating Wikkit panel covers the
        # review page's "Return to poster layout" button, so use a desktop size.
        context = browser.new_context(base_url=self.base, accept_downloads=True,
                                      viewport={"width": 1920, "height": 1080})
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        page.set_default_timeout(30_000)
        page.on("pageerror", lambda error: errors.append(f"{page.url}: {error}"))
        chatlog = page.locator("#chatlog")
        try:
            self.step("landing page asks for a name")
            page.goto("/wikkit/magic")
            expect(chatlog).to_contain_text("Can I have your name to start?")
            page.locator("#chatinput").fill("Ada")
            page.locator("#chatinput").press("Enter")
            page.wait_for_url("**/account/register")

            self.step("register")
            page.locator("input[name=username]").fill(username)
            page.locator("input[name=email]").fill(email)
            page.locator("input[name=password]").fill(password)
            page.get_by_role("button", name="Register").click()
            expect(chatlog).to_contain_text("sent a confirmation link")

            self.step("confirm email and start a poster")
            page.goto(self.confirmation_link(email))
            page.wait_for_url("**/account/confirmation")
            page.get_by_role("link", name="Start my poster").click()
            page.wait_for_url("**/posters/list")
            page.locator("button[data-new-poster]").click()
            page.wait_for_url("**/conferences/query")

            self.step("skip the conference and choose dimensions")
            page.get_by_role("link", name="Continue without conference information").click()
            page.wait_for_url("**/layout/dimensions")
            size = page.locator("button.dimension-size").first
            print(f"  e2e: choosing {size.get_attribute('aria-label')}", file=sys.stderr)
            size.click()
            page.wait_for_url("**/content/upload")

            self.step(f"upload {self.document.name}")
            page.locator("#source-files").set_input_files(self.document)
            expect(page.locator("#upload-status")).to_contain_text("1 uploaded", timeout=2 * MINUTE)
            expect(page.locator("#upload-status")).to_contain_text("0 failed")
            page.locator("#finish-upload").click()
            page.wait_for_url("**/content/extract")

            self.step("extract content (Docling)")
            page.wait_for_url("**/content/elucidate", timeout=10 * MINUTE)

            self.step("analyze content (OpenAI)")
            page.wait_for_url("**/content/evaluate", timeout=10 * MINUTE)

            self.step("evaluate graphics (OpenAI)")
            page.locator("#continue-review").click(timeout=15 * MINUTE)
            page.wait_for_url("**/content/review")

            self.step("review sections")
            page.locator("#review-continue").click()
            page.wait_for_url("**/layout/edit")
            pid = page.locator("#layout").get_attribute("data-pid")
            self.assertTrue(pid and pid.isdigit(), "The editor has no poster ID")

            self.step("purchase and download the PDF")
            page.locator("#print").click()
            with page.expect_download(timeout=5 * MINUTE) as pending:
                page.locator('#chatlog button[data-sends="ready"]').click()
            download = pending.value
            self.assertEqual(download.suggested_filename, f"poster-{pid}.pdf")
            pdf = self.artifacts / download.suggested_filename
            download.save_as(pdf)
            data = pdf.read_bytes()
            self.assertTrue(data.startswith(b"%PDF-"), "The download is not a PDF")
            self.assertGreater(len(data), 10_000, "The PDF is suspiciously small")

            self.step("done")
            self.assertEqual(errors, [], "JavaScript errors occurred")
        except BaseException:
            shot = self.artifacts / "failure.png"
            try:
                page.screenshot(path=shot, full_page=True)
                (self.artifacts / "failure-chatlog.txt").write_text(chatlog.inner_text(timeout=2000))
            except Exception:
                pass
            print(f"\n  e2e failed at step '{self.current}' on {page.url}\n"
                  f"  JavaScript errors: {errors or 'none'}\n"
                  f"  artifacts: {self.artifacts} (server.log, failure.png, failure-chatlog.txt)",
                  file=sys.stderr)
            raise
        finally:
            trace = self.artifacts / "trace.zip"
            context.tracing.stop(path=trace)
            context.close()
            print(f"  e2e: replay with: bin/python -m playwright show-trace {trace}", file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
