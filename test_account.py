import asyncio
import os
import re
import unittest
from datetime import datetime, timedelta, timezone
from html import unescape
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import psycopg
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from httpx import Response

with patch.dict(os.environ, {"SESSION_BACKEND": "none"}):
    import app  # noqa: F401  (loads .env and the routers in application order)
import mailer
from kvstore import KVStore
import users
from identity import NAME_COOKIE, identity_context
from memory import memory_context
from routers import account, wikkit
from sessions import PostgresSessionMiddleware, PostgresSessionStore, Session

SECRET = "s" * 48


class PasswordAndLinkTests(unittest.TestCase):
    def test_password_hashes_verify_and_are_salted(self) -> None:
        first, second = users.hash_password("correct horse"), users.hash_password("correct horse")
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("scrypt$"))
        self.assertTrue(users.verify_password("correct horse", first))
        self.assertFalse(users.verify_password("wrong horse", first))
        self.assertFalse(users.verify_password("correct horse", None))
        self.assertFalse(users.verify_password("correct horse", "garbage"))

    def test_links_are_bound_to_purpose_user_recovery_and_password(self) -> None:
        now = datetime.now(timezone.utc)
        user = users.User(7, "Ada", "ada", "ada@example.com", "hash", now, None)
        with patch.dict(os.environ, {"SECRET_KEY": SECRET}):
            token = users.link_token(users.CONFIRMATION_PURPOSE, user)
            self.assertTrue(users.check_link(users.CONFIRMATION_PURPOSE, user, token, 60))
            self.assertFalse(users.check_link(users.RECOVERY_PURPOSE, user, token, 60))
            self.assertFalse(users.check_link(users.CONFIRMATION_PURPOSE, user, token + "x", 60))
            for changed in (
                users.User(8, "Ada", "ada", "ada@example.com", "hash", now, None),
                users.User(7, "Ada", "ada", "ada@example.com", "new", now, None),
                users.User(7, "Ada", "ada", "ada@example.com", "hash", now + timedelta(seconds=1), None),
                users.User(7, "Ada", "ada", "ada@example.com", "hash", None, None),
            ):
                with self.subTest(changed=changed):
                    self.assertFalse(
                        users.check_link(users.CONFIRMATION_PURPOSE, changed, token, 60)
                    )
            expired = users.User(7, "Ada", "ada", "ada@example.com", "hash", now - timedelta(hours=2), None)
            expired_token = users.link_token(users.CONFIRMATION_PURPOSE, expired)
            self.assertFalse(
                users.check_link(users.CONFIRMATION_PURPOSE, expired, expired_token, 3600)
            )

    def test_links_require_a_strong_secret(self) -> None:
        user = users.User(7, "Ada", "ada", "ada@example.com", "hash", datetime.now(timezone.utc), None)
        with patch.dict(os.environ, {"SECRET_KEY": "short"}):
            with self.assertRaises(RuntimeError):
                users.link_token(users.CONFIRMATION_PURPOSE, user)


class AccountWithoutDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app.app)

    def test_json_endpoints_reject_forms_and_cross_origin_requests(self) -> None:
        response = self.client.post("/account/validate", data={"email": "a@b.co"})
        self.assertEqual(response.status_code, 415)
        response = self.client.post(
            "/account/validate", json={"email": "a@b.co"},
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            "/account/validate", content=b"[1]",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)

    def test_sign_in_without_sessions_explains_unavailability(self) -> None:
        response = self.client.post("/account/validate", json={"email": "a@b.co", "password": "x"})
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["ok"])

    def test_signed_out_pages_redirect_to_sign_in(self) -> None:
        for path in ("/account", "/account/update", "/account/password", "/account/delete"):
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 303)
                self.assertEqual(response.headers["location"], "/account/signin")

    def test_public_pages_render_with_wikkit_and_account_script(self) -> None:
        for path, page in (
            ("/account/register", "account-register"),
            ("/account/signin", "account-signin"),
            ("/account/recover?email=a%40b.co", "account-recover"),
            ("/account/signout", "account-signout"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(f'data-page="{page}"', response.text)
                self.assertIn('src="/static/javascript/account/router.js"', response.text)
                self.assertIn("window.wikkit.say(", response.text)
        self.assertIn('value="a@b.co"', self.client.get("/account/recover?email=a%40b.co").text)

    def test_sign_in_greets_returning_user_by_escaped_cookie_name(self) -> None:
        self.client.cookies.set(NAME_COOKIE, "%3Cb%3EAda%3C%2Fb%3E")
        response = self.client.get("/account/signin")
        self.assertIn("&lt;b&gt;Ada&lt;/b&gt;", response.text)
        self.assertNotIn("<b>Ada</b>", response.text)
        self.assertIn('src="/static/graphics/wikkit.svg"', response.text)


class AccountMenuTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = users.User(
            7, "Ada", "ada", "ada@example.com", users.hash_password("analytical"),
            None, datetime.now(timezone.utc),
        )
        self.store = AsyncMock()
        store = patch.object(users, "store", return_value=self.store)
        store.start()
        self.addCleanup(store.stop)
        self.store.get.return_value = self.user
        self.store.get_by_email.return_value = self.user
        session_store = PostgresSessionStore(SECRET)
        session = Session("menu-test", {}, 1, datetime.now(timezone.utc), b"")
        for method, result in (("load", session), ("save", None)):
            mocked = patch.object(session_store, method, return_value=result)
            mocked.start()
            self.addCleanup(mocked.stop)
        application = FastAPI(dependencies=[Depends(identity_context)])
        application.add_middleware(
            PostgresSessionMiddleware, store=session_store, secure=False,
        )
        application.include_router(wikkit.router)
        application.include_router(account.router)
        self.client = TestClient(application)

    def sign_in(self) -> Response:
        return self.client.post("/account/validate", json={
            "email": self.user.email, "password": "analytical",
        })

    def test_sign_in_reports_state_before_redirect_and_sign_out_clears_session(self) -> None:
        response = self.sign_in()
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()["signed_in"], True)
        self.assertEqual(response.json()["redirect"], "/posters/list")
        self.assertIn('href="/account/signout">Sign out</a>', self.client.get("/wikkit").text)

        response = self.client.get("/account/signout")
        self.assertIn('data-signed-in="false"', response.text)
        shell = self.client.get("/wikkit").text
        self.assertIn('href="/account/signin">Sign in</a>', shell)
        self.assertNotIn('href="/account/signout">Sign out</a>', shell)
        self.assertEqual(
            self.client.get("/account/update", follow_redirects=False).headers["location"],
            "/account/signin",
        )

    def test_failed_sign_in_does_not_report_a_state_change(self) -> None:
        response = self.client.post("/account/validate", json={
            "email": self.user.email, "password": "wrong",
        })
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("signed_in", response.json())

    def test_password_recovery_reports_signed_in_state(self) -> None:
        recovery = datetime.now(timezone.utc)
        self.user = users.User(
            7, "Ada", "ada", "ada@example.com", self.user.password,
            recovery, self.user.verified,
        )
        self.store.get.return_value = self.user
        self.store.set_password.return_value = self.user
        with patch.dict(os.environ, {"SECRET_KEY": SECRET}):
            token = users.link_token(users.RECOVERY_PURPOSE, self.user)
            response = self.client.get(
                "/account/recovered", params={"uid": self.user.uid, "token": token},
            )
        self.assertEqual(response.status_code, 200)
        response = self.client.post("/account/password", json={"password": "difference"})
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()["signed_in"], True)

    def test_account_deletion_reports_signed_out_state(self) -> None:
        self.sign_in()
        response = self.client.post("/account/delete", json={
            "email": self.user.email, "password": "analytical",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()["signed_in"], False)
        self.assertIn('href="/account/signin">Sign in</a>', self.client.get("/wikkit").text)


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "set TEST_DATABASE_URL for PostgreSQL tests")
class AccountFlowTests(unittest.TestCase):
    """End-to-end account flows against a dedicated test database (public schema)."""

    def setUp(self) -> None:
        dsn = os.environ["TEST_DATABASE_URL"]
        environment = patch.dict(os.environ, {
            "DATABASE_URL": dsn, "SECRET_KEY": SECRET, "MAIL_BACKEND": "console",
            "SESSION_COOKIE_SECURE": "false", "PUBLIC_BASE_URL": "http://testserver",
        })
        environment.start()
        self.addCleanup(environment.stop)
        store = patch.object(users, "_store", users.UserStore())
        store.start()
        self.addCleanup(store.stop)
        asyncio.run(users.store().get(-1))
        with psycopg.connect(dsn) as connection:
            connection.execute('TRUNCATE "user" CASCADE')
        mailer.outbox.clear()
        application = FastAPI(dependencies=[
            Depends(identity_context), Depends(memory_context, scope="function"),
        ])
        application.add_middleware(
            PostgresSessionMiddleware, store=PostgresSessionStore(SECRET), secure=False
        )
        application.include_router(wikkit.router)
        application.include_router(account.router)
        self.client = TestClient(application)
        self.client.get("/account/register")  # establish a session

    def mailed_link(self) -> tuple[str, dict[str, str]]:
        body = mailer.outbox[-1].get_content()
        url = re.search(r"http://testserver\S+", body)
        assert url is not None
        parts = urlsplit(url.group(0))
        return parts.path, {key: values[0] for key, values in parse_qs(parts.query).items()}

    def register(self, username: str = "ada", email: str = "Ada@Example.com",
                 password: str = "analytical") -> dict[str, object]:
        with self.assertLogs("mailer", "WARNING"):
            response = self.client.post("/account/register", json={
                "username": username, "email": email, "password": password,
            })
        self.assertEqual(response.status_code, 200)
        return response.json()

    def confirm(self) -> None:
        path, query = self.mailed_link()
        self.assertEqual(path, "/account/confirmation")
        response = self.client.get(path, params=query)
        self.assertEqual(response.status_code, 200)

    def sign_in(self, email: str = "ada@example.com", password: str = "analytical"):
        return self.client.post("/account/validate", json={"email": email, "password": password})

    def test_registration_validates_fields(self) -> None:
        response = self.client.post("/account/register", json={
            "username": "a", "email": "nope", "password": "short",
        })
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(set(body["errors"]), {"username", "email", "password"})
        self.assertEqual(len(mailer.outbox), 0)

    def test_registration_uses_remembered_name_and_requires_unique_fields(self) -> None:
        self.client.cookies.set(NAME_COOKIE, "Ada%20Lovelace")
        body = self.register()
        self.assertTrue(body["ok"])
        self.assertIn("Ada Lovelace", body["say"])
        self.assertIn("Ada@Example.com", mailer.outbox[-1]["To"])
        response = self.client.post("/account/register", json={
            "username": "ADA", "email": "ada@example.com", "password": "analytical",
        })
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(set(body["errors"]), {"username", "email"})
        self.assertIn("email and username\nare already registered", body["say"])

    def test_confirmation_signs_in_once(self) -> None:
        self.assertEqual(
            self.client.get("/account/confirmation", follow_redirects=False).headers["location"],
            "/account/signin",
        )
        self.register()
        path, query = self.mailed_link()
        response = self.client.get(path, params=query, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/account/confirmation")
        for _ in range(2):  # The landing page survives reloads.
            response = self.client.get("/account/confirmation")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Congratulations, ada.", unescape(response.text))
            self.assertIn('data-signed-in="true"', response.text)
        self.assertEqual(
            self.client.get("/account", follow_redirects=False).headers["location"],
            "/account/update",
        )
        self.assertEqual(self.client.get(path, params=query).status_code, 400)
        self.assertEqual(self.client.get(path, params={**query, "token": "x"}).status_code, 400)

    def test_unconfirmed_sign_in_resends_confirmation(self) -> None:
        self.register()
        with self.assertLogs("mailer", "WARNING"):
            response = self.sign_in()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(mailer.outbox), 2)
        self.confirm()

    def test_sign_in_sign_out_and_wrong_password(self) -> None:
        self.register()
        self.confirm()
        self.client.get("/account/signout")
        response = self.sign_in(password="wrong password")
        self.assertEqual(response.status_code, 401)
        self.assertIn("don&rsquo;t recognize that email", response.json()["say"])
        self.client.cookies.set(NAME_COOKIE, "Ada")
        self.assertIn("I&rsquo;m sorry, Ada,", self.sign_in(password="wrong").json()["say"])
        response = self.sign_in(email="ADA@example.com")
        self.assertEqual(response.json(), {
            "ok": True, "redirect": "/posters/list",
            "signed_in": True,
            "command": {"does": "clear-chat"},
            "say": response.json()["say"],
        })
        self.assertEqual(
            self.client.get("/account/signin", follow_redirects=False).headers["location"],
            "/posters/list",
        )
        signout = self.client.get("/account/signout")
        self.assertIn('data-signed-in="false"', signout.text)
        self.assertEqual(
            self.client.get("/account/update", follow_redirects=False).headers["location"],
            "/account/signin",
        )

    def test_password_recovery_is_single_use_and_does_not_reveal_accounts(self) -> None:
        self.register()
        self.confirm()
        self.client.get("/account/signout")
        unknown = self.client.post("/account/recover", json={"email": "who@example.com"})
        self.assertTrue(unknown.json()["ok"])
        self.assertEqual(len(mailer.outbox), 1)
        with self.assertLogs("mailer", "WARNING"):
            known = self.client.post("/account/recover", json={"email": "ada@example.com"})
        self.assertEqual(
            unknown.json()["say"].replace("who@example.com", "ada@example.com"),
            known.json()["say"],
        )
        path, query = self.mailed_link()
        self.assertEqual(path, "/account/recovered")
        response = self.client.get(path, params=query, follow_redirects=False)
        self.assertEqual(response.headers["location"], "/account/password")
        page = self.client.get("/account/password")
        self.assertNotIn('name="current_password"', page.text)
        response = self.client.post("/account/password", json={"password": "difference"})
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["redirect"], "/posters/list")
        self.client.get("/account/signout")
        self.assertEqual(self.client.get(path, params=query).status_code, 400)
        self.assertEqual(self.sign_in().status_code, 401)
        self.assertTrue(self.sign_in(password="difference").json()["ok"])

    def test_signed_in_password_change_requires_current_password(self) -> None:
        self.register()
        self.confirm()
        self.assertIn('name="current_password"', self.client.get("/account/password").text)
        response = self.client.post("/account/password", json={
            "current_password": "wrong", "password": "difference",
        })
        self.assertEqual(response.status_code, 403)
        response = self.client.post("/account/password", json={
            "current_password": "analytical", "password": "difference",
        })
        self.assertTrue(response.json()["ok"])
        self.client.get("/account/signout")
        self.assertTrue(self.sign_in(password="difference").json()["ok"])

    def test_delete_requires_matching_credentials(self) -> None:
        self.register()
        self.confirm()
        user = asyncio.run(users.store().get_by_email("ada@example.com"))
        assert user is not None
        # User memory rows must not block deletion.
        asyncio.run(
            KVStore("user_memory", "user", foreign_key_value=user.uid).set("color", "gold")
        )
        response = self.client.post("/account/delete", json={
            "email": "ada@example.com", "password": "wrong",
        })
        self.assertEqual(response.status_code, 403)
        self.client.cookies.set(NAME_COOKIE, "Ada")
        response = self.client.post("/account/delete", json={
            "email": "ada@example.com", "password": "analytical",
        })
        self.assertTrue(response.json()["ok"])
        self.assertIn(f'{NAME_COOKIE}=""', response.headers["set-cookie"])
        self.assertIsNone(asyncio.run(users.store().get_by_email("ada@example.com")))
        self.assertEqual(
            self.client.get("/account", follow_redirects=False).headers["location"],
            "/account/signin",
        )


if __name__ == "__main__":
    unittest.main()
