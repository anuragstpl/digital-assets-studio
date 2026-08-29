"""The edit document - what the editor edits and the renderer renders.

One video track, read left to right, plus free-floating text overlays and audio
beds on top. That shape is deliberate: a sequential track is what ffmpeg can
join losslessly, what a model can reason about without inventing overlaps, and
what makes a ripple edit - trim a clip and everything after it slides - a single
line of arithmetic rather than a constraint solver.

Everything here is plain data. The document round-trips through JSON, so an edit
survives a crash, can be diffed, and can be written by a model as easily as by a
person.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# what a clip is made of
VIDEO, IMAGE, COLOUR = "video", "image", "colour"
KINDS = (VIDEO, IMAGE, COLOUR)

# how a clip enters. A cut is free; the others cost the renderer an xfade pass.
CUT, FADE, DISSOLVE, SLIDE = "cut", "fade", "dissolve", "slide"
TRANSITIONS = (CUT, FADE, DISSOLVE, SLIDE)

# where an overlay sits in the frame
TOP, MIDDLE, LOWER_THIRD, BOTTOM = "top", "middle", "lower_third", "bottom"
POSITIONS = (TOP, MIDDLE, LOWER_THIRD, BOTTOM)

VOICE, MUSIC = "voice", "music"

LANDSCAPE = (1920, 1080)
PORTRAIT = (1080, 1920)

VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
AUDIO_SUFFIXES = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")

MIN_CLIP = 0.2          # shorter than this and a frame barely registers
MIN_SPEED, MAX_SPEED = 0.25, 4.0


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def kind_for(path: str | Path) -> str:
    return IMAGE if Path(path).suffix.lower() in IMAGE_SUFFIXES else VIDEO


@dataclass
class Clip:
    """One piece of picture on the track.

    ``source_in``/``source_out`` are points in the *source* file. A still has no
    source timeline, so its ``source_out`` is simply how long it is held.
    """
    source: str = ""                     # relative to the project, or absolute
    kind: str = VIDEO
    source_in: float = 0.0
    source_out: float = 4.0
    speed: float = 1.0
    volume: float = 1.0                  # the clip's own audio; 0 mutes it
    transition: str = CUT                # how this clip *enters*
    transition_seconds: float = 0.5
    ken_burns: bool = True               # stills only: a slow push
    brightness: float = 0.0              # -1..1
    contrast: float = 1.0                # 0..3
    saturation: float = 1.0              # 0..3
    label: str = ""
    colour: str = "#000000"              # COLOUR clips only
    id: str = field(default_factory=_new_id)

    # --------------------------------------------------------------- shape --
    @property
    def source_length(self) -> float:
        return max(self.source_out - self.source_in, 0.0)

    @property
    def length(self) -> float:
        """How long this clip occupies the timeline, after speed."""
        if self.kind in (IMAGE, COLOUR):
            return max(self.source_length, MIN_CLIP)
        speed = clamp(self.speed or 1.0, MIN_SPEED, MAX_SPEED)
        return max(self.source_length / speed, 0.0)

    @property
    def has_audio(self) -> bool:
        return self.kind == VIDEO and self.volume > 0

    def path(self, base: Path) -> Path:
        p = Path(self.source).expanduser()
        return p if p.is_absolute() else (base / self.source)

    def sanitised(self) -> "Clip":
        """A copy with every value inside the range the renderer can honour."""
        c = Clip(**asdict(self))
        c.kind = c.kind if c.kind in KINDS else VIDEO
        c.transition = c.transition if c.transition in TRANSITIONS else CUT
        c.source_in = max(_f(c.source_in), 0.0)
        c.source_out = max(_f(c.source_out), c.source_in + MIN_CLIP)
        c.speed = clamp(_f(c.speed, 1.0) or 1.0, MIN_SPEED, MAX_SPEED)
        c.volume = clamp(_f(c.volume, 1.0), 0.0, 4.0)
        c.transition_seconds = clamp(_f(c.transition_seconds, 0.5), 0.1, 3.0)
        c.brightness = clamp(_f(c.brightness), -1.0, 1.0)
        c.contrast = clamp(_f(c.contrast, 1.0), 0.0, 3.0)
        c.saturation = clamp(_f(c.saturation, 1.0), 0.0, 3.0)
        return c

    @classmethod
    def from_dict(cls, d: dict) -> "Clip":
        known = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known).sanitised()


@dataclass
class Text:
    """A title you wrote, as opposed to a caption cut from the narration."""
    text: str = ""
    start: float = 0.0
    seconds: float = 3.0
    position: str = LOWER_THIRD
    size: float = 0.048                  # fraction of frame height
    colour: str = "#FFFFFF"
    box: bool = True                     # a dark plate behind the words
    id: str = field(default_factory=_new_id)

    @property
    def end(self) -> float:
        return self.start + self.seconds

    def sanitised(self) -> "Text":
        t = Text(**asdict(self))
        t.text = " ".join((t.text or "").split())
        t.start = max(_f(t.start), 0.0)
        t.seconds = max(_f(t.seconds, 3.0), 0.4)
        t.position = t.position if t.position in POSITIONS else LOWER_THIRD
        t.size = clamp(_f(t.size, 0.048), 0.02, 0.16)
        return t

    @classmethod
    def from_dict(cls, d: dict) -> "Text":
        known = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known).sanitised()


@dataclass
class Audio:
    """A bed underneath the picture: narration, or music.

    Music carries its own gain rather than being mixed flat, because a bed at
    the same level as the narration is the commonest reason a video is
    unwatchable.
    """
    source: str = ""
    role: str = MUSIC
    start: float = 0.0                   # where it lands on the timeline
    source_in: float = 0.0               # where to enter the file
    seconds: float = 0.0                 # 0 means all of it
    gain_db: float = -22.0
    fade_in: float = 0.5
    fade_out: float = 1.5
    loop: bool = True                    # music only: fill the whole edit
    id: str = field(default_factory=_new_id)

    def path(self, base: Path) -> Path:
        p = Path(self.source).expanduser()
        return p if p.is_absolute() else (base / self.source)

    def sanitised(self) -> "Audio":
        a = Audio(**asdict(self))
        a.role = a.role if a.role in (VOICE, MUSIC) else MUSIC
        a.start = max(_f(a.start), 0.0)
        a.source_in = max(_f(a.source_in), 0.0)
        a.seconds = max(_f(a.seconds), 0.0)
        a.gain_db = clamp(_f(a.gain_db, -22.0), -60.0, 12.0)
        a.fade_in = clamp(_f(a.fade_in, 0.5), 0.0, 10.0)
        a.fade_out = clamp(_f(a.fade_out, 1.5), 0.0, 10.0)
        return a

    @classmethod
    def from_dict(cls, d: dict) -> "Audio":
        known = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known).sanitised()


@dataclass
class Timeline:
    name: str = "edit"
    width: int = 1920
    height: int = 1080
    fps: int = 30
    clips: list[Clip] = field(default_factory=list)
    overlays: list[Text] = field(default_factory=list)
    audio: list[Audio] = field(default_factory=list)
    captions: str = ""                   # an .srt burned into the picture
    fade_out_seconds: float = 0.0        # a fade to black at the very end
    notes: str = ""                      # what the AI edit says it did

    # --------------------------------------------------------------- shape --
    @property
    def portrait(self) -> bool:
        return self.height > self.width

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)

    def overlap(self, index: int) -> float:
        """How far clip ``index`` slides back over the one before it.

        A transition eats time from both neighbours, so it can never be longer
        than half of either - otherwise the shorter clip vanishes inside it and
        every later start time is wrong.
        """
        if index <= 0 or index >= len(self.clips):
            return 0.0
        clip = self.clips[index]
        if clip.transition == CUT:
            return 0.0
        before = self.clips[index - 1].length
        return max(min(clip.transition_seconds, before / 2, clip.length / 2), 0.0)

    def starts(self) -> list[float]:
        """Where each clip begins on the timeline."""
        out: list[float] = []
        at = 0.0
        for i, clip in enumerate(self.clips):
            at -= self.overlap(i)
            out.append(round(at, 3))
            at += clip.length
        return out

    @property
    def duration(self) -> float:
        starts = self.starts()
        if not starts:
            return 0.0
        return round(starts[-1] + self.clips[-1].length, 3)

    def at(self, seconds: float) -> Clip | None:
        """Which clip is on screen at a moment - what the preview needs."""
        for start, clip in zip(self.starts(), self.clips):
            if start <= seconds < start + clip.length:
                return clip
        return self.clips[-1] if self.clips else None

    def source_seconds(self, ref: str | int, seconds: float) -> float:
        """A moment on the timeline turned into a moment inside a clip's file."""
        clip = self.clip(ref)
        if clip is None:
            return 0.0
        i = self.clips.index(clip)
        offset = max(seconds - self.starts()[i], 0.0)
        if clip.kind in (IMAGE, COLOUR):
            return 0.0
        return round(clip.source_in + offset * clamp(clip.speed or 1.0, MIN_SPEED, MAX_SPEED), 3)

    # ------------------------------------------------------------ indexing --
    def clip(self, ref: str | int) -> Clip | None:
        """A clip by id, by index, or by an index that arrived as a string.

        Models are cheerfully inconsistent about which they send back, and a
        rejected edit is worse than a lenient lookup."""
        if isinstance(ref, bool):
            return None
        if isinstance(ref, int):
            return self.clips[ref] if 0 <= ref < len(self.clips) else None
        text = str(ref).strip()
        for c in self.clips:
            if c.id == text:
                return c
        if text.lstrip("-").isdigit():
            i = int(text)
            if 0 <= i < len(self.clips):
                return self.clips[i]
        return None

    def index(self, ref: str | int) -> int:
        clip = self.clip(ref)
        return self.clips.index(clip) if clip is not None else -1

    # --------------------------------------------------------------- edits --
    def add(self, clip: Clip, at: int | None = None) -> Clip:
        clip = clip.sanitised()
        if at is None or at >= len(self.clips):
            self.clips.append(clip)
        else:
            self.clips.insert(max(at, 0), clip)
        if self.clips:
            self.clips[0].transition = CUT
        return clip

    def remove(self, ref: str | int) -> bool:
        clip = self.clip(ref)
        if clip is None:
            return False
        self.clips.remove(clip)
        if self.clips:
            self.clips[0].transition = CUT
        return True

    def move(self, ref: str | int, to: int) -> bool:
        i = self.index(ref)
        if i < 0:
            return False
        clip = self.clips.pop(i)
        self.clips.insert(max(0, min(to, len(self.clips))), clip)
        # the first clip can never transition in from something that is not there
        if self.clips:
            self.clips[0].transition = CUT
        return True

    def trim(self, ref: str | int, source_in: float | None = None,
             source_out: float | None = None) -> bool:
        clip = self.clip(ref)
        if clip is None:
            return False
        new_in = max(_f(source_in, clip.source_in), 0.0) if source_in is not None \
            else clip.source_in
        new_out = _f(source_out, clip.source_out) if source_out is not None else clip.source_out
        if new_out - new_in < MIN_CLIP:
            new_out = new_in + MIN_CLIP
        clip.source_in, clip.source_out = round(new_in, 3), round(new_out, 3)
        return True

    def split(self, ref: str | int, at: float) -> Clip | None:
        """Cut one clip in two at a point measured on the timeline.

        The halves keep the source's own timing, which is the whole point: a
        split is not a re-encode, it is two windows onto the same file.
        """
        clip = self.clip(ref)
        if clip is None:
            return None
        i = self.clips.index(clip)
        offset = at - self.starts()[i]
        speed = 1.0 if clip.kind in (IMAGE, COLOUR) else clamp(clip.speed or 1.0,
                                                               MIN_SPEED, MAX_SPEED)
        cut = clip.source_in + offset * speed
        if offset < MIN_CLIP or clip.source_out - cut < MIN_CLIP:
            return None                   # nothing worth having on one side
        tail = Clip(**{**asdict(clip), "id": _new_id()})
        tail.source_in = round(cut, 3)
        tail.transition = CUT             # a split is a cut, never a dissolve
        clip.source_out = round(cut, 3)
        self.clips.insert(i + 1, tail)
        return tail

    def duplicate(self, ref: str | int) -> Clip | None:
        clip = self.clip(ref)
        if clip is None:
            return None
        copy = Clip(**{**asdict(clip), "id": _new_id()})
        self.clips.insert(self.clips.index(clip) + 1, copy)
        return copy

    def set(self, ref: str | int, **props) -> bool:
        clip = self.clip(ref)
        if clip is None:
            return False
        for key, value in props.items():
            if key in Clip.__dataclass_fields__ and key != "id":
                setattr(clip, key, value)
        fixed = clip.sanitised()
        for key in Clip.__dataclass_fields__:
            if key != "id":
                setattr(clip, key, getattr(fixed, key))
        if self.clips:
            self.clips[0].transition = CUT
        return True

    def add_text(self, text: Text) -> Text:
        t = text.sanitised()
        self.overlays.append(t)
        self.overlays.sort(key=lambda o: o.start)
        return t

    def remove_text(self, ref: str | int) -> bool:
        for o in self.overlays:
            if o.id == str(ref):
                self.overlays.remove(o)
                return True
        if isinstance(ref, int) or str(ref).lstrip("-").isdigit():
            i = int(ref)
            if 0 <= i < len(self.overlays):
                del self.overlays[i]
                return True
        return False

    def set_music(self, source: str, gain_db: float = -22.0) -> Audio:
        """One music bed at a time - a second one is a mistake, not a feature."""
        self.audio = [a for a in self.audio if a.role != MUSIC]
        bed = Audio(source=source, role=MUSIC, gain_db=gain_db).sanitised()
        self.audio.append(bed)
        return bed

    @property
    def music(self) -> Audio | None:
        return next((a for a in self.audio if a.role == MUSIC), None)

    # ----------------------------------------------------------- soundness --
    def problems(self, base: Path) -> list[str]:
        """Everything that would make the render fail or come out wrong.

        The editor shows this before you press Render, because an ffmpeg error
        about a missing input is not something anyone should have to decode.
        """
        out: list[str] = []
        if not self.clips:
            out.append("The timeline is empty - add a clip before rendering.")
        for i, clip in enumerate(self.clips, 1):
            if clip.kind != COLOUR and not clip.source:
                out.append(f"Clip {i} has no file.")
            elif clip.kind != COLOUR and not clip.path(base).exists():
                out.append(f"Clip {i}: no file at {clip.source}")
            elif clip.length < MIN_CLIP:
                out.append(f"Clip {i} is shorter than {MIN_CLIP}s and would not be seen.")
        total = self.duration
        for i, o in enumerate(self.overlays, 1):
            if total and o.start >= total:
                out.append(f"Title {i} (“{o.text[:24]}”) starts after the video ends.")
        for a in self.audio:
            if a.source and not a.path(base).exists():
                out.append(f"Audio {a.source} is missing.")
        if self.captions:
            caption_path = Path(self.captions).expanduser()
            if not caption_path.is_absolute():
                caption_path = base / self.captions
            if not caption_path.exists():
                out.append(f"Subtitle file {self.captions} is missing.")
        return out

    def summary(self) -> str:
        mins, secs = divmod(int(self.duration), 60)
        bits = [f"{len(self.clips)} clips", f"{mins}:{secs:02d}"]
        if self.overlays:
            bits.append(f"{len(self.overlays)} titles")
        if self.music:
            bits.append("music")
        if self.captions:
            bits.append("captions")
        bits.append("9:16" if self.portrait else "16:9")
        return " · ".join(bits)

    # --------------------------------------------------------- persistence --
    def to_dict(self) -> dict:
        return {
            "name": self.name, "width": self.width, "height": self.height, "fps": self.fps,
            "captions": self.captions, "fade_out_seconds": self.fade_out_seconds,
            "notes": self.notes,
            "clips": [asdict(c) for c in self.clips],
            "overlays": [asdict(o) for o in self.overlays],
            "audio": [asdict(a) for a in self.audio],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Timeline":
        d = d or {}
        tl = cls(
            name=str(d.get("name", "edit")),
            width=int(_f(d.get("width"), 1920)) or 1920,
            height=int(_f(d.get("height"), 1080)) or 1080,
            fps=int(_f(d.get("fps"), 30)) or 30,
            captions=str(d.get("captions", "") or ""),
            fade_out_seconds=max(_f(d.get("fade_out_seconds"), 0.0), 0.0),
            notes=str(d.get("notes", "") or ""),
        )
        tl.clips = [Clip.from_dict(c) for c in (d.get("clips") or [])]
        tl.overlays = [Text.from_dict(o) for o in (d.get("overlays") or [])]
        tl.audio = [Audio.from_dict(a) for a in (d.get("audio") or [])]
        if tl.clips:
            tl.clips[0].transition = CUT
        return tl

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(path)
        return path


def load(path: Path) -> Timeline:
    path = Path(path)
    if not path.exists():
        return Timeline()
    try:
        return Timeline.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001 - a corrupt edit must not take the app down
        return Timeline()


__all__ = [
    "Audio", "Clip", "Text", "Timeline", "load",
    "CUT", "FADE", "DISSOLVE", "SLIDE", "TRANSITIONS",
    "VIDEO", "IMAGE", "COLOUR", "KINDS", "VOICE", "MUSIC",
    "TOP", "MIDDLE", "LOWER_THIRD", "BOTTOM", "POSITIONS",
    "LANDSCAPE", "PORTRAIT", "MIN_CLIP", "MIN_SPEED", "MAX_SPEED",
    "VIDEO_SUFFIXES", "IMAGE_SUFFIXES", "AUDIO_SUFFIXES", "kind_for", "clamp",
]
