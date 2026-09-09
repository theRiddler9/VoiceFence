#!/usr/bin/env python3
"""
Standalone, repeatable evidence run. This is the script Abhinav's spec
asks for: something that can be re-run identically as many times as
needed, with no human talking into a microphone, producing a log file
and a report derived only from that log.

Usage:
    python -m tests.run_scenario_report
    python -m tests.run_scenario_report --runs 20 --lookup-delay 1.5
    python -m tests.run_scenario_report --runs 10 --out-dir tests/logs

Defaults use realistic-ish delays (a real lookup takes real time) rather
than the compressed timing test_interruption_scenario.py uses for a fast
pytest run — this is meant to be the actual evidence artifact you hand
to judges, not a CI smoke test.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

from tests.event_log import EventLogger
from tests.report import write_report
from tests.scenario import ScenarioConfig, run_interruption_scenario


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10, help="number of scripted runs (default: 10)")
    parser.add_argument("--lookup-delay", type=float, default=1.5, help="OrderStore artificial delay, seconds")
    parser.add_argument("--interrupt-after", type=float, default=0.5,
                         help="seconds after turn 1 starts before the scripted interrupt fires")
    parser.add_argument("--max-silence-ms", type=float, default=300.0,
                         help="pass/fail budget for time-to-silence after interrupt")
    parser.add_argument("--out-dir", type=str, default="tests/logs", help="directory for log + report")
    parser.add_argument("--tag", type=str, default=None,
                         help="label for this sweep, used in filenames (default: timestamp-based)")
    args = parser.parse_args(argv)

    tag = args.tag or uuid.uuid4().hex[:8]
    out_dir = Path(args.out_dir)
    log_path = out_dir / f"scenario_{tag}.jsonl"
    report_path = out_dir / f"scenario_{tag}_report.txt"

    cfg = ScenarioConfig(
        lookup_delay_seconds=args.lookup_delay,
        interrupt_after_seconds=args.interrupt_after,
        max_silence_ms=args.max_silence_ms,
    )

    print(f"Running {args.runs} scripted interruption scenario(s) -> {log_path}")
    logger = EventLogger(log_path, run_id=tag)
    try:
        for i in range(args.runs):
            result = run_interruption_scenario(f"{tag}-run-{i}", logger, cfg)
            status = "PASS" if result.passed else "FAIL"
            print(f"  run {i + 1}/{args.runs}: {status}"
                  + (f" ({result.failures})" if not result.passed else
                     f" (silence in {result.time_to_audio_silence_ms:.1f}ms)"))
    finally:
        logger.close()

    report_text = write_report(log_path, report_path)
    print()
    print(report_text)
    print()
    print(f"Report written to {report_path}")

    # Non-zero exit on any failure so this can gate a CI step if wanted.
    events_had_failure = "FAIL" in report_text.splitlines()[-1]
    return 1 if events_had_failure else 0


if __name__ == "__main__":
    sys.exit(main())
