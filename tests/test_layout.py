import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.requests import HTTPConnection

import layout as routes
import posters

DRAFT = posters.Poster(11, "Research", None, "36x48", None, None, False, None)


class LayoutRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.uid = 7
        self.session = {"poster_id": 11}
        self.store = AsyncMock()
        self.store.get.return_value = DRAFT
        mocked = patch.object(posters, "store", return_value=self.store)
        mocked.start()
        self.addCleanup(mocked.stop)

        async def identity(connection: HTTPConnection) -> None:
            connection.state.user_id = self.uid
            connection.scope["session"] = self.session

        application = FastAPI(dependencies=[Depends(identity)])
        application.include_router(routes.router)
        self.client = TestClient(application)

    def test_selected_draft_renders_its_layout(self) -> None:
        response = self.client.get("/layout/layout")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="pane0"', response.text)
        self.store.get.assert_awaited_once_with(7, 11)

    def test_edit_route_selects_requested_owned_poster(self) -> None:
        self.store.get.return_value = replace(DRAFT, pid=12, template="central")
        response = self.client.get("/layout/edit?pid=12")
        self.assertEqual(response.status_code, 200)
        self.assertIn('class="pane,vertical"', response.text)
        self.assertEqual(self.session["poster_id"], 12)

    def test_home_redirects_to_layout(self) -> None:
        response = self.client.get("/layout", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/layout/layout")

    def test_requires_sign_in_and_a_selected_poster(self) -> None:
        self.uid = None
        response = self.client.get("/layout/layout", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/account/signin")
        self.uid = 7
        self.session.clear()
        response = self.client.get("/layout/layout", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/posters/list")
        self.store.get.assert_not_awaited()

    def test_purchased_poster_redirects_to_view(self) -> None:
        self.store.get.return_value = replace(DRAFT, purchased=True)
        response = self.client.get("/layout/layout", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/posters/11/view")
