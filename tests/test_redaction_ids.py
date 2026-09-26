"""Correlation IDs (run_id, ticket_id) survive redaction wherever they appear, keyed or in free text.

The account-number pattern masks any 5 to 12 digit run. A random UUID whose first segment happens to be
all digits (eight of them) looks exactly like one, so a UUID quoted in a sentence — a model telling a user
"ticket 12345678-371f-...", a log line naming a run — used to come out as "[REDACTED]-371f-...", matching
no ticket file. Real account numbers must still be masked, including right next to a UUID.
"""

from cua.config import Config
from cua.redaction import REDACTED, Redactor

DIGIT_FIRST = "12345678-371f-4bfb-978b-fe9bfc3720c2"  # a UUID whose first segment is all digits
ALL_DIGIT_MIDDLE = "abcdef12-1234-4bfb-9788-fe9bfc372012"


def redactor(secrets=None):
    return Redactor(Config(), secrets=secrets or [])


def test_a_uuid_in_free_text_is_kept_whole_even_when_a_segment_is_all_digits():
    text = f"An escalation ticket (#{DIGIT_FIRST}) has been opened."
    assert redactor().redact(text) == text


def test_a_uuid_is_kept_in_any_position_and_case():
    for uuid in (DIGIT_FIRST, DIGIT_FIRST.upper(), ALL_DIGIT_MIDDLE):
        assert redactor().redact(f"ref {uuid}") == f"ref {uuid}"
        assert redactor().redact(uuid) == uuid
        assert redactor().redact(f"{uuid}: done") == f"{uuid}: done"


def test_an_account_number_next_to_a_uuid_is_still_masked():
    text = f"account 13566 opened ticket {DIGIT_FIRST} for account #13677"
    assert redactor().redact(text) == f"account {REDACTED} opened ticket {DIGIT_FIRST} for account #{REDACTED}"


def test_two_uuids_and_numbers_in_one_string():
    a, b = DIGIT_FIRST, ALL_DIGIT_MIDDLE
    assert redactor().redact(f"{a} then 99999 then {b} then 12345678") == f"{a} then {REDACTED} then {b} then {REDACTED}"


def test_text_that_only_looks_a_little_like_a_uuid_is_still_masked():
    # not a well-formed UUID: nothing to protect, so the number pattern applies as usual
    assert redactor().redact("12345678-371f-4bfb-978b") == f"{REDACTED}-371f-4bfb-978b"


def test_a_secret_is_still_removed_even_next_to_a_uuid():
    assert redactor(["hunter2"]).redact(f"pw hunter2 for {DIGIT_FIRST}") == f"pw {REDACTED} for {DIGIT_FIRST}"


def test_keyed_ids_are_still_exempt_and_nested_free_text_is_protected():
    out = redactor().redact({"ticket_id": DIGIT_FIRST, "note": f"see {DIGIT_FIRST}", "account": "13566"})
    assert out == {"ticket_id": DIGIT_FIRST, "note": f"see {DIGIT_FIRST}", "account": REDACTED}
