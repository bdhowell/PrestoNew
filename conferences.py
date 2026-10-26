"""Conference and fair lookup, storage, and poster requirements."""

import datetime
import json
import logging
import re
from dataclasses import dataclass, replace

from dateutil.parser import parse as date_parse
from num2words import num2words
from psycopg.types.json import Jsonb

from common import pluralize
from llm import interact_with_openai, SYSTEM_PROMPT
from posters import PosterStore

logger = logging.getLogger(__name__)

CONFERENCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "conference_name",
        "event_type",
        "conference_location",
        "conference_start_date",
        "conference_end_date",
        "conference_subject_or_summary",
        "maximum_poster_dimensions",
        "maximum_poster_width",
        "maximum_poster_height",
        "required_poster_dimensions",
        "required_poster_width",
        "required_poster_height",
        "allowed_poster_dimensions",
        "allowed_poster_width",
        "allowed_poster_height",
        "poster_units",
        "poster_layouts",
        "special_poster_attributes",
    ],
    "properties": {
        "conference_name": {
            "type": "string",
            "description": "Name of the conference or fair",
        },
        "event_type": {
            "type": "string",
            "enum": ["conference", "fair"],
            "description": "Whether the event is a conference or a fair",
        },
        "conference_location": {
            "type": "object",
            "additionalProperties": False,
            "required": ["country", "state_province", "city"],
            "properties": {
                "country": {
                    "type": "string",
                    "description": "Country of the conference or fair",
                },
                "state_province": {
                    "type": "string",
                    "description": "State or province of the conference or fair",
                },
                "city": {
                    "type": "string",
                    "description": "City of the conference or fair",
                },
            },
        },
        "conference_start_date": {
            "type": "string",
            "description": "Start date of the conference or fair",
        },
        "conference_end_date": {
            "type": "string",
            "description": "End date of the conference or fair",
        },
        "conference_subject_or_summary": {
            "type": "string",
            "description": "Conference subject area or short summary",
        },
        "maximum_poster_dimensions": {
            "type": ["string", "null"],
            "description": "Maximum poster dimensions, if specified",
        },
        "maximum_poster_width": {
            "type": ["integer", "null"],
            "description": "Maximum poster width, if specified",
        },
        "maximum_poster_height": {
            "type": ["integer", "null"],
            "description": "Maximum poster height, if specified",
        },
        "required_poster_dimensions": {
            "type": ["array", "null"],
            "items": {"type": "integer"},
            "description": "Required poster dimensions, if specified",
        },
        "required_poster_width": {
            "type": ["array", "null"],
            "items": {"type": "integer"},
            "description": "Required poster widths, if specified",
        },
        "required_poster_height": {
            "type": ["array", "null"],
            "items": {"type": "integer"},
            "description": "Required poster heights, if specified",
        },        
        "allowed_poster_dimensions": {
            "type": ["array", "null"],
            "items": {"type": "integer"},
            "description": "Allowed poster dimensions, if specified",
        },
        "allowed_poster_width": {
            "type": ["array", "null"],
            "items": {"type": "integer"},
            "description": "Allowed poster widths, if specified",
        },
        "allowed_poster_height": {
            "type": ["array", "null"],
            "items": {"type": "integer"},
            "description": "Allowed poster heights, if specified",
        },
        "poster_units": {
            "type": ["string", "null"],
            "enum": ["inches", "centimeters", "millimeters", None],
            "description": "Poster dimensions' units, if specified",
        },
        "poster_layouts": {
            "type": ["array", "null"],
            "items": {
                "type": "string",
                "enum": ["three-column", "four-column", "tri-fold", "results-arena", "flow-layout"]
            },
            "description": "All required or allowed poster layouts, if specified",
        },
        "special_poster_attributes": {
            "type": ["array", "null"],
            "items": {"type": "string"},
            "description": "Any other specified poster requirements or attributes",
        },
    },
}

IMPORTANT_FEATURES = {
    'urls' : None,
    'qrcodes' : None,
    'email_addresses' : None,
    'phone_numbers' : None,
    'social_media' : None,
    'commercial_logos' : None,
    'abstracts' : None,
    'handouts' : None,
}

IMPORTANT_FEATURES_LIST = list(IMPORTANT_FEATURES)

SINGULARITY = {
    "dimensions" : ["allowed_poster_dimensions", "required_poster_dimensions", "maximum_poster_dimensions"],
    "width" : ["allowed_poster_width", "required_poster_width", "maximum_poster_width"],
    "height" : ["allowed_poster_height", "required_poster_height", "maximum_poster_height"],
    "units" : ["poster_units"],
}

poster_criteria = {
    "height": None,
    "width": None,
    "layout": None,
    "units": "in",
}

UNITS = {"inches": "in", "centimeters": "cm", "millimeters": "mm"}


def _parse_date(value: object) -> datetime.date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date_parse(value, fuzzy=True).date()
    except (ValueError, OverflowError):
        logger.warning("Ignoring unparseable conference date %r", value)
        return None


async def get_conference_info(criteria: str) -> list[dict] | None:
    """Search the web for matching upcoming conferences or fairs.

    Returns conferences with a known start date on or after today, or None.
    Each result's dates are `datetime.date`s and its poster requirements are
    unified into `dimensions`, `width`, `height`, and `units`.
    """
    conferences_prompt = f"""
## INSTRUCTIONS
Act as an agentic web researcher. Search the web, iteratively follow promising results, and cross-check details against authoritative conference or fair websites.
Return every relevant conference or fair you find, not just the first result.

## CONTEXT
Find conferences and fairs that meet the criteria provided in the input data.
When a conference or fair has a poster session, investigate its official poster guidelines and capture the requirements below.
Prefer conferences that occur after the current date, {datetime.date.today().isoformat()}.

## INPUT DATA
{criteria}

## CONSTRAINTS
Do not invent information. Use null when a poster requirement is not specified or cannot be verified. Keep special poster attributes concise and factual.
"""

    conferences_response = {
        "type": "object",
        "additionalProperties": False,
        "required": ["conferences"],
        "properties": {
            "conferences": {
                "type": "array",
                "items": CONFERENCE_SCHEMA,
                "description": "All matching conferences or fairs",
            },
        },
    }

    response = await interact_with_openai(conferences_prompt, schema=conferences_response)
    conferences = json.loads(response)["conferences"]

    today = datetime.date.today()
    past_conferences = []
    future_conferences = []

    for conference in conferences:
        conference["conference_start_date"] = _parse_date(conference.get("conference_start_date"))
        conference["conference_end_date"] = (
            _parse_date(conference.get("conference_end_date")) or conference["conference_start_date"]
        )

        # Unify attributes based on the SINGULARITY mapping: the first specified value wins.
        for dimension, sources in SINGULARITY.items():
            conference[dimension] = next(
                (conference[attribute] for attribute in sources if conference.get(attribute) is not None),
                None,
            )
        conference["units"] = UNITS.get(conference["units"], conference["units"])

        if conference["conference_start_date"] is None:
            logger.info("Ignoring conference without a start date: %r", conference.get("conference_name"))
        elif conference["conference_start_date"] < today:
            past_conferences.append(conference)
        else:
            future_conferences.append(conference)

    if future_conferences:
        return future_conferences

    if past_conferences:
        logger.info(
            "Found %s past conference%s but no upcoming ones: %s",
            num2words(len(past_conferences)), pluralize(len(past_conferences)),
            "; ".join(
                f"{past['conference_name']} ({past['conference_start_date']} - {past['conference_end_date']})"
                for past in past_conferences
            ),
        )
    return None


@dataclass(frozen=True)
class Conference:
    """A conference or fair shown in the conference query table."""
    cid: int | None
    name: str
    location: str
    purpose: str
    starts: datetime.date
    ends: datetime.date
    kind: str
    details: dict | None = None

    @property
    def key(self) -> tuple[str, datetime.date]:
        return " ".join(self.name.lower().split()), self.starts

    def public(self) -> dict[str, object]:
        """JSON-safe row data for the client; omits stored requirement details."""
        return {
            "cid": self.cid, "name": self.name, "location": self.location,
            "purpose": self.purpose, "starts": self.starts.isoformat(),
            "ends": self.ends.isoformat(), "kind": self.kind,
        }

    def memory(self) -> dict[str, object]:
        return {**self.public(), "details": self.details}


def _kind(name: str, details: dict | None) -> str:
    kind = (details or {}).get("event_type")
    if kind in ("conference", "fair"):
        return kind
    return "fair" if re.search(r"\bfairs?\b", name, re.IGNORECASE) else "conference"


def from_search_result(result: dict) -> Conference:
    location = result.get("conference_location") or {}
    details = json.loads(json.dumps(result, default=lambda value: value.isoformat()))
    return Conference(
        cid=None,
        name=" ".join(str(result["conference_name"]).split()),
        location=", ".join(
            part.strip() for part in (
                location.get("city"), location.get("state_province"), location.get("country"),
            ) if isinstance(part, str) and part.strip()
        ),
        purpose=(result.get("conference_subject_or_summary") or "").strip(),
        starts=result["conference_start_date"],
        ends=result["conference_end_date"] or result["conference_start_date"],
        kind=_kind(str(result["conference_name"]), details),
        details=details,
    )


class ConferenceStore(PosterStore):
    _COLUMNS = "c.cid, c.name, c.location, c.description, c.starts::date, c.ends::date, c.details"

    @staticmethod
    def _row(row: tuple) -> Conference:
        cid, name, location, purpose, starts, ends, details = row
        details = details if isinstance(details, dict) else None
        return Conference(cid, name, location, purpose, starts, ends, _kind(name, details), details)

    async def search(self, criteria: str, limit: int = 20) -> list[Conference]:
        """Full-text search of visible conferences, matching any of the criteria's terms."""
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "WITH q AS (SELECT nullif(replace(plainto_tsquery('english', %s)::text, ' & ', ' | '), '')"
                "::tsquery AS query) "
                f"SELECT {self._COLUMNS} FROM conference c, q "
                "WHERE c.hidden IS NULL AND q.query IS NOT NULL AND c.search_vector @@ q.query "
                "ORDER BY ts_rank_cd(c.search_vector, q.query) DESC, c.starts, c.cid LIMIT %s",
                (criteria, limit),
            )
            rows = await cursor.fetchall()
        return [self._row(row) for row in rows]

    async def select(self, uid: int, pid: int, conference: Conference) -> Conference:
        """Store the conference if it is new, then attach it to the owner's editable poster."""
        connection = await self._connect()
        async with connection:
            await connection.execute('SELECT uid FROM "user" WHERE uid = %s FOR UPDATE', (uid,))
            if conference.cid is not None:
                cursor = await connection.execute(
                    f"SELECT {self._COLUMNS} FROM conference c WHERE c.cid = %s AND c.hidden IS NULL",
                    (conference.cid,),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise LookupError("That conference is no longer available.")
                conference = self._row(row)
            else:
                name, starts = conference.key
                # Serialize inserts of the same conference so it is stored only once.
                await connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (name,))
                cursor = await connection.execute(
                    f"SELECT {self._COLUMNS} FROM conference c "
                    "WHERE lower(regexp_replace(c.name, '\\s+', ' ', 'g')) = %s AND c.starts::date = %s "
                    "ORDER BY c.cid LIMIT 1",
                    (name, starts),
                )
                row = await cursor.fetchone()
                if row is not None:
                    conference = self._row(row)
                else:
                    cursor = await connection.execute(
                        "INSERT INTO conference (name, description, starts, ends, location, "
                        "administrator, password, notify, details, verified, created, updated, hideafter) "
                        "VALUES (%s, %s, %s, %s, %s, '', '', 'never', %s, NULL, now(), now(), 0) "
                        "RETURNING cid",
                        (conference.name, conference.purpose, conference.starts, conference.ends,
                         conference.location, Jsonb(conference.details)),
                    )
                    row = await cursor.fetchone()
                    assert row is not None
                    conference = replace(conference, cid=row[0])
            cursor = await connection.execute(
                "UPDATE poster p SET cid = %s, updated = now() WHERE p.uid = %s AND p.pid = %s "
                "AND NOT EXISTS (SELECT 1 FROM purchase b WHERE b.pid = p.pid) RETURNING p.pid",
                (conference.cid, uid, pid),
            )
            if await cursor.fetchone() is None:
                await connection.rollback()
                raise LookupError("Your poster can no longer be edited.")
        return conference


_store = ConferenceStore()


def store() -> ConferenceStore:
    return _store

async def get_important_details(conference_info):
    if conference_info['special_poster_attributes']:
        intersection_prompt = f"""
{SYSTEM_PROMPT}

Here is a list of special poster attributes, which ends with ## END:
{'\n'.join(conference_info['special_poster_attributes'])}
## END

Here is a list of important poster features:
{(',').join(IMPORTANT_FEATURES_LIST)}

Return the set of important poster features which are allowed or disallowed by the special poster attributes.
Here is an example:
{{
"allowed": ["abstracts"],
"required": ["institutional_seal"],
"prohibited": ["urls", "qrcodes", "email_addresses", "phone_numbers"]
}}
"""
        intersection_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["allowed", "required", "prohibited"],
            "properties": {
                "allowed": {
                    "type": "array",
                    "description": "Important features allowed by the special poster attributes",
                    "items": {"type": "string", "enum": IMPORTANT_FEATURES_LIST},
                },
                "required": {
                    "type": "array",
                    "description": "Important features required by the special poster attributes",
                    "items": {"type": "string", "enum": IMPORTANT_FEATURES_LIST},
                },
                "prohibited": {
                    "type": "array",
                    "description": "Important features prohibited by the special poster attributes",
                    "items": {"type": "string", "enum": IMPORTANT_FEATURES_LIST},
                },
            },
        }

        return json.loads(
            await interact_with_openai(intersection_prompt, schema=intersection_schema)
        )
