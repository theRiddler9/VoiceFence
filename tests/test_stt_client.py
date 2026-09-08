from src.stt_client import TranscriptEvent, extract_address


def test_extracts_address_from_interim_transcript():
    event = TranscriptEvent("change my address to 42 Wallaby Way, Sydney", False, 12.5)
    intent = extract_address(event)
    assert intent.address == "42 Wallaby Way, Sydney"
    assert intent.transcript == event.text
    assert intent.is_final is False
    assert intent.timestamp == 12.5


def test_latest_explicit_correction_wins():
    event = TranscriptEvent(
        "change my address to 42 Wallaby Way, actually make it 221B Baker Street, London",
        True,
        13.0,
    )
    assert extract_address(event).address == "221B Baker Street, London"


def test_latest_of_multiple_explicit_corrections_wins():
    event = TranscriptEvent(
        "change my address to 1 First St, actually make it 2 Second St, actually make it 3 Third St",
        True,
        13.5,
    )
    assert extract_address(event).address == "3 Third St"


def test_latest_direct_correction_wins_without_actually():
    event = TranscriptEvent(
        "change my address to 1 First St, actually make it 2 Second St, make it 3 Third St",
        True,
        13.6,
    )
    assert extract_address(event).address == "3 Third St"


def test_make_delivery_address_is_supported():
    event = TranscriptEvent("make my delivery address 500 Market Street", True, 13.7)
    assert extract_address(event).address == "500 Market Street"


def test_make_delivery_address_with_to_is_supported():
    event = TranscriptEvent("make my delivery address to 500 Market Street", True, 13.8)
    assert extract_address(event).address == "500 Market Street"


def test_unrelated_make_plan_to_address_is_rejected():
    event = TranscriptEvent("make a plan to address 123 Main St", True, 13.85)
    assert extract_address(event) is None


def test_polite_intervening_words_are_allowed_in_correction():
    event = TranscriptEvent(
        "change my address to 42 Wallaby Way, actually, please make it 221B Baker Street",
        True,
        13.75,
    )
    assert extract_address(event).address == "221B Baker Street"


def test_address_intent_preserves_raw_transcript():
    raw_text = "  change   my address to 42 Wallaby Way  "
    event = TranscriptEvent(raw_text, False, 13.9)
    assert extract_address(event).transcript == raw_text


def test_unrelated_address_word_is_rejected():
    event = TranscriptEvent("I need to address that problem tomorrow", True, 14.0)
    assert extract_address(event) is None
