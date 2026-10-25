"""Checks and converts values typed into HTML forms before they reach AWS.

Input fields are described with small dictionaries, for example:
    {"name": "hours", "label": "Hours to look back", "type": "number", "default": 24, "min": 1, "max": 168}
Supported types: "text", "number", and "region".
"""

import re  # Regular expressions, used to check region names.

# Matches region names like "us-east-1" or "ap-southeast-2".
REGION_PATTERN = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d{1,2}$")

# Longest text accepted in any field, so nobody can send huge values.
MAX_TEXT_LENGTH = 200


def clean_inputs(input_fields, form_data):
    """Validates form values against their field descriptions.

    input_fields: a list of field dictionaries (see the top of this file).
    form_data: the submitted form, a dictionary-like object of field name to text.
    Returns: a dictionary of field name to cleaned value, ready to pass to a Python function.
    Raises: ValueError with a readable message if any value is invalid.
    """
    cleaned = {}
    for field in input_fields:
        # Reads the submitted text and removes spaces from both ends.
        raw_value = str(form_data.get(field["name"], "")).strip()
        cleaned[field["name"]] = clean_one_value(field, raw_value)
    return cleaned


def clean_one_value(field, raw_value):
    # The label is used in error messages because it is the text the user sees on the form.
    label = field["label"]

    # An empty field uses its default value, or is rejected if it is required.
    if raw_value == "":
        if field.get("required"):
            raise ValueError(f"{label} is required.")
        return field.get("default")

    if len(raw_value) > MAX_TEXT_LENGTH:
        raise ValueError(f"{label} is too long.")

    if field["type"] == "number":
        return clean_number(field, raw_value)

    if field["type"] == "region":
        if not REGION_PATTERN.match(raw_value):
            raise ValueError(f"{label} must look like an AWS region, for example us-east-1.")

    # Text and valid region values are returned unchanged.
    return raw_value


def clean_number(field, raw_value):
    label = field["label"]
    # Only whole numbers are accepted; int() raises ValueError for text like "abc".
    try:
        number = int(raw_value)
    except ValueError:
        raise ValueError(f"{label} must be a whole number.")

    # Enforces optional minimum and maximum limits.
    if "min" in field and number < field["min"]:
        raise ValueError(f"{label} must be at least {field['min']}.")
    if "max" in field and number > field["max"]:
        raise ValueError(f"{label} must be at most {field['max']}.")
    return number
