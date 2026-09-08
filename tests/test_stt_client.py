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


def test_unrelated_address_word_is_rejected():
    event = TranscriptEvent("I need to address that problem tomorrow", True, 14.0)
    assert extract_address(event) is None
