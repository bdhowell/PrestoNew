import Levenshtein
import re
import string


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

PUNCTUATION_TRANSLATION_TABLE = str.maketrans('', '', string.punctuation)


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

def add_the(name: str, leading: bool = True) -> str:
    return name if name.split()[0].endswith("'s") or name.split()[0].endswith("s") else f"The {name}" if leading else f"the {name}"

def pluralize(n: int, es=False):
    if es:
        return "es" if n == 1 else ""

    return "s" if n != 1 else ""

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
def clean_string(s: str, spaces="") -> str:
    return  (s.replace(" ", spaces).translate(PUNCTUATION_TRANSLATION_TABLE) if spaces else s).lower().strip()

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
