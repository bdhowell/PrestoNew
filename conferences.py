from copy import deepcopy
from common import add_the


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


def show_conference(conf):
    country = conf['conference_location']['country']
    if country == "United States":
        country = ""
    else:
        country = f"{country}, "

    print(f"{add_the(conf['conference_name'])}[bright_yellow], in [/bright_yellow]{conf['conference_location']['city']}[bright_yellow], [/bright_yellow]{conf['conference_location']['state_province']}[bright_yellow], [/bright_yellow]{country}[bright_yellow]from [/bright_yellow]{format_date(conf['conference_start_date'])}[bright_yellow] to [/bright_yellow]{format_date(conf['conference_end_date'])}")

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
