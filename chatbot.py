"""Prompt and structured-output schema for Wikkit conversations."""


from __future__ import annotations

import copy
import hashlib
import json
import random
import re
import string
import sys
import time
import Levenshtein
from datetime import date, datetime
from pathlib import Path

import openai
from dateutil.parser import parse as date_parse
from dotenv import load_dotenv
from num2words import num2words
from rich import print as rich_print
from typing import Any

CACHE_DIR = Path(__file__).resolve().parent / "cache"
CACHE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60

NO_CACHE = "-n" in sys.argv or "--no-cache" in sys.argv
CLEAR_CACHE = "-c" in sys.argv or "--clear-cache" in sys.argv
NO_DELAY = "-d" in sys.argv or "--no-delay" in sys.argv 


def _cache_key(request: dict[str, Any]) -> str:
    payload = json.dumps(request, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def clear_cache() -> None:
    if not CACHE_DIR.exists():
        return
    for cached_file in CACHE_DIR.glob("*.json"):
        cached_file.unlink(missing_ok=True)


def prune_expired_cache(max_age_seconds: int = CACHE_MAX_AGE_SECONDS) -> None:
    if not CACHE_DIR.exists():
        return
    now = time.time()
    for cached_file in CACHE_DIR.glob("*.json"):
        if now - cached_file.stat().st_mtime > max_age_seconds:
            cached_file.unlink(missing_ok=True)


def _init_cache() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if CLEAR_CACHE:
        clear_cache()
    prune_expired_cache()


_init_cache()

# get valid layouts

with open('../specs/layouts.json') as s:
    VALID_LAYOUTS = []
    for layout in json.load(s):
        VALID_LAYOUTS.append(layout.replace('-', ' ').title())

VALID_LAYOUTS_CI = [layout.casefold() for layout in VALID_LAYOUTS]

with open('../specs/sizes.json') as s:
    VALID_SIZES = json.load(s)["poster_dimension_pairs"]

RANDOM_NAMES = [
    "Liam", "Olivia",
    "Noah", "Emma",
    "Oliver", "Charlotte",
    "James", "Amelia",
    "Elijah", "Sophia",
    "William", "Mia",
    "Henry", "Isabella",
    "Lucas", "Ava",
    "Theodore", "Evelyn",
    "Benjamin", "Harper",
    "Mateo", "Luna",
    "Levi", "Camila",
    "Sebastian", "Sofia",
    "Jack", "Eleanor",
    "Daniel", "Elizabeth",
    "Michael", "Gianna",
    "Alexander", "Scarlett",
    "Ethan", "Violet",
    "Jacob", "Aurora",
    "Carter", "Penelope",
    "Owen", "Chloe",
    "Wyatt", "Hazel",
    "John", "Lily",
    "Samuel", "Ella",
    "Owen", "Emily",
    "John", "Chloe",
    "Asher", "Abigail",
    "Ezra", "Aria",
    "Leo", "Penelope",
    "Jackson", "Aurora",
    "Mason", "Hazel",
    "Ethan", "Luna",
    "Hudson", "Avery",
    "Joseph", "Nora",
    "David", "Lily",
    "Jacob", "Ellie",
    "Julian", "Mila",
    "Logan", "Layla",
    "Luke", "Eliana",
    "Luca", "Madison",
    "Matthew", "Isla",
    "Wyatt", "Grace",
    "Aiden", "Nova",
    "Elias", "Zoe",
    "Gabriel", "Lucy",
    "Carter", "Stella",
    "Jayden", "Violet",
    "Isaac", "Aurora",
    "Lincoln", "Hazel",
    "Anthony", "Luna",
    "Jaxon", "Aria",
    "Santigo", "Penelope",
    "Jayden", "Zoey",
    "Miles", "Elena",
    "Charles", "Leah",
]

AGENT_NAME = "Wikkit"
AGENT_THE_NAME = f"{AGENT_NAME}, the PrestoPresentations Wizard"

LAYOUT_TYPES = [
    {
        "name": "one-column",
        "description": "A single column layout.",
    },
    {
        "name": "two-column",
        "description": "A layout with two columns.",
    },
    {
        "name": "three-column",
        "description": "A layout with three columns.",
    },
    {
        "name": "four-column",
        "description": "A layout with four columns.",
    },
    {
        "name": "tri-fold",
        "description": "A 36\"x48\" poster folded into three sections.",
    },
    {
        "name": "one-one-two",
        "description": "A layout with two narrow, and one wide last column.",
    },
    {
        "name": "one-two-one",
        "description": "A layout with two narrow side columns, and a wide central column.",
    },
    {
        "name": "two-one-one",
        "description": "A layout with a wide first column, and two narrow columns.",
    }
]

LAYOUT_NAMES = [layout["name"] for layout in LAYOUT_TYPES]

SYSTEM_PROMPT = f"""
## Instructions
You are {AGENT_THE_NAME}.
You are an expert in preparing conference posters, including layout and sizing, and preparing graphics and text elements.
You guide each customer through preparing their conference poster using helpful and detailed conversations.
"""

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

PUNCTUATION_TRANSLATION_TABLE = str.maketrans('', '', string.punctuation)

SEEKING_ACTIONS = {
    "startover" : "restart",
    "restart" : "restart",
    "reset" : "restart",
    "goback" : "revert",
    "undo" : "revert",
    "revert" : "revert"
}

SEEKING_OPTIONS = {
    ("callme", r"call\.?me\.?"),
    ("mynameis", r"my\.?name\.?is\.?"),
    ("iam", r"i\.?am\.?")
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

KNOW_SHOW_PUNT = {
    "show": ["sizes", "standard", "display", "me"],
    "know": ["know", "size", "be"],
    "punt": ["skip", "later", "after", "not now"]
}

SINGULARITY = {
    "dimensions" : ["allowed_poster_dimensions", "required_poster_dimensions", "maximum_poster_dimensions"],
    "width" : ["allowed_poster_width", "required_poster_width", "maximum_poster_width"],
    "height" : ["allowed_poster_height", "required_poster_height", "maximum_poster_height"],
    "units" : ["poster_units"],
}

load_dotenv()


username = None
important_features = None
conference_info = None
selected_conference = None
confair = None

poster_criteria = {
    "height": None,
    "width": None,
    "layout": None,
    "units": "in",
}

def to_ordinal(n):
    # Check for 11, 12, and 13 because they break the normal pattern
    if 11 <= (n % 100) <= 13:
        suffix = 'th'
    else:
        # Look at the very last digit
        last_digit = n % 10
        if last_digit == 1:
            suffix = 'st'
        elif last_digit == 2:
            suffix = 'nd'
        elif last_digit == 3:
            suffix = 'rd'
        else:
            suffix = 'th'
            
    return f"{n}{suffix}"

def format_date(datetime_obj):
    day = to_ordinal(datetime_obj.day)
    return datetime_obj.strftime(f"%A, %B {day}, %Y")

def print(text="", fast=False, **kwargs):
    rich_print(text, **kwargs)

    if not NO_DELAY and not fast:
        time.sleep(random.uniform(0.25, 1.00))

def add_the(name: str, leading: bool = True) -> str:
    return name if name.split()[0].endswith("'s") or name.split()[0].endswith("s") else f"The {name}" if leading else f"the {name}"

def pluralize(n: int, es=False):
    if es:
        return "es" if n == 1 else ""

    return "s" if n != 1 else ""

def clean_string(s: str, spaces="") -> str:
    return  (s.replace(" ", spaces).translate(PUNCTUATION_TRANSLATION_TABLE) if spaces else s).lower().strip()

def check_seeking(str: str, keywords: list[str]) -> dict|None:
    cleaned_str = clean_string(str, ".")

    for action, pattern in SEEKING_OPTIONS:
        if re.search(pattern, cleaned_str):
            return {"type":"action", "response": action}

    iwantto = "iwantto" in cleaned_str or "iwouldliketo" in cleaned_str
    iwant = "iwant" in cleaned_str or "iwouldlike" in cleaned_str
    ineed = "ineed" in cleaned_str or "irequire" in cleaned_str
    iall = iwantto or iwant or ineed

    if iwantto:
        scope = "todo"
    elif iwant:
        scope = "wants"
    elif ineed:
        scope = "needs"
    else:
        scope = None

    for action, response in SEEKING_ACTIONS.items():
        if cleaned_str == action or (iall and action in cleaned_str):
            return {"type":"action", "response": response, "scope": scope}

    if not iall:
        return None
    
    for keyword in keywords:
        if clean_string(keyword) in cleaned_str:
            return {"type":"caller", "keyword": keyword, "scope": scope }
        
    return None

def clean_response(response: str, other: list[str]|None = None) -> dict|None:
    if not response:
        return None
    
    response = clean_string(response)

    if response in ["no",  "nope", "nah"] or response == "n":
        return {"type": "system", "response": "no"}
    if response in ["yes", "sure", "ok", "okay", "yep"] or response == "y":
        return {"type":"system", "response": "yes"}
    if response in ["idontknow", "notsure", "unsure", "idk", "maybe"]:
        return {"type":"system", "response": "idk"}

    if seeking := check_seeking(response, LAYOUT_NAMES):
        return seeking

    highest_ratio = 0
    best_match = None

    if other:
        for item in other:
            current_ratio = Levenshtein.ratio(response, clean_string(item))

            if current_ratio > highest_ratio:
                highest_ratio = current_ratio
                best_match = item

    if highest_ratio < 0.7:
        best_match = None

    if best_match:
        return {"type": "caller", " match": best_match, "ratio": highest_ratio, "cleaned": response}
    
    return {"type": "unknown", "response": response}

def interact_with_openai(
    prompt: str,
    schema: dict[str, object] | None = None,
    model: str = "gpt-5.6-terra",
    temperature: float = 0.7,
) -> str:
    request: dict[str, Any] = {
        "model": model,
        "instructions": SYSTEM_PROMPT,
        "input": prompt,
        "tools": [{"type": "web_search_preview"}],
    }
    if not model.startswith("gpt-5"):
        request["temperature"] = temperature
    if schema is not None:
        request["text"] = {
            "format": {
                "type": "json_schema",
                "name": "response_schema",
                "schema": schema,
                "strict": True,
            }
        }

    cache_key = _cache_key(request)
    cache_file = _cache_path(cache_key)

    if not NO_CACHE and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))["output_text"]

    client = openai.OpenAI()
    response = client.responses.create(**request)
    output_text = response.output_text.strip()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({"output_text": output_text}), encoding="utf-8")

    return output_text

def show_conference(conf):
    country = conf['conference_location']['country']
    if country == "United States":
        country = ""
    else:
        country = f"{country}, "

    print(f"{add_the(conf['conference_name'])}[bright_yellow], in [/bright_yellow]{conf['conference_location']['city']}[bright_yellow], [/bright_yellow]{conf['conference_location']['state_province']}[bright_yellow], [/bright_yellow]{country}[bright_yellow]from [/bright_yellow]{format_date(conf['conference_start_date'])}[bright_yellow] to [/bright_yellow]{format_date(conf['conference_end_date'])}")

def poster_general(conference: dict) -> None:
    print(f"\n     [green]Conference Name:[/green] {conference['conference_name']}", fast=True)
    print(f"     [green]Conference Location:[/green] {conference['conference_location']['city']}, {conference['conference_location']['state_province']}, {conference['conference_location']['country']}", fast=True)
    print(f"     [green]Conference Dates:[/green] {format_date(conference['conference_start_date'])} [green]to[/green] {format_date(conference['conference_end_date'])}", fast=True)
    print(f"     [green]Conference Subject:[/green] {conference['conference_subject_or_summary']}", fast=True)

def poster_details(conference: dict) -> None:
    if conference.get("dimensions"):
        print(f"     [green]Poster Sizing:[/green] {conference['dimensions']}", fast=True)
        print(f"     [green]Poster Dimensions:[/green] {conference['width']} [green]by[/green] {conference['height']} [green]{conference['units'] if 'units' in conference else ''}[/green]", fast=True)
    if conference.get("poster_layout"):
        print(f"     [green]Poster Layout:[/green] {conference['poster_layout']}", fast=True)
    if conference.get("special_poster_attributes"):
        print(f"     [green]Special Poster Attributes:[/green]\n         {'\n         '.join(conference['special_poster_attributes'])}\n", fast=True)

def poster_features(conference: dict) -> None:
    if conference.get("poster_features"):
        print(f"     [green]Poster Features:[/green] {', '.join(conference['poster_features'])}", fast=True)

def introduce_myself() -> str:
    print(f"\n> [bright_yellow]Hi, I'm [/bright_yellow][bright_magenta]{AGENT_NAME}[/bright_magenta][bright_yellow], the [/bright_yellow][bright_green]Presto[/bright_green][bright_blue]Presentations[/bright_blue][bright_yellow] poster wizard![/bright_yellow]")
    print("> [bright_yellow]With a few minutes and a little magic, we can quickly and easily prepare your conference or fair poster.[/bright_yellow]\n")
    print("> [bright_yellow]To get started, may I have your name? [/bright_yellow]", end="")

    username = input("").strip()

    if not username:
        print("> [bright_yellow]I didn't catch that. Could you please provide your name? [/bright_yellow]")
        username = input("").strip()

    if not username:
        username = random.choice(RANDOM_NAMES)
        print(f"\n> [bright_yellow]I'm sorry, I didn't catch your name. I'll call you [/bright_yellow]{username}[bright_yellow] for now.[/bright_yellow]")

    if random.choice([True, False]):
        print(f"\n> [bright_yellow]Hello, [/bright_yellow]{username}![bright_yellow][/bright_yellow]")
    else:
        print(f"\n> [bright_yellow]Nice to meet you, [/bright_yellow]{username}[bright_yellow]!")

    print("> [bright_yellow]Let's get started on your poster![/bright_yellow]\n")

    print("> [bright_yellow]I'm going to ask you a few questions to help us create the perfect poster for your needs.[/bright_yellow]")
    print("> [bright_yellow]The more information you can provide, the better I can help you.[/bright_yellow]")
    print("> [bright_yellow]And you can relax, you can change or update your poster's requirements, preferences, and content at any time until your poster's commercially printable PDF is generated.[/bright_yellow]\n")

    return username

def get_conference_info(prompt = None, scope="") -> list|None:
    if not scope:
        scope = "conference or fair"

    if prompt is None:
        print(f"> [bright_yellow]Let me begin by asking what you know about the {scope} for which we're creating your poster.[/bright_yellow]")
        prompt = "> [bright_yellow]Please tell me any information you have about your conference or fair, such as its name, subject area, location, or dates: [/bright_yellow]"

    print(prompt, end="")

    confinfo = clean_response(input())

    if not confinfo:
        return None
    
    if confinfo["type"] == "system" and confinfo["response"] in [ "no", "idk" ]:
        return None

    criteria = confinfo["response"]
    
    while True:
        print(f"\n> [bright_yellow]Thanks! Please give me a few moments. I'll do my best to find the {scope} that matches your details.[/bright_yellow]\n")

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

        addmore = clean_response(input("> [bright_yellow]Can you add anything to your conference criteria?[/bright_yellow]"))
        if addmore and addmore["type"] == "system" and addmore["response"] in ["no", "idk"]:
            return None

        additional_info = clean_string(input("> [bright_yellow]Okay, please provide the additional information:[/bright_yellow]"))

        if not additional_info:
            return None

        criteria += ' ' + additional_info
        return None

def get_conference(conferences)->int|None:
    global selected_conference, important_features, confair
    
    selected_conference = None
    important_features = copy.deepcopy(IMPORTANT_FEATURES)

    contcount = 0
    faircount = 0
    
    found = len(conferences)
    confairs = []

    for conf in conferences:
        confname = conf["conference_name"].casefold()
        if "fair" in confname and "conference" not in confname:
            faircount += 1
            confairs.append('fair')
        else:
            contcount += 1
            confairs.append('conference')

    # extent = "this"
    if contcount > 0 and faircount > 0:
        print(f"> [bright_yellow]I found {num2words(contcount)} conference{pluralize(contcount)} and {num2words(faircount)} fair{pluralize(faircount)} that match your information:[/bright_yellow]\n")
        scope = "conference or fair"
        # extent = "one of these"
    elif contcount > 0:
        print(f"> [bright_yellow]I found {num2words(contcount)} conference{pluralize(contcount)} that match{pluralize(contcount, es=True)} your information:[/bright_yellow]\n")
        scope = "conference"        
    elif faircount > 0:
        print(f"> [bright_yellow]I found {num2words(faircount)} fair{pluralize(faircount)} that match{pluralize(faircount, es=True)} your information:[/bright_yellow]\n")  
        scope = "fair"
    else:
        scope = "conference or fair"

    if found == 1:
        show_conference(conferences[0])
        print(f"\n> [bright_yellow]Is this your {scope}?[/bright_yellow] ", end="")

        yn = clean_response(input())
        if yn and yn["response"] == "yes":
            return 0

        print("> [bright_yellow]Okay, can you provide any more details?[/bright_yellow]", end="")
        return None
    
    for idx, conf in enumerate(conferences, start=1):
        print(f"  {idx}. ", end="", fast=True)
        show_conference(conf)
            
    while True:
        print(f"\n> [bright_yellow]{username}, please select the {scope} that best matches your poster requirements by entering the corresponding number.[/bright_yellow]")
        print("> [bright_yellow]If none of these are correct, you can enter '0' to continue without conference information.[/bright_yellow]")
        print(f"\n> [bright_yellow]Enter the number of the {scope}: [/bright_yellow]", end="")
        
        selection = input().strip()

        if selection.isdigit():
            selection = int(selection)
            if selection == 0:
                print("> [bright_yellow]No worries! We can continue without conference information. Let's proceed.[/bright_yellow]\n")
                return None
            
            if 1 <= selection <= found:
                selected_conference = selection - 1
                confair = confairs[selected_conference]
                break

            print(f"> [bright_yellow]I'm sorry, {username}. That is not a valid selection.[/bright_yellow]\n")
        else:
            print("> [bright_yellow]Invalid input. Please enter a valid number corresponding to the conference or fair, or '0' to continue without conference information.[/bright_yellow]\n")

    return selected_conference

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

def is_valid_layout(layout):
    return True

def show_valid_layouts(layouts=VALID_LAYOUTS):
        for i, layout in enumerate(layouts):
            print(f"  [bright_blue]{i+1:>5}.[/bright_blue] {layout}", fast=True)

def parse_coordinates(coordinate_string: str) -> bool:
    if coordinates := re.match(r"^.*(\d+).*?(in|cent|cm)?.+(\d+)*.?(in|cent|cm)", coordinate_string):
        print(f"\n> [bright_yellow]Got 'em, {username}![/bright_yellow]")
        print("> [bright_yellow]We'll use the dimensions you provided.[/bright_yellow]\n")
        
        poster_criteria["width"] = int(coordinates.group(1))
        poster_criteria["height"] = int(coordinates.group(3))
        poster_criteria["units"] = coordinates.group(2) or coordinates.group(4) or poster_criteria["units"] or "in" 

        return True
    return False

def get_poster_layout(conference_info: dict|None)-> int|None:
    choice = None

    if conference_info:
        layouts = conference_info.get("poster_layouts") or []
        orgcount = len(layouts)

        if layouts:
            allowed = []
            for layout in layouts:
                layout = layout.casefold()
                for valid in VALID_LAYOUTS_CI:
                    if Levenshtein.distance(layout, valid) <= 2:
                        allowed.append(layout)

            layouts = allowed

        laycount = len(layouts)

        if orgcount and not laycount:
            print(f"> [bright_yellow]I'm sorry, {username}. It seems that {add_the(conference_info['conference_name'], False)} does not allow any of the standard poster layouts.[/bright_yellow]\n")
            print("> [bright_yellow]I am afraied I cannot help you with your conference poster.[/bright_yellow]")

            sys.exit()

        if laycount == 1:
            print(f"> [bright_yellow]As I understand, [/bright_yellow]{username}[bright_yellow], there is only one allowed poster layout permitted for {add_the(conference_info['conference_name'], False)}: {layouts[0]}.[/bright_yellow]")
            print("> [bright_yellow]So we'll go with that.[/bright_yellow]\n")

            return layouts[0]

        if laycount > 1:
            print(f"> {username}, I found that {add_the(conference_info['conference_name'], False)} allows the following available poster layouts:")
            show_valid_layouts(layouts)

            print("> [bright_yellow]Do you have a preferred poster layout? You can always easily change it later. Enter [/bright_yellow][bright_red]0[/bright_red][bright_yellow] if you don't have a preference and I'll choose one for you.[/bright_yellow]", end="")

            while True:
                choice = input()
                if choice.isdigit():
                    choice = int(choice)

                    if 0 <= choice <= len(layouts):
                        if choice == 0:
                            return None
                        
                        return layouts[choice - 1]
                    
                print(f"> [bright_yellow]I'm sorry, {username}. I didn't understand your answer. Please enter a valid number corresponding to your initial poster layout or '0' if you want me to choose.[/bright_yellow]\n")

    if choice is None:
        print(f"> [bright_yellow]Well,[/bright_yellow] {username}[bright_yellow], it seems that {add_the(conference_info['conference_name'], False)} does not specify any allowed poster layouts.[/bright_yellow]")
        print("> [bright_yellow]I can create any of the following poster layouts for you, or I can use my magic to select one or more alternatives for you later.[/bright_yellow]\n")
    else:
        print(f"> {username}[bright_yellow], I can create any of the following poster layouts: (or Iuse my magic to select one or more alternatives for you later)[/bright_yellow]", end="")

    show_valid_layouts()

    print(f"\n> {username}[bright_yellow], please select your preferred poster layout from the options above.[/bright_yellow]")
    print("> [bright_yellow]Or enter [/bright_yellow][bright_red]0[/bright_red][bright_yellow] if you don't have a preference and I'll choose a layout for us to start with.")
    print("> [bright_yellow]Remember, you can always change the layout later:[/bright_yellow] ", end="")

    while True:
        choice = input()
        if choice.isdigit():
            choice = int(choice)

            if 0 <= choice <= len(VALID_LAYOUTS):
                print("\n> [bright_yellow]Terrific! We're moving right along![/bright_yellow]")
                if choice == 0:
                    return None

                print(f"> [bright_yellow]I'll use the [/bright_yellow]{VALID_LAYOUTS[choice - 1]}[bright_yellow] layout in my poster incantations![/bright_yellow]")
                return choice - 1
        
        print(f"> [bright_yellow]I'm sorry, {username}. I didn't understand your answer.[/bright_yellow]")
        print("> [bright_yellow]Please enter a valid number corresponding to your initial poster layout or '0' if you want me to choose.[/bright_yellow] ", end="")

def get_poster_dimensions(conference_info):
    dimdoing = None

    if conference_info["required_poster_width"] and conference_info["required_poster_height"]:
        poster_criteria["width"] = conference_info["required_poster_width"]
        poster_criteria["height"] = conference_info["required_poster_height"]

        if len(conference_info["required_poster_width"]) > 1 or len(conference_info["required_poster_height"]) > 1:
            print(f"> [bright_yellow]{conference_info['conference_name']} allows multiple poster dimensions:[/bright_yellow]\n")

            while True:
                print("> [bright_yellow]Please select your preferred poster dimensions from the allowed options:[/bright_yellow]")
                for i, (width, height) in enumerate(zip(conference_info["required_poster_width"], conference_info["required_poster_height"])):
                    print(f"  {i+1}. {width} x {height}")

                choice = input()
                if choice.isdigit():
                    choice = int(choice)

                    if 1 <= choice <= len(conference_info["required_poster_width"]):
                        poster_criteria["width"] = conference_info["required_poster_width"][choice - 1]
                        poster_criteria["height"] = conference_info["required_poster_height"][choice - 1]

                        print(f"> [bright_yellow]Thanks, {username}! We're making great progress[/bright_yellow]\n")
                        return

                print(f"> [bright_yellow]I'm sorry, {username}, I didn't understand your answer.")
                print("> [bright_yellow]Please enter valid numbers corresponding to your poster dimensions.[/bright_yellow]\n")
        else:
            print(f"> [bright_yellow]{conference_info['conference_name']} allows poster dimensions of only {poster_criteria['width']} x {poster_criteria['height']}.")
            print("> [bright_yellow]We'll use those dimensions for your poster.[/bright_yellow]\n")

            poster_criteria["width"] = conference_info["required_poster_width"][0]
            poster_criteria["height"] = conference_info["required_poster_height"][0]

            return
    else:
        print(f"\n> {add_the(conference_info['conference_name'])}[bright_yellow] does not specify required poster dimensions.[/bright_yellow]")

        if conference_info["maximum_poster_width"] and conference_info["maximum_poster_height"]:
            print(f"> [bright_yellow]However, the maximum allowed dimensions are [/bright_yellow]{conference_info['maximum_poster_width']}{conference_info['poster_units']}[bright_yellow] x [/bright_yellow]{conference_info['maximum_poster_height']}{conference_info['poster_units']}[bright_yellow].[/bright_yellow]\n")
        else:
            print()

        print("> [bright_yellow]Do you know what size you would like your poster to be? [/bright_yellow]")
        print("> [bright_yellow]You can always choose to skip this step for now and we can decide on your poster's dimensions later:[/bright_yellow] ", end="")
        posize = input().strip()
        if not posize:
            return
        
        if posize and parse_coordinates(posize):
            return

        cleaned = clean_response(posize)
        if cleaned and cleaned["type"] == "system" and cleaned["response"] == "yes":
            dimdoing = "know"
        else:
            print("\n> [bright_yellow]I can show you the standard sizes I can conjure.[/bright_yellow]")
            print("> [bright_yellow]Or we can skip the decision until later, say after you've uploaded your content.[/bright_yellow]\n")

            print(f"> [bright_yellow]What would you like to do about poster dimensions,[/bright_yellow] {username}[bright_yellow]?[/bright_yellow]")
            print("> [bright_yellow]Your wish is my command:[/bright_yellow] ", end="")
            dimaction = input().lower()

            if parse_coordinates(dimaction):
                return

            for key, values in KNOW_SHOW_PUNT.items():
                if any(value in dimaction for value in values):
                    dimdoing = key
                    break

            if not dimdoing:
                idk = clean_response(dimaction)
                if idk and idk["type"] == "system" and idk["response"] == "idk":
                    dimdoing = "punt"
                
        if dimdoing == "punt":
            print(f"\n> [bright_yellow]No problem, [/bright_yellow]{username}[bright_yellow].")
            print("> [bright_yellow]We can decide on your poster dimensions later.[/bright_yellow]\n")
            return # Skip the decision for now

        if dimdoing == "show":
            print(f"\n> [bright_yellow]Here are standard poster sizes for you to consider, [/bright_yellow]{username}[bright_yellow]: [/bright_yellow]\n")

            i = 1
            for sizes in VALID_SIZES:
                for size in sizes:
                    if size[2] == poster_criteria["poster_units"]:
                        if size[0] > size[1]:
                            aspect = "landscape"
                        elif size[0] < size[1]:
                            aspect = "portrait"
                        else:
                            aspect = "square"

                        print(f"  [bright_blue]{i:>5}.[/bright_blue]{size[0]}{conference_info['poster_units']} [bright_yellow]x[/bright_yellow] {size[1]}{conference_info['poster_units']} ({aspect})", fast=True)
                        i += 1

            print()

            while True:
                laynum = input().strip()
                if laynum.isdigit() and 0 <= int(laynum) < len(VALID_SIZES):
                    poster_criteria["width"], poster_criteria["height"] = VALID_SIZES[int(laynum) - 1]
                    return

                print(f"> [bright_yellow]I'm sorry, {username}. I didn't understand your answer.[/bright_yellow]")
                print("> [bright_yellow]Please enter a valid number corresponding to the poster size.[/bright_yellow]\n")

        while True:
            print("> [bright_yellow]Please give me your poster's dimensions: [/bright_yellow]", end="")
            dimensions = input().strip()

            if not dimensions:
                print(f"> [bright_yellow]Okay, {username}, we'll figure the poster dimensions based on your content.[/bright_yellow]")
                poster_criteria["height"] = None
                poster_criteria["width"] = None
                return

            if parse_coordinates(dimensions):
                return

            print(f"> [bright_yellow]I'm sorry, {username}. I didn't understand your answer.[/bright_yellow]")
            print("> [bright_yellow]Please enter valid values for your poster dimensions: [italic]width x height.[/italic][/bright_yellow]\n")


def get_poster_criteria(conference_info):
    # Implement the logic to extract poster criteria from the conference information
    # This is a placeholder implementation
    
    return {
        "height": conference_info.get("poster_height"),
        "width": conference_info.get("poster_width"),
        "layout": conference_info.get("poster_layout"),
        "poster_units": conference_info.get("poster_units"),
    }
            
            # TELL USER ALLOWED POSTER SIZES
            # TELL USER ALLOWED LAYOUTS

            # TELL USER ALLOWED FEATURES
            # REQUIRED FEATURES
            # PROHIBITED FEATURES


#
# Main program execution starts here
#

if __name__ == "__main__":
    random.seed()

    layout = None

    username = introduce_myself()
    conferences = get_conference_info()

    if not conferences:
        print("> [bright_yellow]No worries! We can continue without conference information. Let's proceed.[/bright_yellow]\n")
    else:
        selected_conference = get_conference(conferences)

        conference_info = conferences[selected_conference] if selected_conference is not None else None

        if conference_info:
            poster_criteria = get_poster_criteria(conference_info)

            poster_general(conference_info)  
            poster_details(conference_info)
            
            country = conference_info['conference_location']['country']
            country = "" if country == "United States" else f", {country}"

            print(f"> [bright_yellow]Thank you, [/bright_yellow]{username}[bright_yellow].[/bright_yellow]")
            print(f"> [bright_yellow]You selected[/bright_yellow] {add_the(conference_info['conference_name'], leading=False)} [bright_yellow]in {conference_info['conference_location']['city']}, {conference_info['conference_location']['state_province']}{country}, {format_date(conference_info['conference_start_date'])} to {format_date(conference_info['conference_end_date'])}.[/bright_yellow]\n")

            if important_features := get_important_details(conference_info):
                for category, features in important_features.items():
                    print(f"     [bright_red]{category.capitalize()}:[/bright_red] {', '.join(features)}", fast=True)
                print(fast=True)

            layout = get_poster_layout(conference_info)

    if layout is not None:
        poster_criteria["layout"] = VALID_LAYOUTS[layout]

    dimensions = get_poster_dimensions(conference_info)

    if layout is not None:
        if dimensions:
            print(f"     [bright_red]Layout:[/bright_red] {VALID_LAYOUTS[layout]} ({dimensions['width']} x {dimensions['height']})", fast=True)
        else:
            print(f"     [bright_red]Layout:[/bright_red] {VALID_LAYOUTS[layout]} (not specified)")
    else:
        if dimensions:
            print(f"     [bright_red]Layout:[/bright_red] Not specified ({dimensions['width']} x {dimensions['height']})", fast=True)
        else:
            print("     [bright_red]Layout:[/bright_red] Not specified (not specified)", fast=True)

    print("\n> [bright_yellow]Poster criteria based on conference requirements:[/bright_yellow]\n")

