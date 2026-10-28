import asyncio
import datetime
import json
import os
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import openai
import psycopg
from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from psycopg import sql
from starlette.requests import HTTPConnection

import conferences
import posters
from conferences import Conference
from routers import conferences as routes
from routers import wikkit as wikkit_routes

ROOT = Path(__file__).resolve().parent.parent
TODAY = datetime.date.today()
NEXT_YEAR = TODAY.replace(year=TODAY.year + 1)
DRAFT = posters.Poster(11, "Research", None, None, None, None, False, None)


def result(name: str, start: str | None, end: str | None = None, **extra: object) -> dict:
    data = {
        "conference_name": name, "event_type": "conference",
        "conference_location": {"country": "USA", "state_province": "MA", "city": "Boston"},
        "conference_start_date": start, "conference_end_date": end,
        "conference_subject_or_summary": "Chemistry",
        "maximum_poster_dimensions": None, "maximum_poster_width": 48, "maximum_poster_height": None,
        "required_poster_dimensions": None, "required_poster_width": None, "required_poster_height": [36],
        "allowed_poster_dimensions": None, "allowed_poster_width": [40], "allowed_poster_height": None,
        "poster_units": "inches", "poster_layouts": None, "special_poster_attributes": None,
    }
    return {**data, **extra}


def conference(name: str, cid: int | None = None, kind: str = "conference") -> Conference:
    return Conference(cid, name, "Boston, MA, USA", "Chemistry", NEXT_YEAR, NEXT_YEAR, kind, {"event_type": kind})


class ConferenceInfoTests(unittest.IsolatedAsyncioTestCase):
    async def test_filters_past_and_undated_parses_dates_and_unifies_requirements(self) -> None:
        response = {"conferences": [
            result("Past Meeting", "2001-03-01"),
            result("Undated Meeting", None),
            result("Unparseable Meeting", "sometime soon"),
            result("Future Meeting", NEXT_YEAR.isoformat()),
            result("Future Fair", f"{NEXT_YEAR.isoformat()}", f"{NEXT_YEAR.year}-12-31", poster_units="centimeters"),
        ]}
        criteria = dict(conferences.poster_criteria)
        with patch.object(conferences, "interact_with_openai", AsyncMock(return_value=json.dumps(response))) as ask:
            found = await conferences.get_conference_info("chemistry in Boston")
        self.assertIn("chemistry in Boston", ask.await_args.args[0])
        self.assertEqual([item["conference_name"] for item in found], ["Future Meeting", "Future Fair"])
        self.assertEqual(found[0]["conference_start_date"], NEXT_YEAR)
        self.assertEqual(found[0]["conference_end_date"], NEXT_YEAR)
        self.assertEqual(found[1]["conference_end_date"], datetime.date(NEXT_YEAR.year, 12, 31))
        # The first specified value wins rather than the last listed attribute.
        self.assertEqual(found[0]["width"], [40])
        self.assertEqual(found[0]["height"], [36])
        self.assertIsNone(found[0]["dimensions"])
        self.assertEqual((found[0]["units"], found[1]["units"]), ("in", "cm"))
        self.assertEqual(conferences.poster_criteria, criteria)

    async def test_returns_none_when_only_past_conferences_are_found(self) -> None:
        response = json.dumps({"conferences": [result("Past Meeting", "2001-03-01")]})
        with patch.object(conferences, "interact_with_openai", AsyncMock(return_value=response)), \
                self.assertLogs("conferences", "INFO") as logs:
            self.assertIsNone(await conferences.get_conference_info("chemistry"))
        self.assertIn("Past Meeting", "\n".join(logs.output))

    def test_schema_allows_null_units_and_identifies_fairs(self) -> None:
        self.assertIn(None, conferences.CONFERENCE_SCHEMA["properties"]["poster_units"]["enum"])
        self.assertIn("event_type", conferences.CONFERENCE_SCHEMA["required"])

    def test_search_results_become_json_safe_conference_rows(self) -> None:
        raw = result("  Chem   Expo ", NEXT_YEAR.isoformat(), event_type="fair")
        raw["conference_start_date"] = raw["conference_end_date"] = NEXT_YEAR
        row = conferences.from_search_result(raw)
        self.assertEqual((row.name, row.location, row.kind, row.cid), ("Chem Expo", "Boston, MA, USA", "fair", None))
        self.assertEqual(row.public()["starts"], NEXT_YEAR.isoformat())
        json.dumps(row.memory())


class ChoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.table = [conference("American Chemistry Meeting", 3), conference("Boston Science Fair", kind="fair")]

    def test_numbers_names_declines_and_more_information(self) -> None:
        self.assertIs(routes.choice("2", self.table), self.table[1])
        self.assertIs(routes.choice("#1", self.table), self.table[0])
        self.assertEqual(routes.choice("3", self.table), "unclear")
        self.assertIs(routes.choice("boston science fair", self.table), self.table[1])
        for reply in ("No", "None of these", "skip", "I don't know"):
            with self.subTest(reply=reply):
                self.assertEqual(routes.choice(reply, self.table), "decline")
        self.assertEqual(routes.choice("yes", self.table), "unclear")
        self.assertEqual(routes.choice("It is held every spring in Denver", self.table), "more")

    def test_scope_and_merge(self) -> None:
        self.assertEqual(routes.scope(self.table[:1]), "conference")
        self.assertEqual(routes.scope(self.table[1:]), "fair")
        self.assertEqual(routes.scope(self.table), "conference or fair")
        table = [conference("Boston Science Fair", kind="fair")]
        routes.merge(table, [conference("boston  science fair", 9, "fair"), conference("Other")])
        self.assertEqual([(item.name, item.cid) for item in table], [("boston  science fair", 9), ("Other", None)])
        self.assertIn("select the conference or fair", routes.select_message("Ada", self.table))


class ConferenceRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.uid = 7
        self.pid = 11
        self.posters = AsyncMock()
        self.posters.get.return_value = DRAFT
        mocked = patch.object(posters, "store", return_value=self.posters)
        mocked.start()
        self.addCleanup(mocked.stop)

        async def identity(connection: HTTPConnection) -> None:
            connection.state.user_id = self.uid
            connection.state.poster_id = self.pid
            connection.state.user = {"user-name": "<Ada>"}
            connection.scope["session"] = {}

        application = FastAPI(dependencies=[Depends(identity)])
        application.include_router(routes.router)
        application.mount("/static", StaticFiles(directory=ROOT / "static"))
        self.client = TestClient(application)

    def test_find_navigation_goes_to_query_page(self) -> None:
        response = self.client.get("/conferences/find", follow_redirects=False)
        self.assertEqual((response.status_code, response.headers["location"]), (303, "/conferences/query"))

    def test_find_returns_conferences_and_validates_requests(self) -> None:
        raw = result("Chem Expo", NEXT_YEAR.isoformat())
        raw["conference_start_date"] = raw["conference_end_date"] = NEXT_YEAR
        with patch.object(conferences, "get_conference_info", AsyncMock(return_value=[raw])) as find:
            response = self.client.post("/conferences/find", json={"criteria": "  chemistry \n Boston "})
        self.assertEqual(response.status_code, 200)
        find.assert_awaited_once_with("chemistry Boston")
        self.assertEqual(response.json()["conferences"][0]["name"], "Chem Expo")
        self.assertNotIn("details", response.json()["conferences"][0])
        for body in ({}, {"criteria": " "}, {"criteria": "x" * 2001}, {"criteria": 3}):
            self.assertEqual(self.client.post("/conferences/find", json=body).status_code, 422)
        self.assertEqual(self.client.post("/conferences/find", json={"criteria": "a"}, headers={"Origin": "https://evil.example"}).status_code, 403)
        with patch.object(conferences, "get_conference_info", AsyncMock(side_effect=openai.OpenAIError("down"))), \
                self.assertLogs("routers.conferences", "ERROR"):
            self.assertEqual(self.client.post("/conferences/find", json={"criteria": "a"}).status_code, 503)
        self.uid = None
        self.assertEqual(self.client.post("/conferences/find", json={"criteria": "a"}).status_code, 401)

    def test_query_page_requires_identity_and_editable_draft(self) -> None:
        response = self.client.get("/conferences/query")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="conferences-list"', response.text)
        self.assertIn("/static/javascript/pages/conferences-query.js", response.text)
        self.posters.get.assert_awaited_once_with(7, 11)
        self.posters.get.return_value = posters.Poster(11, "Research", None, None, None, None, True, None)
        response = self.client.get("/conferences/query", follow_redirects=False)
        self.assertEqual(response.headers["location"], "/posters/list")
        self.pid = None
        self.assertEqual(self.client.get("/conferences/query", follow_redirects=False).headers["location"], "/posters/list")
        self.uid = None
        self.assertEqual(self.client.get("/conferences/query", follow_redirects=False).headers["location"], "/account/signin")

    def test_query_workflow_is_registered_for_the_page(self) -> None:
        self.assertIs(wikkit_routes.WORKFLOWS["conferences-query"], routes.query_workflow)


class QueryWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.store = AsyncMock()
        self.store.search.return_value = []
        self.store.select.side_effect = lambda uid, pid, item: item if item.cid else Conference(
            42, item.name, item.location, item.purpose, item.starts, item.ends, item.kind, item.details,
        )
        mocked = patch.object(conferences, "store", return_value=self.store)
        mocked.start()
        self.addCleanup(mocked.stop)
        self.find = AsyncMock(return_value=None)
        mocked = patch.object(conferences, "get_conference_info", self.find)
        mocked.start()
        self.addCleanup(mocked.stop)

    async def run_workflow(self, *replies: str, poster_id: int | None = 11) -> SimpleNamespace:
        replies = list(replies)
        bot = SimpleNamespace(
            websocket=SimpleNamespace(state=SimpleNamespace(user_id=7, poster_id=poster_id, user={"user-name": "<Ada>"})),
            long_term_memory=AsyncMock(), prompts=[], sent=[],
        )

        async def request_response(say: str | None = None) -> str:
            bot.prompts.append(say)
            if not replies:
                raise AssertionError(f"Unexpected prompt: {say}")
            return replies.pop(0)

        async def send(reply: dict) -> None:
            bot.sent.append(reply)

        bot.request_response = request_response
        bot.send = send
        await asyncio.wait_for(routes.query_workflow(bot), 5)
        self.assertEqual(replies, [])
        return bot

    def redirected(self, bot: SimpleNamespace) -> bool:
        return bot.sent[-1]["command"] == {"does": "redirect", "payload": "/content/upload"}

    async def test_no_information_continues_to_uploads(self) -> None:
        for reply in ("I don't know", "idk", "No"):
            with self.subTest(reply=reply):
                bot = await self.run_workflow(reply)
                self.assertIn("Many conferences and fairs", bot.prompts[0])
                self.assertIn("That&rsquo;s okay, we can continue without conference information.", bot.sent[-1]["say"])
                self.assertTrue(self.redirected(bot))
        self.store.search.assert_not_awaited()

    async def test_single_similar_stored_match_is_confirmed_and_selected(self) -> None:
        stored = conference("American Chemistry Meeting", 3)
        self.store.search.return_value = [stored]
        bot = await self.run_workflow("american chemistry meetng", "Yes")
        self.assertIn("I found a conference or fair that I think is yours.", bot.prompts[1])
        self.store.select.assert_awaited_once_with(7, 11, stored)
        bot.long_term_memory.set.assert_awaited_once_with("conference", stored.memory())
        self.assertTrue(self.redirected(bot))
        self.find.assert_not_awaited()
        shown = [reply["command"]["payload"] for reply in bot.sent if reply.get("command", {}).get("does") == "show-conferences"]
        self.assertEqual(shown[0][0]["cid"], 3)

    async def test_web_results_are_added_to_the_table_and_selected_by_number(self) -> None:
        self.store.search.return_value = [conference("Stored Meeting", 3)]
        raw = result("Chem Expo", NEXT_YEAR.isoformat(), event_type="fair")
        raw["conference_start_date"] = raw["conference_end_date"] = NEXT_YEAR
        self.find.return_value = [raw]
        bot = await self.run_workflow("chemistry in Boston", "2")
        self.find.assert_awaited_once_with("chemistry in Boston")
        self.assertIn("&lt;Ada&gt;, please select the conference or fair", bot.prompts[1])
        picked = self.store.select.await_args.args[2]
        self.assertEqual((picked.name, picked.cid), ("Chem Expo", None))
        saved = bot.long_term_memory.set.await_args.args[1]
        self.assertEqual((saved["cid"], saved["name"]), (42, "Chem Expo"))
        self.assertTrue(self.redirected(bot))

    async def test_nothing_found_asks_for_more_information_then_continues_without(self) -> None:
        bot = await self.run_workflow("mystery meeting", "yes", "It is about bees", "no")
        self.assertIn("I&rsquo;m sorry &lt;Ada&gt; I couldn&rsquo;t find any matching conferences or fairs.", bot.prompts[1])
        self.assertIn("Please tell me anything else", bot.prompts[2])
        self.assertEqual(self.find.await_args_list[1].args[0], "mystery meeting\nIt is about bees")
        self.assertIn("Okay, we will continue without conference information.", bot.sent[-1]["say"])
        self.assertTrue(self.redirected(bot))
        self.store.select.assert_not_awaited()

    async def test_declining_listed_conferences_continues_without(self) -> None:
        self.store.search.return_value = [conference("Stored Meeting", 3), conference("Other Meeting", 4)]
        bot = await self.run_workflow("meeting", "none of these")
        self.assertIn("Okay, we will continue without conference information.", bot.sent[-1]["say"])
        self.store.select.assert_not_awaited()

    async def test_search_failures_are_reported_and_selection_failures_reprompt(self) -> None:
        self.store.search.side_effect = psycopg.OperationalError("down")
        self.find.side_effect = [ValueError("bad JSON"), None]
        with self.assertLogs("routers.conferences", "ERROR"):
            bot = await self.run_workflow("meeting", "no")
        self.assertTrue(any("couldn&rsquo;t search" in reply.get("say", "") for reply in bot.sent))
        self.store.search.side_effect = None
        self.store.search.return_value = [conference("Stored Meeting", 3), conference("Other Meeting", 4)]
        self.find.side_effect = None
        self.store.select.side_effect = [LookupError("Your poster can no longer be edited."), conference("Other Meeting", 4)]
        bot = await self.run_workflow("meeting", "1", "2")
        self.assertTrue(any("no longer be edited" in reply.get("say", "") for reply in bot.sent))
        self.assertEqual(self.store.select.await_count, 2)
        self.assertTrue(self.redirected(bot))

    async def test_requires_a_selected_poster(self) -> None:
        bot = await self.run_workflow(poster_id=None)
        self.assertIn("Please create or open a poster", bot.sent[0]["say"])


@unittest.skipUnless(os.environ.get("POSTERS_TEST_DATABASE_URL"), "Set POSTERS_TEST_DATABASE_URL for isolated PostgreSQL tests")
class ConferenceDatabaseTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = "test_conferences_" + uuid.uuid4().hex
        cls.connection = psycopg.connect(os.environ["POSTERS_TEST_DATABASE_URL"], autocommit=True)
        cls.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema)))
        cls.addClassCleanup(cls.cleanup_schema)
        cls.connection.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(cls.schema)))
        cls.connection.execute((ROOT / "schema/schema.sql").read_text())
        cls.store = conferences.ConferenceStore("POSTERS_TEST_DATABASE_URL", cls.schema)

    @classmethod
    def cleanup_schema(cls) -> None:
        cls.connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema)))
        cls.connection.close()

    def setUp(self) -> None:
        self.connection.execute('TRUNCATE "user", conference RESTART IDENTITY CASCADE')
        self.connection.execute("""
            INSERT INTO "user" (uid, name, username, email, password, pwd_updated, created, updated)
            VALUES (7, 'Ada', 'ada', 'ada@example.com', 'hash', now(), now(), now())
        """)
        self.pid = self.connection.execute(
            "INSERT INTO poster (uid, created) VALUES (7, now()) RETURNING pid"
        ).fetchone()[0]

    async def test_selected_web_conference_is_stored_once_searchable_and_attached(self) -> None:
        found = conference("Boston Chemistry Meeting")
        stored = await self.store.select(7, self.pid, found)
        self.assertIsNotNone(stored.cid)
        again = await self.store.select(7, self.pid, conference("boston  chemistry meeting"))
        self.assertEqual(again.cid, stored.cid)
        row = self.connection.execute(
            "SELECT count(*), min(administrator), min(notify), min(hideafter), bool_and(verified IS NULL) FROM conference"
        ).fetchone()
        self.assertEqual(row, (1, "", "never", 0, True))
        self.assertEqual(self.connection.execute("SELECT cid FROM poster WHERE pid = %s", (self.pid,)).fetchone()[0], stored.cid)
        results = await self.store.search("chemistry conference in Denver")
        self.assertEqual([item.cid for item in results], [stored.cid])
        self.assertEqual(results[0].details, {"event_type": "conference"})
        self.assertEqual(await self.store.search("the and"), [])

    async def test_purchased_or_foreign_posters_cannot_be_attached(self) -> None:
        self.connection.execute("INSERT INTO purchase VALUES (%s, 7, 10, 'test', now())", (self.pid,))
        with self.assertRaises(LookupError):
            await self.store.select(7, self.pid, conference("Boston Chemistry Meeting"))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM conference").fetchone()[0], 0)
        with self.assertRaises(LookupError):
            await self.store.select(7, 999, conference("Boston Chemistry Meeting", 123))

    async def test_poster_conference_is_nullable_and_references_a_conference(self) -> None:
        self.assertIsNone(self.connection.execute(
            "SELECT cid FROM poster WHERE pid = %s", (self.pid,)
        ).fetchone()[0])
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            self.connection.execute(
                "UPDATE poster SET cid = %s WHERE pid = %s", (123, self.pid)
            )

    async def test_stored_conference_selection_and_reselection_update_poster(self) -> None:
        first = await self.store.select(7, self.pid, conference("First Meeting"))
        second = await self.store.select(7, self.pid, conference("Second Meeting"))
        for selected in (first, second):
            with self.subTest(cid=selected.cid):
                result = await self.store.select(7, self.pid, selected)
                self.assertEqual(result.cid, selected.cid)
                self.assertEqual(self.connection.execute(
                    "SELECT cid FROM poster WHERE pid = %s", (self.pid,)
                ).fetchone()[0], selected.cid)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM conference"
        ).fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
