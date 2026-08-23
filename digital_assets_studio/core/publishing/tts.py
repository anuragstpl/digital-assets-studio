"""Voiceover, generated inside the app.

Three routes, tried in the order you configure:
  edge-tts      free, no key, very good quality, needs the package installed
  OpenAI TTS    paid, one API call, uses the key you already saved
  Google TTS    paid, uses the Google API key you already saved

Each scene is synthesised separately, which gives an exact duration per scene -
that is what lets the renderer cut the visuals to the audio instead of guessing.
"""
from __future__ import annotations

import base64
import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from .. import keyvault
from ..settings import load as load_settings

log = logging.getLogger(__name__)

EDGE_VOICES = {
    "English (US, male)": "en-US-AndrewNeural",
    "English (US, female)": "en-US-AvaNeural",
    "English (UK, male)": "en-GB-RyanNeural",
    "English (India, male)": "en-IN-PrabhatNeural",
    "English (India, female)": "en-IN-NeerjaNeural",
    "Hindi (male)": "hi-IN-MadhurNeural",
    "Hindi (female)": "hi-IN-SwaraNeural",
    "Spanish (female)": "es-ES-ElviraNeural",
    "Indonesian (female)": "id-ID-GadisNeural",
}

OPENAI_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]


class TTSError(RuntimeError):
    pass


@dataclass
class Clip:
    path: Path
    seconds: float
    text: str


def edge_available() -> bool:
    if shutil.which("edge-tts"):
        return True
    try:
        import edge_tts  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def ffprobe_duration(path: Path) -> float:
    exe = shutil.which("ffprobe")
    if not exe:
        return 0.0
    try:
        out = subprocess.run(
            [exe, "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
            capture_output=True, text=True, timeout=60, check=True)
        return float(json.loads(out.stdout)["format"]["duration"])
    except Exception:  # noqa: BLE001
        return 0.0


# ------------------------------------------------------------------- engines --

def _edge(text: str, out: Path, voice: str, rate: str = "-3%") -> None:
    exe = shutil.which("edge-tts")
    vtt = out.with_suffix(".vtt")
    if exe:
        cmd = [exe, "--text", text, "--write-media", str(out),
               "--write-subtitles", str(vtt), "--voice", voice, f"--rate={rate}"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if res.returncode != 0:
            raise TTSError(f"edge-tts failed: {res.stderr[:300]}")
        return
    import asyncio

    import edge_tts

    async def go() -> None:
        comm = edge_tts.Communicate(text, voice, rate=rate)
        await comm.save(str(out))

    asyncio.run(go())


def _openai(text: str, out: Path, voice: str, model: str = "gpt-4o-mini-tts") -> None:
    s = load_settings()
    prov = s.provider("openai")
    key = keyvault.get_secret(prov.secret_name) if prov else ""
    if not key:
        raise TTSError("No OpenAI key saved - add one in Settings, or install edge-tts for the free route.")
    r = httpx.post("https://api.openai.com/v1/audio/speech",
                   headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                   json={"model": model, "voice": voice, "input": text, "response_format": "mp3"},
                   timeout=300)
    if r.status_code >= 400:
        raise TTSError(f"OpenAI TTS {r.status_code}: {r.text[:300]}")
    out.write_bytes(r.content)


def _google(text: str, out: Path, voice: str, language: str = "en-US") -> None:
    s = load_settings()
    prov = s.provider("google")
    key = keyvault.get_secret(prov.secret_name) if prov else ""
    if not key:
        raise TTSError("No Google key saved.")
    r = httpx.post("https://texttospeech.googleapis.com/v1/text:synthesize",
                   params={"key": key},
                   json={"input": {"text": text},
                         "voice": {"languageCode": language, "name": voice},
                         "audioConfig": {"audioEncoding": "MP3", "speakingRate": 0.97}},
                   timeout=300)
    if r.status_code >= 400:
        raise TTSError(f"Google TTS {r.status_code}: {r.text[:300]}")
    out.write_bytes(base64.b64decode(r.json()["audioContent"]))


ENGINES = {"edge-tts (free)": "edge", "OpenAI": "openai", "Google": "google"}


def synthesize(text: str, out: Path, engine: str = "edge", voice: str = "en-US-AndrewNeural",
               rate: str = "-3%") -> Clip:
    out.parent.mkdir(parents=True, exist_ok=True)
    text = text.strip()
    if not text:
        raise TTSError("Nothing to say - the narration is empty.")
    if engine == "edge":
        if not edge_available():
            raise TTSError("edge-tts is not installed. Run `pip install edge-tts`, "
                           "or switch the voiceover engine to OpenAI or Google in the step options.")
        _edge(text, out, voice, rate)
    elif engine == "openai":
        _openai(text, out, voice if voice in OPENAI_VOICES else "onyx")
    elif engine == "google":
        _google(text, out, voice)
    else:
        raise TTSError(f"Unknown voiceover engine: {engine}")
    return Clip(out, ffprobe_duration(out), text)


def synthesize_scenes(scenes: list[str], out_dir: Path, engine: str = "edge",
                      voice: str = "en-US-AndrewNeural", rate: str = "-3%",
                      progress=None) -> list[Clip]:
    out_dir.mkdir(parents=True, exist_ok=True)
    clips: list[Clip] = []
    for i, text in enumerate(scenes, start=1):
        if progress:
            progress(i / max(len(scenes), 1), f"Voicing scene {i} of {len(scenes)}")
        clips.append(synthesize(text, out_dir / f"scene_{i:03d}.mp3", engine, voice, rate))
    return clips


def write_srt(clips: list[Clip], out: Path) -> Path:
    def stamp(t: float) -> str:
        h, rem = divmod(t, 3600)
        m, s = divmod(rem, 60)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}"

    lines, t = [], 0.0
    for i, c in enumerate(clips, start=1):
        lines += [str(i), f"{stamp(t)} --> {stamp(t + c.seconds)}", c.text.strip(), ""]
        t += c.seconds
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
