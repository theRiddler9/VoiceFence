"""
Report generator. Reads a scenario event log (JSONL) and produces a
plain-text pass/fail summary.

Hard rule from Abhinav's spec: "all final reported numbers must come
from the actual log file — never typed in by hand from memory or
guesswork." This module is the only thing allowed to produce the report,
and it only ever reads tests.event_log.read_events() — it never accepts
numbers as arguments.
"""
from __future__ import annotations

import statistics
from pathlib import Path
from typing import List

from tests.event_log import read_events


def build_report(log_path: str | Path) -> str:
    events = read_events(log_path)
    results = [e for e in events if e.get("event") == "scenario_result"]

    lines: List[str] = []
    lines.append("Interruption Scenario — Evidence Report")
    lines.append("=" * 44)
    lines.append(f"Source log: {log_path}")
    lines.append(f"Total scripted runs found: {len(results)}")
    lines.append("")

    if not results:
        lines.append("No scenario_result events found in this log — nothing to report.")
        return "\n".join(lines)

    passed = [r for r in results if r.get("passed")]
    failed = [r for r in results if not r.get("passed")]

    lines.append(f"Passed: {len(passed)} / {len(results)}")
    lines.append(f"Failed: {len(failed)} / {len(results)}")
    lines.append("")

    stale_leaks = [r for r in results if r.get("stale_address_leaked")]
    lines.append(f"Runs where stale address leaked to the store: {len(stale_leaks)}")
    lines.append("  (zero tolerance per spec — any number above 0 here is a failing build)")
    lines.append("")

    silence_times = [
        r["time_to_audio_silence_ms"]
        for r in results
        if isinstance(r.get("time_to_audio_silence_ms"), (int, float))
    ]
    if silence_times:
        lines.append("Time-to-silence after interrupt (ms), from real measured timestamps:")
        lines.append(f"  min:    {min(silence_times):.2f}")
        lines.append(f"  max:    {max(silence_times):.2f}")
        lines.append(f"  mean:   {statistics.mean(silence_times):.2f}")
        if len(silence_times) > 1:
            lines.append(f"  stdev:  {statistics.stdev(silence_times):.2f}")
    else:
        lines.append("Time-to-silence: no measurements recorded.")
    lines.append("")

    if failed:
        lines.append("Failing runs:")
        for r in failed:
            lines.append(f"  - run_id={r.get('run_id')}: {r.get('failures')}")
        lines.append("")

    lines.append("Verdict: " + ("PASS — 0 failures across all runs." if not failed else
                                 f"FAIL — {len(failed)} run(s) failed. See above."))

    return "\n".join(lines)


def write_report(log_path: str | Path, report_path: str | Path) -> str:
    report_text = build_report(log_path)
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text)
    return report_text
