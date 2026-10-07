import copy
import datetime
import json
from common import add_the, pluralize
from openai import interact_with_openai, SYSTEM_PROMPT
from num2words import num2words
from dateutil.parser import parse as date_parse

CONFERENCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "conference_name",
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
            "enum": ["inches", "centimeters", "millimeters"],
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

def get_conference_info(criteria) -> list|None:
        conferences_prompt = f"""
## INSTRUCTIONS
Act as an agentic web researcher. Search the web, iteratively follow promising results, and cross-check details against authoritative conference or fair websites. 
Return every relevant conference or fair you find, not just the first result.

## CONTEXT
Find conferences and fairs that meet the criteria provided in the input data. 
When a conference or fair has a poster session, investigate its official poster guidelines and capture the requirements below.
Prefer conferences that occur after the current date.

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

        response = interact_with_openai(conferences_prompt, schema=conferences_response)
        conferences = json.loads(response)["conferences"]

        past_conferences = []
        future_conferences = []

        if conferences:
            for conference in conferences:
                if conference['conference_start_date']:
                    conference['conference_start_date'] = date_parse(conference['conference_start_date'], fuzzy=True).date()

                    if conference['conference_start_date'] < datetime.now().date():
                        past_conferences.append(conference)

                        conferences.remove(conference)
                    else:
                        future_conferences.append(conference)

                if conference['conference_end_date']:
                    conference['conference_end_date'] = date_parse(conference['conference_end_date'], fuzzy=True).date()

                # unify attributes based on SINGULARITY mapping

                for dimension, sources in SINGULARITY.items():
                    for attribute in sources:
                        if attribute in conference:
                            conference[dimension] = conference[attribute]

                if conference['poster_units'] == "centimeters":
                    conference['poster_units'] = "cm"
                elif conference['poster_units'] == "inches":
                    conference['poster_units'] = "in"

                poster_criteria['units'] = conference['poster_units']
 
            return future_conferences

        print("> [bright_red]I'm sorry, I wasn't able to find any matching conferences or fairs that meet your criteria.[/bright_red]")

        if past_conferences:
            pastcount = len(past_conferences)
            print(f"> [bright_yellow]However, I did find {num2words(pastcount)} past conference{pluralize(pastcount)} that matched your information:[/bright_yellow]")
            print("> [bright_yellow]These are the past conferences that matched your information:[/bright_yellow]")

            for past_conf in past_conferences:
                print(f"  - {past_conf['conference_name']} ({past_conf['conference_start_date']} - {past_conf['conference_end_date']})")

            print()

        return None

def get_important_details(conference_info):
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

        return json.loads(interact_with_openai(intersection_prompt, schema=intersection_schema))
