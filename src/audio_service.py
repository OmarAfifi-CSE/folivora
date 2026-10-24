"""
audio_service.py - Bidirectional ElevenLabs Voice Service (Scribe v2 STT + Flash v2.5 TTS)

1. Speech-to-Text (`transcribe_and_forget`): Sends spoken lazy filter instructions to
   ElevenLabs Scribe (`scribe_v1`), returns the transcribed text, and immediately calls
   `DELETE /v1/speech-to-text/transcripts/{id}` so raw voice transcripts never linger on
   third-party servers.
2. Text-to-Speech (`synthesize_speech`): Converts the verified 2-sentence tab digest
   into lifelike spoken MP3 audio using Charlie (`IKne3meq5aSn9XLyUdCD`) and `eleven_flash_v2_5`.
"""

import os
import requests

from telemetry import trace_genai_span

ELEVENLABS_BASE_URL = "https://api.elevenlabs.io/v1"


class AudioService:
    DEFAULT_VOICE_ID = "IKne3meq5aSn9XLyUdCD"
    DEFAULT_VOICE_NAME = "Charlie"

    def __init__(self, api_key: str | None = None, voice_id: str | None = None):
        self.api_key = (api_key if api_key is not None else os.getenv("ELEVENLABS_API_KEY", "")).strip()
        self.voice_id = (voice_id or os.getenv("ELEVENLABS_VOICE_ID") or self.DEFAULT_VOICE_ID).strip()
        self.voice_name = self.DEFAULT_VOICE_NAME if self.voice_id == self.DEFAULT_VOICE_ID else "Custom"

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def transcribe_and_forget(
        self,
        audio_bytes: bytes,
        filename: str = "voice_note.webm",
        content_type: str = "audio/webm",
        trace_collector: list | None = None,
    ) -> dict:
        """
        Transcribes a spoken voice note via ElevenLabs Scribe (`scribe_v1`) and immediately
        deletes the remote transcript from ElevenLabs for user privacy.
        """
        if not audio_bytes:
            raise ValueError("Audio payload cannot be empty.")
        if not self.api_key:
            raise RuntimeError("Missing ELEVENLABS_API_KEY environment variable.")

        with trace_genai_span(
            name="elevenlabs.scribe_stt",
            model_name="elevenlabs/scribe_v1",
            op="gen_ai.request",
            trace_collector=trace_collector,
        ) as span_meta:
            try:
                resp = requests.post(
                    f"{ELEVENLABS_BASE_URL}/speech-to-text",
                    headers={"xi-api-key": self.api_key},
                    files={"file": (filename, audio_bytes, content_type)},
                    data={"model_id": "scribe_v1"},
                    timeout=15.0,
                )
            except requests.RequestException as exc:
                raise RuntimeError(f"ElevenLabs Scribe request failed: {exc}")

            if resp.status_code != 200:
                raise RuntimeError(f"ElevenLabs Scribe error ({resp.status_code}): {resp.text}")

            payload = resp.json()
            text = (payload.get("text") or "").strip()
            transcription_id = payload.get("transcription_id")

            # Immediate privacy deletion (MicroAdventures pattern)
            remote_deleted = False
            if transcription_id:
                try:
                    del_resp = requests.delete(
                        f"{ELEVENLABS_BASE_URL}/speech-to-text/transcripts/{transcription_id}",
                        headers={"xi-api-key": self.api_key},
                        timeout=6.0,
                    )
                    remote_deleted = del_resp.status_code in (200, 204)
                except Exception:
                    remote_deleted = False

            words = len(text.split())
            span_meta["gen_ai.usage.input_tokens"] = max(1, int(len(audio_bytes) / 1200))
            span_meta["gen_ai.usage.output_tokens"] = max(1, words)
            span_meta["elevenlabs.remote_transcript_deleted"] = remote_deleted

            return {
                "text": text,
                "remote_deleted": remote_deleted,
                "transcription_id_redacted": bool(transcription_id),
            }

    def synthesize_speech(
        self,
        text: str,
        trace_collector: list | None = None,
    ) -> bytes:
        """
        Synthesizes spoken audio via ElevenLabs `eleven_flash_v2_5` (Charlie voice).
        """
        cleaned = (text or "").strip()
        if not cleaned:
            raise RuntimeError("Cannot synthesize empty text.")
        if not self.api_key:
            raise RuntimeError("Missing ELEVENLABS_API_KEY environment variable.")

        with trace_genai_span(
            name="elevenlabs.flash_tts",
            model_name="elevenlabs/eleven_flash_v2_5",
            op="gen_ai.request",
            trace_collector=trace_collector,
        ) as span_meta:
            words = len(cleaned.split())
            span_meta["gen_ai.usage.input_tokens"] = words
            span_meta["gen_ai.usage.output_tokens"] = words * 2
            span_meta["elevenlabs.voice_name"] = self.voice_name

            try:
                resp = requests.post(
                    f"{ELEVENLABS_BASE_URL}/text-to-speech/{self.voice_id}",
                    headers={
                        "Accept": "audio/mpeg",
                        "Content-Type": "application/json",
                        "xi-api-key": self.api_key,
                    },
                    json={
                        "text": cleaned,
                        "model_id": "eleven_flash_v2_5",
                        "voice_settings": {
                            "stability": 0.72,
                            "similarity_boost": 0.84,
                        },
                    },
                    timeout=15.0,
                )
            except requests.RequestException as exc:
                raise RuntimeError(f"ElevenLabs TTS request failed: {exc}")

            if resp.status_code == 200 and resp.content:
                return resp.content
            raise RuntimeError(f"ElevenLabs TTS API error ({resp.status_code}): {resp.text}")
