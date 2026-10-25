"""Tests for the lab form checks and the document upload checks (core/form_checks.py)."""

import pytest

from core.form_checks import MAX_DOCUMENT_BYTES, check_document, make_safe_filename, validate_submission

VALID = {"username": "student_1", "first_name": "Mary-Jane", "last_name": "O'Neil",
         "mobile_number": "01234", "email": "mary@example.com"}


def test_valid_form_has_no_errors():
    values, errors = validate_submission(VALID)
    assert errors == {}
    # The mobile number stays text, so the leading zero is kept.
    assert values["mobile_number"] == "01234"


def test_missing_required_fields():
    values, errors = validate_submission({})
    assert set(errors) == {"username", "first_name", "last_name", "mobile_number", "email"}
    assert errors["username"] == "Username is required."


@pytest.mark.parametrize("email", ["not-an-email", "a@b", "a b@example.com", "@example.com", "a@example.com\n"])
def test_invalid_email(email):
    # Note: "a@example.com\n" becomes valid after strip(), so it is checked separately below.
    values, errors = validate_submission(dict(VALID, email=email))
    if email.strip() == "a@example.com":
        assert "email" not in errors
    else:
        assert "valid email" in errors["email"]


@pytest.mark.parametrize("mobile", ["1234", "123456", "12a45", "12 34", "١٢٣٤٥"])
def test_invalid_mobile_number(mobile):
    # The last value is "12345" written in Arabic-Indic digits, which must be rejected.
    values, errors = validate_submission(dict(VALID, mobile_number=mobile))
    assert "exactly 5 digits" in errors["mobile_number"]


def test_invalid_names_and_username():
    values, errors = validate_submission(dict(VALID, first_name="R2D2", last_name="-Smith", username="bad name!"))
    assert "letters" in errors["first_name"]
    assert "letters" in errors["last_name"]
    assert "username" in errors


def test_safe_filename_blocks_path_traversal():
    assert make_safe_filename("..\\..\\Windows\\evil.TXT") == "evil.txt"
    assert make_safe_filename("../../etc/passwd.pdf") == "passwd.pdf"
    assert make_safe_filename("My Report (final).pdf") == "My_Report__final.pdf"
    assert make_safe_filename("...hidden.pdf") == "hidden.pdf"  # Leading dots are removed.
    assert make_safe_filename("().pdf") == "document.pdf"  # Nothing usable left, so a default name is used.


def test_document_exactly_5_mb_is_accepted():
    document = check_document("notes.txt", b"a" * MAX_DOCUMENT_BYTES)
    assert document["size"] == 5 * 1024 * 1024
    assert document["content_type"] == "text/plain"


def test_document_over_5_mb_is_rejected():
    with pytest.raises(ValueError, match="larger than 5 MB"):
        check_document("notes.txt", b"a" * (MAX_DOCUMENT_BYTES + 1))


def test_document_type_checks():
    assert check_document("report.pdf", b"%PDF-1.7 ...")["content_type"] == "application/pdf"
    assert check_document("data.csv", b"a,b\n1,2\n")["name"] == "data.csv"
    with pytest.raises(ValueError, match="Only PDF"):
        check_document("virus.exe", b"MZ...")
    with pytest.raises(ValueError, match="real PDF"):
        check_document("fake.pdf", b"not a pdf")
    with pytest.raises(ValueError, match="real Word"):
        check_document("fake.docx", b"plain text")
    with pytest.raises(ValueError, match="binary"):
        check_document("binary.txt", b"abc\x00def")
    with pytest.raises(ValueError, match="empty"):
        check_document("empty.txt", b"")
