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

# Rime returns headerless 16-bit little-endian PCM.
RIME_SAMPLING_RATE = int(os.environ.get("RIME_SAMPLING_RATE", "16000"))
RIME_SPEED_ALPHA = float(os.environ.get("RIME_SPEED_ALPHA", "1.0"))

# --- Mock order backend -------------------------------------------------
# Artificial delay used by the mock backend and test sweeps.
MOCK_LOOKUP_DELAY_SECONDS = float(os.environ.get("MOCK_LOOKUP_DELAY_SECONDS", "3.0"))
