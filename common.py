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
