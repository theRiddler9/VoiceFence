"""
Central config. Nothing in here is a secret except RIME_API_KEY, and that
one lives in an environment variable — never hardcoded, never committed.
"""
import os

# --- Rime TTS ---------------------------------------------------------
RIME_API_KEY = os.environ.get("RIME_API_KEY", "")
RIME_TTS_URL = "https://users.rime.ai/v1/rime-tts"
RIME_MODEL_ID = os.environ.get("RIME_MODEL_ID", "mistv2")
RIME_SPEAKER = os.environ.get("RIME_SPEAKER", "astra")
RIME_LANGUAGE = os.environ.get("RIME_LANGUAGE", "eng")

# PCM is headerless 16-bit little-endian audio. That matters here: it's
# what lets us play audio chunk-by-chunk and stop cleanly on the sample,
# instead of waiting on a container format to finish a frame.
RIME_SAMPLING_RATE = int(os.environ.get("RIME_SAMPLING_RATE", "16000"))
RIME_SPEED_ALPHA = float(os.environ.get("RIME_SPEED_ALPHA", "1.0"))

# --- Mock order backend -------------------------------------------------
# This is the "artificial delay" the spec calls out as a first-class,
# easy-to-change setting (not buried in code) — Person D sweeps this.
MOCK_LOOKUP_DELAY_SECONDS = float(os.environ.get("MOCK_LOOKUP_DELAY_SECONDS", "3.0"))
