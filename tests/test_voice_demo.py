from voice_demo import run_demo


def test_voice_demo_interrupts_stale_turn_and_commits_correction():
    """Catches a demo that allows the interrupted address to commit or speak."""
    result = run_demo()

    assert result["final_order"]["address"] == "221B Baker Street, London"
    assert result["spoken"] == [
        "Sure, I've updated your address to 221B Baker Street, London."
    ]
    assert any(event["event"] == "barge-in-detected" for event in result["events"])
