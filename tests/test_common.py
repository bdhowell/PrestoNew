import unittest
from unittest.mock import AsyncMock, patch

import psycopg
from fastapi import HTTPException, Request
from starlette.datastructures import URL

import common
import posters


class ResumeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.request = Request({
            "type": "http", "headers": [], "session": {"poster_id": 11},
        })
        self.request.state.user_id = 7
        self.store = AsyncMock()
        self.store.set_resumption.return_value = True
        mocked = patch.object(posters, "store", return_value=self.store)
        mocked.start()
        self.addCleanup(mocked.stop)

    async def test_preserves_query_parameters_for_strings_and_request_urls(self) -> None:
        for url in (
            "/layout/edit?pid=11&filter=a%2Fb&tag=one&tag=two&blank=",
            URL("https://example.com/content/upload?pid=11&name=O%27Brien+Poster"),
        ):
            with self.subTest(url=url):
                await common.resume(self.request, url, clear=False)
                self.store.set_resumption.assert_awaited_with(7, 11, str(url))

    async def test_default_appends_marker_without_changing_query_or_fragment(self) -> None:
        for url, expected in (
            ("/layout/edit", "/layout/edit?xyzzy=arpa"),
            ("/layout/edit#title", "/layout/edit?xyzzy=arpa#title"),
            ("/layout/edit?", "/layout/edit?xyzzy=arpa"),
            ("/layout/edit?pid=11&", "/layout/edit?pid=11&xyzzy=arpa"),
            (
                URL("https://example.com/edit?tag=a%2Fb&tag=two&blank=#title"),
                "https://example.com/edit?tag=a%2Fb&tag=two&blank=&xyzzy=arpa#title",
            ),
        ):
            with self.subTest(url=url):
                await common.resume(self.request, url)
                self.store.set_resumption.assert_awaited_with(7, 11, expected)

    async def test_none_or_omitted_url_uses_current_request_with_query_parameters(self) -> None:
        self.request.scope.update({
            "scheme": "https", "server": ("example.com", 443),
            "path": "/content/upload",
            "query_string": b"pid=11&filter=a%2Fb&tag=one&tag=two&blank=",
        })
        expected = "https://example.com/content/upload?pid=11&filter=a%2Fb&tag=one&tag=two&blank=&xyzzy=arpa"
        await common.resume(self.request, None)
        self.store.set_resumption.assert_awaited_once_with(7, 11, expected)
        self.store.set_resumption.reset_mock()
        await common.resume(self.request)
        self.store.set_resumption.assert_awaited_once_with(7, 11, expected)
        self.store.set_resumption.reset_mock()
        await common.resume(self.request, clear=False)
        self.store.set_resumption.assert_awaited_once_with(7, 11, expected.removesuffix("&xyzzy=arpa"))

    async def test_requires_sign_in_and_a_selected_poster(self) -> None:
        self.request.state.user_id = None
        with self.assertRaises(HTTPException) as error:
            await common.resume(self.request, "/layout/edit")
        self.assertEqual(error.exception.status_code, 401)
        self.request.state.user_id = 7
        for pid in (None, "11", True, 0, -1):
            self.request.scope["session"]["poster_id"] = pid
            with self.subTest(pid=pid), self.assertRaises(HTTPException) as error:
                await common.resume(self.request, "/layout/edit")
            self.assertEqual(error.exception.status_code, 409)
        self.request.scope.pop("session")
        with self.assertRaises(HTTPException) as error:
            await common.resume(self.request, "/layout/edit")
        self.assertEqual(error.exception.status_code, 409)
        self.store.set_resumption.assert_not_awaited()

    async def test_rejects_empty_url(self) -> None:
        for url in ("", " "):
            with self.subTest(url=url), self.assertRaises(HTTPException) as error:
                await common.resume(self.request, url)
            self.assertEqual(error.exception.status_code, 422)
        self.store.set_resumption.assert_not_awaited()

    async def test_missing_or_unowned_poster_is_reported(self) -> None:
        self.store.set_resumption.return_value = False
        with self.assertRaises(HTTPException) as error:
            await common.resume(self.request, "/layout/edit?pid=11")
        self.assertEqual(error.exception.status_code, 404)

    async def test_database_failure_is_logged_and_reported(self) -> None:
        self.store.set_resumption.side_effect = psycopg.OperationalError("unavailable")
        with self.assertLogs("common", "ERROR"), self.assertRaises(HTTPException) as error:
            await common.resume(self.request, "/layout/edit")
        self.assertEqual(error.exception.status_code, 503)

    async def test_clear_resume_sets_resumption_to_null(self) -> None:
        await common.clear_resume(self.request)
        self.store.set_resumption.assert_awaited_once_with(7, 11, None)

    async def test_clear_resume_requires_sign_in_and_selection(self) -> None:
        self.request.state.user_id = None
        with self.assertRaises(HTTPException) as error:
            await common.clear_resume(self.request)
        self.assertEqual(error.exception.status_code, 401)
        self.request.state.user_id = 7
        for pid in (None, "11", True, 0, -1):
            self.request.scope["session"]["poster_id"] = pid
            with self.subTest(pid=pid), self.assertRaises(HTTPException) as error:
                await common.clear_resume(self.request)
            self.assertEqual(error.exception.status_code, 409)
        self.request.scope.pop("session")
        with self.assertRaises(HTTPException) as error:
            await common.clear_resume(self.request)
        self.assertEqual(error.exception.status_code, 409)
        self.store.set_resumption.assert_not_awaited()

    async def test_clear_resume_reports_missing_or_unowned_poster(self) -> None:
        self.store.set_resumption.return_value = False
        with self.assertRaises(HTTPException) as error:
            await common.clear_resume(self.request)
        self.assertEqual(error.exception.status_code, 404)

    async def test_clear_resume_logs_and_reports_database_failure(self) -> None:
        self.store.set_resumption.side_effect = psycopg.OperationalError("unavailable")
        with self.assertLogs("common", "ERROR"), self.assertRaises(HTTPException) as error:
            await common.clear_resume(self.request)
        self.assertEqual(error.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
