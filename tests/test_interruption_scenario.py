"""
Runs the scripted "slow lookup, then mid-flight correction" scenario
under pytest. Uses compressed (but still real) timing so the suite stays
fast — the standalone run_scenario_report.py script is what you reach
for to do the full 10+ run sweep at realistic delays for the actual
evidence report.

No microphone, no real Rime call, no human — see tests/fakes.py.
"""
import uuid

import pytest

from tests.event_log import EventLogger
from tests.scenario import ScenarioConfig, run_interruption_scenario


FAST_CFG = ScenarioConfig(
    lookup_delay_seconds=0.3,
    lookup_poll_interval=0.01,
    speak_num_chunks=20,
    speak_chunk_size=320,
    speak_chunk_delay=0.015,
    interrupt_after_seconds=0.1,
    max_silence_ms=300.0,
)


@pytest.fixture
def logger(tmp_path):
    log_path = tmp_path / "scenario_events.jsonl"
    logger = EventLogger(log_path, run_id="pytest-session")
    yield logger
    logger.close()


def test_single_interruption_scenario_passes(logger):
    result = run_interruption_scenario(str(uuid.uuid4()), logger, FAST_CFG)
    assert result.passed, f"scenario failed: {result.failures}"
    assert result.lookup1_status == "cancelled"
    assert result.speak1_status == "cancelled"
    assert result.lookup2_status == "completed"
    assert result.speak2_status == "completed"
    assert result.stale_address_leaked is False
    assert result.time_to_audio_silence_ms is not None
    assert result.time_to_audio_silence_ms < FAST_CFG.max_silence_ms


@pytest.mark.parametrize("run_index", range(10))
def test_repeated_interruption_scenario_passes(logger, run_index):
    """Abhinav's spec: one successful run proves nothing — run it many
    times. This runs 10 back-to-back under pytest; run_scenario_report.py
    is for the larger, realistically-timed evidence sweep."""
    result = run_interruption_scenario(f"pytest-repeat-{run_index}", logger, FAST_CFG)
    assert result.passed, f"run {run_index} failed: {result.failures}"


def test_back_to_back_interrupts_do_not_confuse_the_system(logger):
    """Akash's spec explicitly calls out 'must survive rapid back-to-back
    interruptions without getting confused' as the case where most bugs
    hide. Run the scenario twice against the same logger in a row and
    confirm both are independently correct."""
    r1 = run_interruption_scenario("back-to-back-1", logger, FAST_CFG)
    r2 = run_interruption_scenario("back-to-back-2", logger, FAST_CFG)
    assert r1.passed, f"first run failed: {r1.failures}"
    assert r2.passed, f"second run failed: {r2.failures}"
