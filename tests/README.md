# Tests & Evidence

## Layout

| File | What it covers |
|---|---|
| `fakes.py` | Test doubles: `FakeRimeSession`/`FakeRimeResponse` (no real network call, but real chunked timing) and `RecordingSink` (no real audio device, records write/abort timestamps). |
| `event_log.py` | `EventLogger` — append-only structured JSONL log. Every scenario run writes here. `read_events()` is the *only* way numbers get into a report. |
| `scenario.py` | `run_interruption_scenario()` — the scripted "slow lookup starts, user corrects mid-flight" scenario every role doc describes. Acts as the user directly; no microphone needed. Returns a `ScenarioResult` and logs every step. |
| `report.py` | Reads a JSONL log and produces a plain-text pass/fail report with real timing stats (min/max/mean/stdev of time-to-silence). Never accepts numbers as arguments — only a log path. |
| `run_scenario_report.py` | Standalone CLI: runs the scenario N times at realistic delays, writes a log + report. This is the artifact to hand to judges. |
| `test_batch_registry.py`, `test_order_store.py`, `test_rime_speaker.py` | Unit tests per component, including thread-safety and the "cancel mid-flight" cases in isolation. |
| `test_interruption_scenario.py` | Runs the same scripted scenario under pytest, with compressed (but still real) timing, including a 10x repeat and a back-to-back-interrupts case. |

## Running

```bash
# Fast unit + scenario suite (CI-friendly, seconds)
pytest

# Full evidence sweep at realistic delays — the actual report artifact
python -m tests.run_scenario_report --runs 10
python -m tests.run_scenario_report --runs 25 --lookup-delay 2.0 --tag pre_demo_sweep
```

Logs and reports land in `tests/logs/` by default (gitignore this if you
don't want 25-run sweeps checked in).

## Design notes

- **No mocked clock.** The fakes replace the network call and the audio
  device, but timing is always real `time.sleep` / `time.monotonic()`,
  so the "did it go silent within N ms" numbers are measuring the actual
  code path, not a simulated one.
- **Zero tolerance is enforced in code, not just reported.** A single run
  with `stale_address_leaked=True` fails that run outright — the report
  surfaces the count but the scenario itself already treats it as a hard
  failure.
- **Log format is intentionally flat JSONL** so it's greppable/`jq`-able
  without a bespoke parser, and so the report generator has one trusted
  read path (`read_events`) instead of several ad-hoc ones.
