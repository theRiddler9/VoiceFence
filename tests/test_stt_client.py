import pytest

from src.stt_client import TranscriptEvent, VoicePipeline, extract_address


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


def test_overlap_emits_one_immediate_barge_in_per_segment():
    calls = []
    events = []
    pipeline = VoicePipeline(
        lambda: calls.append("barge-in"),
        lambda address: None,
        events.append,
    )

    pipeline.set_assistant_speaking(True)
    pipeline.on_user_speech_started(timestamp=20.0)
    pipeline.on_user_speech_started(timestamp=20.1)

    assert calls == ["barge-in"]
    assert events[-1] == {"event": "barge-in-detected", "timestamp": 20.0}

    pipeline.on_user_speech_ended()
    pipeline.on_user_speech_started(timestamp=21.0)

    assert calls == ["barge-in", "barge-in"]


def test_no_barge_in_is_emitted_when_assistant_is_not_speaking():
    calls = []
    events = []
    pipeline = VoicePipeline(lambda: calls.append("barge-in"), lambda address: None, events.append)

    pipeline.on_user_speech_started(timestamp=20.0)

    assert calls == []
    assert events == []


def test_duplicate_speech_start_after_assistant_starts_does_not_barge_in():
    calls = []
    events = []
    pipeline = VoicePipeline(lambda: calls.append("barge-in"), lambda address: None, events.append)

    pipeline.on_user_speech_started(timestamp=20.0)
    pipeline.set_assistant_speaking(True)
    pipeline.on_user_speech_started(timestamp=20.1)

    assert calls == []
    assert events == []


def test_interim_correction_is_not_duplicated_by_final_transcript():
    addresses = []
    pipeline = VoicePipeline(lambda: None, addresses.append)

    pipeline.on_user_speech_started()
    pipeline.on_transcript(TranscriptEvent("make it 221B Baker Street", False, 1.0))
    pipeline.on_transcript(TranscriptEvent("make it 221B Baker Street", True, 1.2))

    assert addresses == ["221B Baker Street"]


def test_later_different_correction_in_same_segment_is_emitted():
    addresses = []
    pipeline = VoicePipeline(lambda: None, addresses.append)

    pipeline.on_user_speech_started()
    pipeline.on_transcript(TranscriptEvent("make it 221B Baker Street", False, 1.0))
    pipeline.on_transcript(TranscriptEvent("make it 10 Downing Street", False, 1.1))

    assert addresses == ["221B Baker Street", "10 Downing Street"]


def test_anonymous_late_final_stays_deduplicated_across_next_speech_start():
    """Catches a no-item-id final that arrives after the next VAD segment starts."""
    addresses = []
    pipeline = VoicePipeline(lambda: None, addresses.append)

    pipeline.on_user_speech_started(timestamp=30.0)
    pipeline.on_transcript(TranscriptEvent("make it 221B Baker Street", False, 30.1))
    pipeline.on_user_speech_ended(timestamp=30.2)
    pipeline.on_user_speech_started(timestamp=30.3)
    pipeline.on_transcript(TranscriptEvent("make it 221B Baker Street", True, 30.4))

    assert addresses == ["221B Baker Street"]


def test_anonymous_repeat_after_matching_final_in_a_later_segment_is_emitted():
    """Catches session-wide anonymous dedup suppressing a legitimate repeat."""
    addresses = []
    pipeline = VoicePipeline(lambda: None, addresses.append)

    pipeline.on_user_speech_started(timestamp=40.0)
    pipeline.on_transcript(TranscriptEvent("make it 221B Baker Street", False, 40.1))
    pipeline.on_transcript(TranscriptEvent("make it 221B Baker Street", True, 40.2))
    pipeline.on_user_speech_ended(timestamp=40.3)
    pipeline.on_user_speech_started(timestamp=40.4)
    pipeline.on_transcript(TranscriptEvent("make it 221B Baker Street", True, 40.5))

    assert addresses == ["221B Baker Street", "221B Baker Street"]


def test_anonymous_corrections_can_return_to_a_previous_address_in_one_segment():
    """Catches address-keyed dedup suppressing an A-to-B-to-A correction."""
    addresses = []
    pipeline = VoicePipeline(lambda: None, addresses.append)

    pipeline.on_user_speech_started(timestamp=50.0)
    pipeline.on_transcript(TranscriptEvent("make it 1 First Street", False, 50.1))
    pipeline.on_transcript(TranscriptEvent("make it 10 Downing Street", False, 50.2))
    pipeline.on_transcript(TranscriptEvent("make it 1 First Street", False, 50.3))

    assert addresses == ["1 First Street", "10 Downing Street", "1 First Street"]


def test_turn_dedup_cache_evicts_the_least_recent_turn_at_its_bound():
    """Catches unbounded item-id retention in a process that handles many calls."""
    addresses = []
    pipeline = VoicePipeline(lambda: None, addresses.append)

    for number in range(VoicePipeline.MAX_TURN_DEDUP_ENTRIES + 1):
        pipeline.on_transcript(
            TranscriptEvent(
                f"make it {number} Example Street",
                True,
                float(number),
                turn_id=f"turn-{number}",
            )
        )

    pipeline.on_transcript(
        TranscriptEvent("make it 0 Example Street", True, 99.0, turn_id="turn-0")
    )

    assert addresses[-1] == "0 Example Street"
    assert len(addresses) == VoicePipeline.MAX_TURN_DEDUP_ENTRIES + 2


def test_failed_interim_callback_allows_identical_final_to_retry():
    """Catches dedup state being committed before the callback accepts intent."""
    attempts = []
    events = []

    def accept_on_second_attempt(address):
        attempts.append(address)
        if len(attempts) == 1:
            raise RuntimeError("orchestrator unavailable")

    pipeline = VoicePipeline(
        lambda: None,
        accept_on_second_attempt,
        events.append,
    )

    with pytest.raises(RuntimeError, match="orchestrator unavailable"):
        pipeline.on_transcript(
            TranscriptEvent(
                "make it 221B Baker Street",
                False,
                60.0,
                turn_id="retry-turn",
            )
        )

    pipeline.on_transcript(
        TranscriptEvent(
            "make it 221B Baker Street",
            True,
            60.1,
            turn_id="retry-turn",
        )
    )

    assert attempts == ["221B Baker Street", "221B Baker Street"]
    assert events[0] == {
        "event": "address-intent-error",
        "timestamp": 60.0,
        "address": "221B Baker Street",
        "transcript": "make it 221B Baker Street",
        "is_final": False,
        "turn_id": "retry-turn",
        "error": "orchestrator unavailable",
    }
    assert events[-1]["event"] == "address-intent"


def test_dedup_uses_canonical_address_key_but_preserves_callback_text():
    """Catches case-only transcript revisions triggering duplicate updates."""
    addresses = []
    pipeline = VoicePipeline(lambda: None, addresses.append)

    pipeline.on_transcript(
        TranscriptEvent(
            "make it 221B Baker Street",
            False,
            61.0,
            turn_id="retry-turn",
        )
    )
    pipeline.on_transcript(
        TranscriptEvent(
            "make it 221b baker street",
            True,
            61.1,
            turn_id="retry-turn",
        )
    )

    assert addresses == ["221B Baker Street"]


def test_blank_transcript_emits_structured_ignored_diagnostic():
    """Catches silently ignored blank provider transcripts."""
    addresses = []
    events = []
    pipeline = VoicePipeline(lambda: None, addresses.append, events.append)

    pipeline.on_transcript(
        TranscriptEvent("  \t ", False, 62.0, turn_id="blank-turn")
    )

    assert addresses == []
    assert events == [
        {
            "event": "transcript-ignored",
            "timestamp": 62.0,
            "reason": "blank",
            "is_final": False,
            "turn_id": "blank-turn",
        }
    ]
