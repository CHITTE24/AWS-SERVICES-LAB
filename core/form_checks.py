"""Checks the "Welcome to AWS Service Lab" form and the optional document upload.

All checks run on the server. The HTML "required" attributes are only a convenience,
because anyone can bypass the browser and send any data they like.
"""

import os  # splitext() separates a file name from its extension.
import re  # Regular expressions ("patterns") for usernames, mobile numbers, and email addresses.

# The largest document accepted: 5 MB = 5 × 1024 × 1024 bytes.
MAX_DOCUMENT_BYTES = 5 * 1024 * 1024

# The document types we accept, and the Content-Type label stored with the file in S3.
ALLOWED_DOCUMENT_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# 1 to 50 letters, digits, dots, dashes, or underscores. fullmatch() means the WHOLE text must match.
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,50}")
# Exactly five digits. [0-9] is used instead of \d, which would also accept digits from other alphabets.
# The number stays TEXT, so a leading zero such as "01234" is kept.
MOBILE_PATTERN = re.compile(r"[0-9]{5}")
# A simple email check: something@something.something (with no spaces).
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def is_valid_name(text):
    # Letters, plus spaces, hyphens, and apostrophes (for example "Mary-Jane" or "O'Neil"). Must start with a letter.
    if not 1 <= len(text) <= 50 or not text[0].isalpha():
        return False
    return all(character.isalpha() or character in " -'" for character in text)


def validate_submission(form_data):
    """Checks the five text fields.

    form_data: the submitted form (a dictionary-like object).
    Returns: (values, errors). values holds the cleaned text. errors maps a field name to a message
    shown beside that field. An empty errors dictionary means everything is valid.
    """
    # Reads each field and removes spaces from both ends.
    values = {}
    for name in ("username", "first_name", "last_name", "mobile_number", "email"):
        values[name] = str(form_data.get(name, "")).strip()

    errors = {}
    if not values["username"]:
        errors["username"] = "Username is required."
    elif not USERNAME_PATTERN.fullmatch(values["username"]):
        errors["username"] = "Use up to 50 letters, digits, dots, dashes, or underscores."

    # The same rules apply to the first and last name.
    for name, label in (("first_name", "First name"), ("last_name", "Last name")):
        if not values[name]:
            errors[name] = f"{label} is required."
        elif not is_valid_name(values[name]):
            errors[name] = f"{label} may contain only letters, spaces, hyphens, and apostrophes (max 50)."

    if not values["mobile_number"]:
        errors["mobile_number"] = "Mobile number is required."
    elif not MOBILE_PATTERN.fullmatch(values["mobile_number"]):
        errors["mobile_number"] = "Mobile number must be exactly 5 digits (for this lab), for example 01234."

    if not values["email"]:
        errors["email"] = "Email address is required."
    elif len(values["email"]) > 254 or not EMAIL_PATTERN.fullmatch(values["email"]):
        errors["email"] = "Enter a valid email address, for example name@example.com."

    return values, errors


def make_safe_filename(filename):
    """Turns any uploaded file name into a safe one, for example "..\\..\\My Report.PDF" -> "My_Report.pdf"."""
    # Keeps only the last part of the path, so "../../secret.txt" cannot point outside its folder.
    name = filename.replace("\\", "/").split("/")[-1]
    stem, extension = os.path.splitext(name)
    # Replaces anything other than letters, digits, dots, dashes, and underscores with "_".
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem).strip("._")[:90]
    return (stem or "document") + extension.lower()


def check_document(filename, content):
    """Checks an uploaded document's type and size.

    filename: the name the browser sent. It is NOT trusted.
    content: the file's bytes. The route reads at most MAX_DOCUMENT_BYTES + 1 bytes.
    Returns: {"name": safe file name, "content": bytes, "content_type": ..., "size": number of bytes}.
    Raises: ValueError with a message for the user if the file is not acceptable.
    """
    safe_name = make_safe_filename(filename)
    extension = os.path.splitext(safe_name)[1]
    if extension not in ALLOWED_DOCUMENT_TYPES:
        raise ValueError("Only PDF, TXT, DOCX, and CSV documents are accepted.")
    if len(content) == 0:
        raise ValueError("The uploaded file is empty.")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("The document is larger than 5 MB. Please upload a smaller file.")

    # The extension alone can be faked, so the first bytes of the file are checked too.
    if extension == ".pdf" and not content.startswith(b"%PDF-"):
        raise ValueError("This file does not look like a real PDF.")
    if extension == ".docx" and not content.startswith(b"PK\x03\x04"):
        # A .docx file is a ZIP archive, and every ZIP archive starts with "PK".
        raise ValueError("This file does not look like a real Word (.docx) document.")
    if extension in (".txt", ".csv"):
        # Text files must be readable UTF-8 text with no "null" bytes, which only appear in binary files.
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("Text and CSV files must be saved as UTF-8 text.")
        if b"\x00" in content:
            raise ValueError("This file contains binary data, not text.")

    return {"name": safe_name, "content": content, "content_type": ALLOWED_DOCUMENT_TYPES[extension],
            "size": len(content)}
