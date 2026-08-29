"""Deep verification of the video editor: does the file that comes out match the
edit that went in?

The other suites prove the editor runs. They do not prove it is *right* - a
render can finish, be the correct length and the correct size, and still show
the wrong clip, start two seconds late, play the narration under the wrong
scene, or drop a title. Those are the failures that matter and none of them are
visible in an exit code.

So this suite looks at the output. Every source is a flat colour with a known
tone, which makes the finished file checkable: the average colour of a frame
says which clip is on screen at that moment, and the mean volume of a window
says what is audible in it. If a cut is late, a clip is out of order, a
crossfade does not blend, a title is not drawn, the music is not ducked or the
narration lands in the wrong place, an assertion here fails.

    python tests/editor.py

Needs ffmpeg and ffprobe. Nothing else - no keys, no network, no model.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

WORK = tempfile.mkdtemp(prefix="das-editor-")
os.environ["DAS_HOME"] = WORK
os.environ["DAS_KEYVAULT"] = "memory"
os.environ["DAS_TELEMETRY"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_assets_studio.config import ASSETS_DIR  # noqa: E402
from digital_assets_studio.core.editor import ai as eai  # noqa: E402
from digital_assets_studio.core.editor import analyze  # noqa: E402
from digital_assets_studio.core.editor import render as erender  # noqa: E402
from digital_assets_studio.core.editor import timeline as tl  # noqa: E402

FAILURES: list[str] = []
PASSED = 0
MEDIA = Path(WORK) / "media"
FONT = ASSETS_DIR / "fonts" / "Poppins-Medium.ttf"


def check(name: str, fn) -> None:
    global PASSED
    try:
        fn()
        PASSED += 1
        print(f"  PASS  {name}")
    except Exception as exc:  # noqa: BLE001
        FAILURES.append(f"{name}: {exc}")
        print(f"  FAIL  {name}: {exc}")
        traceback.print_exc(limit=4)


# ------------------------------------------------------------------- media --

def ffmpeg(*args: str) -> None:
    res = subprocess.run(["ffmpeg", "-y", "-hide_banner", *args],
                         capture_output=True, encoding="utf-8", errors="replace",
                         timeout=300)
    if res.returncode != 0:
        raise RuntimeError(f"fixture build failed: {res.stderr[-400:]}")


def build_media() -> None:
    """Sources whose content can be read back out of a render.

    Flat colours and pure tones are not a shortcut: they are what makes an
    assertion about the finished file possible at all.
    """
    MEDIA.mkdir(parents=True, exist_ok=True)

    # six seconds that change colour on a known beat: red, green, blue
    for name, colour in (("c1", "red"), ("c2", "green"), ("c3", "blue")):
        ffmpeg("-f", "lavfi", "-i", f"color={colour}:size=320x240:rate=25:duration=2",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", str(MEDIA / f"{name}.mp4"))
    listing = MEDIA / "list.txt"
    listing.write_text("\n".join(f"file '{(MEDIA / f'c{i}.mp4').as_posix()}'"
                                 for i in (1, 2, 3)), encoding="utf-8")
    ffmpeg("-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy",
           str(MEDIA / "silent_chapters.mp4"))
    ffmpeg("-i", str(MEDIA / "silent_chapters.mp4"), "-f", "lavfi",
           "-i", "sine=frequency=440:duration=6", "-c:v", "copy", "-c:a", "aac",
           "-shortest", str(MEDIA / "chapters.mp4"))

    # a source with no audio stream at all
    ffmpeg("-f", "lavfi", "-i", "color=green:size=320x240:rate=25:duration=3",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", str(MEDIA / "no_audio.mp4"))
    # a portrait source, to be fitted into a landscape canvas
    ffmpeg("-f", "lavfi", "-i", "color=orange:size=240x426:rate=25:duration=2",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", str(MEDIA / "portrait.mp4"))
    # a take with four seconds of dead air in the middle of it
    ffmpeg("-f", "lavfi", "-i", "color=red:size=320x240:rate=25:duration=9",
           "-f", "lavfi", "-i",
           "aevalsrc='if(lt(t,2)+gt(t,6),0.5*sin(440*2*PI*t),0)':d=9",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
           str(MEDIA / "take.mp4"))
    # a detailed still, so a Ken Burns move is visible in the pixels
    ffmpeg("-f", "lavfi", "-i", "testsrc=size=800x600:duration=1", "-frames:v", "1",
           str(MEDIA / "pattern.png"))
    ffmpeg("-f", "lavfi", "-i", "color=purple:size=800x600:duration=1", "-frames:v", "1",
           str(MEDIA / "flat.png"))
    # sound: a bed, and one loud block of narration
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:duration=30", "-c:a", "libmp3lame",
           str(MEDIA / "music.mp3"))
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=660:duration=2", "-c:a", "libmp3lame",
           str(MEDIA / "voice.mp3"))
    (MEDIA / "subs.srt").write_text(
        "1\n00:00:00,200 --> 00:00:02,000\nA burned in caption\n\n", encoding="utf-8")
    # the same clip behind a path with a space in it
    spaced = MEDIA / "a folder with spaces"
    spaced.mkdir(exist_ok=True)
    shutil.copy(MEDIA / "chapters.mp4", spaced / "my clip.mp4")


# ------------------------------------------------------------------ probing --

def rgb(video: Path, at: float) -> tuple[int, int, int]:
    """The average colour of one frame - which clip is on screen at that moment."""
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video), "-ss", f"{at:.3f}", "-frames:v", "1",
         "-vf", "scale=1:1:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=120)
    data = res.stdout[:3]
    if len(data) < 3:
        raise AssertionError(f"no frame at {at}s in {video.name}")
    return tuple(data)  # type: ignore[return-value]


def band(video: Path, at: float, top: float, bottom: float) -> tuple[int, int, int]:
    """The average colour of a horizontal strip - where a title or caption lands."""
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video), "-ss", f"{at:.3f}", "-frames:v", "1",
         "-vf", f"crop=iw:ih*{bottom - top:.3f}:0:ih*{top:.3f},scale=1:1:flags=area",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=120)
    data = res.stdout[:3]
    if len(data) < 3:
        raise AssertionError(f"no frame at {at}s in {video.name}")
    return tuple(data)  # type: ignore[return-value]


def sample(video: Path, at: float) -> bytes:
    """An 8x8 thumbnail of a frame, for asking whether the picture moved."""
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video), "-ss", f"{at:.3f}", "-frames:v", "1",
         "-vf", "scale=8:8:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=120)
    return res.stdout[:192]


def dominant(colour: tuple[int, int, int]) -> str:
    r, g, b = colour
    if r + g + b < 60:
        return "black"
    top = max(r, g, b)
    others = sorted((r, g, b))[:2]
    if top - others[1] < 45:
        return "mixed"
    return {r: "red", g: "green", b: "blue"}[top]


def level(video: Path, start: float, seconds: float) -> float:
    """Mean volume of one window, in dB. Silence comes back very negative.

    The seek has to be on the input. volumedetect reports one figure for
    everything the filter was fed, and an output-side -ss still decodes and
    still counts the frames before the window - which quietly turns every
    reading into the mean of the whole file.
    """
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}",
         "-i", str(video), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, encoding="utf-8", errors="replace", timeout=180)
    found = re.search(r"mean_volume:\s*(-?[\d.]+) dB", res.stderr or "")
    return float(found.group(1)) if found else -99.0


def has_audio(video: Path) -> bool:
    return analyze.probe(video).has_audio


def render(doc: tl.Timeline, name: str, font: Path | None = None) -> Path:
    out = Path(WORK) / f"{name}.mp4"
    return erender.render(doc, out, base=MEDIA, font=font)


def clip(source: str, start: float, end: float, **kw) -> tl.Clip:
    return tl.Clip(source=source, source_in=start, source_out=end, **kw)


# -------------------------------------------------------------------- tests --

def test_clips_play_in_the_order_they_are_in():
    """Three windows onto one file, in an order the file does not have.

    If the join is wrong, or a clip is dropped, or the order is taken from the
    source rather than the timeline, the colours come out wrong."""
    doc = tl.Timeline(width=320, height=240, fps=25)
    red = doc.add(clip("chapters.mp4", 0.2, 1.7, label="red"))
    doc.add(clip("chapters.mp4", 2.2, 3.7, label="green"))
    blue = doc.add(clip("chapters.mp4", 4.2, 5.7, label="blue"))
    out = render(doc, "order")
    seen = [dominant(rgb(out, t)) for t in (0.7, 2.2, 3.7)]
    assert seen == ["red", "green", "blue"], f"clips came out as {seen}"

    doc.move(blue.id, 0)
    doc.move(red.id, 2)
    out = render(doc, "order2")
    seen = [dominant(rgb(out, t)) for t in (0.7, 2.2, 3.7)]
    assert seen == ["blue", "green", "red"], f"reordering did not take: {seen}"
    assert dominant(rgb(out, 4.4)) in ("red", "black"), "the last clip ended early"
    print(f"        red/green/blue, then reordered to blue/green/red, "
          f"{doc.duration:.1f}s each")


def test_trims_and_speed_are_accurate():
    """An in-point is a promise about the first frame, and speed is a promise
    about when the next thing happens."""
    doc = tl.Timeline(width=320, height=240, fps=25)
    doc.add(clip("chapters.mp4", 2.1, 3.9, label="green only"))
    out = render(doc, "trim")
    assert dominant(rgb(out, 0.2)) == "green", "the in-point was not honoured"
    assert dominant(rgb(out, 1.5)) == "green", "the out-point ran past the chapter"
    assert abs(analyze.probe(out).seconds - 1.8) < 0.25, analyze.probe(out).seconds

    # four seconds of source at 2x: the red/green boundary must arrive at 1s
    doc = tl.Timeline(width=320, height=240, fps=25)
    doc.add(clip("chapters.mp4", 0.0, 4.0, speed=2.0, label="fast"))
    out = render(doc, "speed")
    assert abs(analyze.probe(out).seconds - 2.0) < 0.25, analyze.probe(out).seconds
    assert dominant(rgb(out, 0.5)) == "red", "the fast clip did not start on red"
    assert dominant(rgb(out, 1.5)) == "green", \
        "at 2x the second chapter should arrive at 1s, not 2s"
    assert has_audio(out) and level(out, 0.2, 1.5) > -40, \
        "atempo dropped the audio on a sped-up clip"
    print("        in-point exact, and 2x moves the chapter boundary to 1.0s")


def test_a_transition_actually_blends():
    """A dissolve has to be a blend of both clips, and it has to cost the
    timeline the time it borrows."""
    doc = tl.Timeline(width=320, height=240, fps=25)
    doc.add(clip("chapters.mp4", 0.2, 1.8, label="red"))
    doc.add(clip("chapters.mp4", 2.2, 3.8, transition=tl.DISSOLVE,
                 transition_seconds=1.0, label="green"))
    # 1.0s was asked for, but a transition may not eat more than half of either
    # neighbour, so two 1.6s clips overlap by 0.8s: 3.2s of clips, a 2.4s edit
    assert abs(doc.overlap(1) - 0.8) < 1e-6, doc.overlap(1)
    assert doc.duration == 2.4, doc.duration
    out = render(doc, "dissolve")
    assert abs(analyze.probe(out).seconds - 2.4) < 0.3, analyze.probe(out).seconds
    assert dominant(rgb(out, 0.3)) == "red"
    assert dominant(rgb(out, 2.1)) == "green"
    before, after = rgb(out, 0.3), rgb(out, 2.1)
    middle = rgb(out, 1.2)
    blend = tuple((a + b) // 2 for a, b in zip(before, after))
    assert max(abs(m - x) for m, x in zip(middle, blend)) < 30, \
        f"the midpoint {middle} is not a blend of {before} and {after} (expected {blend})"
    for channel in range(3):
        lo, hi = sorted((before[channel], after[channel]))
        assert lo - 12 <= middle[channel] <= hi + 12, \
            f"channel {channel} of the crossfade is outside both clips: {middle}"
    assert middle != before and middle != after, "the crossfade showed only one clip"
    print(f"        {before} and {after} blend to {middle} at the midpoint, and "
          f"3.2s of clips became a {doc.duration}s edit")


def test_sound_is_where_it_should_be():
    """Mute means silent, music is ducked, and narration lands on its own scene."""
    loud = tl.Timeline(width=320, height=240, fps=25)
    loud.add(clip("chapters.mp4", 0.5, 2.5, volume=1.0))
    out = render(loud, "loud")
    speaking = level(out, 0.3, 1.4)
    assert speaking > -35, f"the clip audio never made it: {speaking} dB"

    muted = tl.Timeline(width=320, height=240, fps=25)
    muted.add(clip("chapters.mp4", 0.5, 2.5, volume=0.0))
    out = render(muted, "muted")
    quiet = level(out, 0.3, 1.4)
    assert has_audio(out), "a muted clip still needs an audio stream to join on"
    assert quiet < speaking - 30, f"muting did nothing: {quiet} dB vs {speaking} dB"

    # the same edit with music under it, at two different levels
    beds = {}
    for gain, name in ((-6.0, "music_loud"), (-30.0, "music_quiet")):
        doc = tl.Timeline(width=320, height=240, fps=25)
        doc.add(clip("chapters.mp4", 0.5, 2.5, volume=0.0))
        doc.set_music("music.mp3", gain_db=gain)
        doc.music.fade_in = doc.music.fade_out = 0.0
        beds[name] = level(render(doc, name), 0.3, 1.4)
    music_loud, music_quiet = beds["music_loud"], beds["music_quiet"]
    assert music_loud > music_quiet + 15, \
        f"the music gain did nothing: {music_loud} vs {music_quiet} dB"

    # narration dropped at 2s must be silent before it and audible after
    doc = tl.Timeline(width=320, height=240, fps=25)
    doc.add(tl.Clip(source="flat.png", kind=tl.IMAGE, source_out=4.0, ken_burns=False))
    doc.audio.append(tl.Audio(source="voice.mp3", role=tl.VOICE, start=2.0, gain_db=0.0,
                              fade_in=0.0, fade_out=0.0, loop=False).sanitised())
    out = render(doc, "narration")
    before, after = level(out, 0.2, 1.5), level(out, 2.2, 1.5)
    assert after > before + 30, \
        f"narration is not sitting at 2s: {before} dB before, {after} dB after"
    print(f"        clip {speaking:.0f} dB, muted {quiet:.0f} dB, music "
          f"{music_loud:.0f}/{music_quiet:.0f} dB, narration {before:.0f} -> "
          f"{after:.0f} dB")


def test_titles_and_captions_are_drawn():
    """Text that a filter string would choke on, drawn where it was asked for.

    Apostrophes, colons, commas and non-ASCII in one title is not a contrived
    case - it is a normal sentence, and it is exactly what breaks a drawtext
    filter that builds its text inline."""
    # one colour for the whole clip, so any change in the frame is the text
    base = tl.Timeline(width=640, height=360, fps=25)
    base.add(clip("chapters.mp4", 0.2, 1.9, volume=0.0))
    plain = render(base, "plain", font=FONT)

    titled = tl.Timeline(width=640, height=360, fps=25)
    titled.add(clip("chapters.mp4", 0.2, 1.9, volume=0.0))
    titled.add_text(tl.Text(
        text="Here's the thing, at 3:15 - it costs \u00a35 (50% off) - \u201creally\u201d",
        start=0.1, seconds=0.9, position=tl.LOWER_THIRD))
    with_title = render(titled, "titled", font=FONT)

    top_before, top_after = band(plain, 0.5, 0.0, 0.3), band(with_title, 0.5, 0.0, 0.3)
    low_before, low_after = band(plain, 0.5, 0.62, 0.95), band(with_title, 0.5, 0.62, 0.95)
    assert max(abs(a - b) for a, b in zip(top_before, top_after)) < 10, \
        f"the title bled into the top of the frame: {top_before} vs {top_after}"
    assert max(abs(a - b) for a, b in zip(low_before, low_after)) > 12, \
        f"no title was drawn in the lower third: {low_before} vs {low_after}"

    # and once its span is over, the frame goes back to the picture
    after = band(with_title, 1.4, 0.62, 0.95)
    same_moment = band(plain, 1.4, 0.62, 0.95)
    assert max(abs(a - b) for a, b in zip(after, same_moment)) < 10, \
        f"the title outstayed its span: {after} vs {same_moment}"

    # the same title without the per-cent sign must draw about as much ink: a
    # single "%" used to make drawtext render the whole line as nothing, with no
    # error and no warning anywhere
    clean = tl.Timeline(width=640, height=360, fps=25)
    clean.add(clip("chapters.mp4", 0.2, 1.9, volume=0.0))
    clean.add_text(tl.Text(text="Here's the thing, at 3:15 - it costs 5 (50 off)",
                           start=0.1, seconds=0.9, position=tl.LOWER_THIRD))
    low_clean = band(render(clean, "clean_title", font=FONT), 0.5, 0.62, 0.95)
    inked = [max(abs(a - b) for a, b in zip(low_before, x))
             for x in (low_after, low_clean)]
    assert min(inked) > 12 and abs(inked[0] - inked[1]) < 12, \
        f"a per-cent sign changed how much of the title was drawn: {inked}"

    capped = tl.Timeline(width=640, height=360, fps=25)
    capped.add(clip("chapters.mp4", 0.2, 1.9, volume=0.0))
    capped.captions = "subs.srt"
    with_caps = render(capped, "capped", font=FONT)
    lower_half = (band(plain, 0.5, 0.5, 1.0), band(with_caps, 0.5, 0.5, 1.0))
    burned = max(abs(a - b) for a, b in zip(*lower_half))
    noise = max(abs(a - b) for a, b in
                zip(band(plain, 0.5, 0.5, 1.0), band(plain, 0.6, 0.5, 1.0)))
    assert burned > max(noise + 2, 3), \
        f"the subtitle file was not burned in: {lower_half} (noise floor {noise})"
    print(f"        a nasty title drew in the lower third {low_before} -> "
          f"{low_after} and left the top alone")


def test_it_fades_to_black():
    doc = tl.Timeline(width=320, height=240, fps=25)
    doc.add(clip("chapters.mp4", 0.2, 3.2, volume=1.0))
    doc.fade_out_seconds = 1.0
    out = render(doc, "fade", font=FONT)
    assert dominant(rgb(out, 0.5)) == "red", "the fade started too early"
    last = rgb(out, doc.duration - 0.08)
    assert dominant(last) == "black", f"the last frame is {last}, not black"
    tail = level(out, doc.duration - 0.3, 0.25)
    body = level(out, 0.3, 1.0)
    assert tail < body - 8, f"the audio did not fade with the picture: {body} to {tail} dB"
    print(f"        last frame {last}, audio {body:.0f} -> {tail:.0f} dB")


def test_ken_burns_moves_and_can_be_turned_off():
    moving = tl.Timeline(width=320, height=240, fps=25)
    moving.add(tl.Clip(source="pattern.png", kind=tl.IMAGE, source_out=3.0,
                       ken_burns=True))
    out = render(moving, "kenburns")
    first, last = sample(out, 0.2), sample(out, 2.6)
    drift = sum(abs(a - b) for a, b in zip(first, last)) / max(len(first), 1)
    assert drift > 2.0, f"the still never moved (mean pixel drift {drift:.2f})"

    still = tl.Timeline(width=320, height=240, fps=25)
    still.add(tl.Clip(source="pattern.png", kind=tl.IMAGE, source_out=3.0,
                      ken_burns=False))
    out = render(still, "static")
    held = sum(abs(a - b) for a, b in zip(sample(out, 0.2), sample(out, 2.6))) / 192
    assert held < 1.0, f"a still with the push off still moved ({held:.2f})"
    print(f"        push on: {drift:.1f} mean drift, push off: {held:.1f}")


def test_dead_air_is_gone_from_the_file():
    """Not just that the timeline got shorter - that the rendered file no longer
    has the gap in it."""
    doc = tl.Timeline(width=320, height=240, fps=25)
    doc.add(clip("take.mp4", 0.0, 9.0, label="take"))
    before = render(doc, "with_silence")
    gaps = [g for g in analyze.silences(before) if g[1] - g[0] > 1.0]
    assert gaps, "the fixture should have a gap in it to begin with"

    removed = eai.cut_dead_air(doc, MEDIA)
    after = render(doc, "no_silence")
    left = [g for g in analyze.silences(after) if g[1] - g[0] > 1.0]
    assert removed > 3.0, f"only {removed}s of dead air was found"
    assert not left, f"the render still has dead air in it: {left}"
    assert analyze.probe(after).seconds < analyze.probe(before).seconds - 3.0
    print(f"        {removed:.1f}s cut, {analyze.probe(before).seconds:.1f}s -> "
          f"{analyze.probe(after).seconds:.1f}s, no gap over a second left")


def test_a_short_holds_the_window_it_was_cut_from():
    doc = tl.Timeline(width=320, height=240, fps=25)
    doc.add(clip("chapters.mp4", 0.2, 1.8, label="red"))
    doc.add(clip("chapters.mp4", 2.2, 3.8, label="green"))
    doc.add(clip("chapters.mp4", 4.2, 5.8, label="blue"))

    # 2.8s in is the last 0.4s of the green clip; the window runs on into blue
    short = eai.crop(doc, 2.8, 1.4, portrait=True)
    assert short.size == (1080, 1920), short.size
    out = render(short, "short")
    info = analyze.probe(out)
    assert (info.width, info.height) == (1080, 1920), (info.width, info.height)
    assert abs(info.seconds - 1.4) < 0.3, info.seconds
    assert dominant(rgb(out, 0.2)) == "green", "the Short took the wrong window"
    assert dominant(rgb(out, 1.0)) == "blue", "the Short did not run into the next clip"
    print(f"        {info.width}x{info.height}, {info.seconds:.1f}s, green then blue")


def test_awkward_edits_still_render():
    """The shapes that break a renderer: a lot of clips, a clip barely longer
    than a frame, a transition longer than what it is dissolving, a source with
    no audio at all, a portrait source in a landscape frame, and a path with a
    space in it."""
    many = tl.Timeline(width=320, height=240, fps=25)
    for i in range(12):
        many.add(clip("chapters.mp4", (i % 3) * 2 + 0.2, (i % 3) * 2 + 0.7,
                      label=f"clip {i}"))
    out = render(many, "many")
    assert abs(analyze.probe(out).seconds - many.duration) < 0.4, \
        f"{analyze.probe(out).seconds} vs {many.duration}"
    assert dominant(rgb(out, 0.2)) == "red" and dominant(rgb(out, 0.8)) == "green"

    tight = tl.Timeline(width=320, height=240, fps=25)
    tight.add(clip("chapters.mp4", 0.2, 1.2))
    tight.add(clip("chapters.mp4", 2.2, 2.2 + tl.MIN_CLIP, transition=tl.DISSOLVE,
                   transition_seconds=3.0))
    assert abs(tight.overlap(1) - tl.MIN_CLIP / 2) < 1e-6, tight.overlap(1)
    out = render(tight, "tight")
    assert analyze.probe(out).seconds > 0.9, analyze.probe(out).seconds

    mixed = tl.Timeline(width=640, height=360, fps=25)
    mixed.add(clip("no_audio.mp4", 0.0, 1.5, label="no audio"))
    mixed.add(clip("portrait.mp4", 0.0, 1.5, label="portrait source"))
    mixed.add(clip("a folder with spaces/my clip.mp4", 0.2, 1.5, label="spaced path"))
    assert mixed.problems(MEDIA) == [], mixed.problems(MEDIA)
    out = render(mixed, "mixed")
    info = analyze.probe(out)
    assert (info.width, info.height) == (640, 360), (info.width, info.height)
    assert info.has_audio, "a silent source must not cost the edit its audio track"
    assert dominant(rgb(out, 0.5)) == "green", "the silent clip did not play"
    assert abs(info.seconds - mixed.duration) < 0.4, f"{info.seconds} vs {mixed.duration}"
    print(f"        12 clips, a {tl.MIN_CLIP}s clip, a silent source, a portrait "
          f"source and a path with spaces")


def test_junk_from_a_model_cannot_corrupt_an_edit():
    """Whatever comes back from the model, the timeline that survives it has to
    still be renderable."""
    doc = tl.Timeline(width=320, height=240, fps=25)
    doc.add(clip("chapters.mp4", 0.2, 1.8, label="A"))
    doc.add(clip("chapters.mp4", 2.2, 3.8, label="B"))
    good = doc.to_dict()
    applied, rejected = eai.apply_ops(doc, [
        None, 42, [], "", {"clip": 0}, {"op": None},
        {"op": "trim", "clip": 0, "start": "abc", "end": None},
        {"op": "speed", "clip": 0, "factor": -5},
        {"op": "speed", "clip": 0, "factor": 1e9},
        {"op": "volume", "clip": 1, "level": -3},
        {"op": "hold", "clip": 0, "seconds": -1},
        {"op": "split", "clip": 0, "at": 99},
        {"op": "reorder", "clip": 1, "to": 500},
        {"op": "title", "text": "x" * 4000, "start": -10, "seconds": 0},
        {"op": "remove_title", "index": 99},
        {"op": "fade_out", "seconds": "soon"},
        {"op": "captions", "source": "../../etc/passwd"},
    ], base=MEDIA)
    assert isinstance(applied, list) and isinstance(rejected, list)
    assert rejected, "none of that should have gone through unquestioned"

    for c in doc.clips:
        assert tl.MIN_SPEED <= c.speed <= tl.MAX_SPEED, c.speed
        assert c.source_out - c.source_in >= tl.MIN_CLIP, (c.source_in, c.source_out)
        assert c.volume >= 0, c.volume
    for o in doc.overlays:
        assert o.start >= 0 and o.seconds > 0, o
    assert doc.clips, "the timeline was emptied by junk"
    assert doc.captions == "" or (MEDIA / doc.captions).exists(), doc.captions
    assert [c["source"] for c in doc.to_dict()["clips"]] == \
        [c["source"] for c in good["clips"]], "junk repointed a clip at another file"

    out = render(doc, "after_junk")
    assert analyze.probe(out).seconds > 0.5, "the edit no longer renders"
    print(f"        {len(rejected)} refusals, every value still in range, "
          f"and it renders in {analyze.probe(out).seconds:.1f}s")


def test_a_broken_edit_explains_itself():
    """The failures a user actually hits: a moved file, an empty timeline, a
    corrupt document, no ffmpeg."""
    doc = tl.Timeline(width=320, height=240, fps=25)
    doc.add(clip("gone.mp4", 0, 2))
    problems = doc.problems(MEDIA)
    assert any("no file at" in p and "gone.mp4" in p for p in problems), problems
    try:
        render(doc, "broken")
        raise AssertionError("rendering a missing source should raise")
    except erender.RenderError as exc:
        assert "gone.mp4" in str(exc), str(exc)

    try:
        erender.render(tl.Timeline(), Path(WORK) / "empty.mp4", base=MEDIA)
        raise AssertionError("rendering nothing should raise")
    except erender.RenderError as exc:
        assert "nothing on the timeline" in str(exc).lower(), str(exc)

    corrupt = Path(WORK) / "corrupt.json"
    corrupt.write_text('{"clips": [{"source": ', encoding="utf-8")
    assert tl.load(corrupt).clips == [], "a corrupt edit must open empty, not crash"

    half = Path(WORK) / "half.json"
    half.write_text('{"clips": [{"source": "chapters.mp4", "source_in": "x", '
                    '"source_out": null, "kind": "nonsense", "speed": 99}]}',
                    encoding="utf-8")
    recovered = tl.load(half)
    assert len(recovered.clips) == 1
    c = recovered.clips[0]
    assert c.kind == tl.VIDEO and c.speed == tl.MAX_SPEED and c.source_length >= tl.MIN_CLIP
    print("        missing file, empty timeline, corrupt and half-valid documents "
          "all handled")


def test_the_editor_survives_a_rerender():
    """Rendering twice over the same output, while the first file is still
    there, must not leave a half-written video behind."""
    doc = tl.Timeline(width=320, height=240, fps=25)
    doc.add(clip("chapters.mp4", 0.2, 1.4))
    out = render(doc, "twice")
    first = out.stat().st_size
    doc.add(clip("chapters.mp4", 2.2, 3.4))
    out = render(doc, "twice")
    assert out.stat().st_size > first, "the second render did not replace the first"
    assert dominant(rgb(out, 1.6)) == "green", "the added clip is not in the file"
    assert abs(analyze.probe(out).seconds - doc.duration) < 0.3
    print(f"        {first // 1024} KB -> {out.stat().st_size // 1024} KB, "
          f"{doc.duration:.1f}s")


if __name__ == "__main__":
    if not erender.available() or not analyze.ffprobe():
        print("ffmpeg and ffprobe are required for this suite")
        raise SystemExit(1)
    print(f"workspace: {WORK}\nbuilding fixtures...")
    build_media()
    print()
    check("clips play in the order the timeline says", test_clips_play_in_the_order_they_are_in)
    check("trims and speed changes are accurate", test_trims_and_speed_are_accurate)
    check("a transition actually blends both clips", test_a_transition_actually_blends)
    check("sound is where it should be", test_sound_is_where_it_should_be)
    check("titles and captions are drawn, and escaped", test_titles_and_captions_are_drawn)
    check("it fades to black, picture and sound", test_it_fades_to_black)
    check("the slow push moves, and can be turned off", test_ken_burns_moves_and_can_be_turned_off)
    check("dead air is gone from the rendered file", test_dead_air_is_gone_from_the_file)
    check("a Short holds the window it was cut from", test_a_short_holds_the_window_it_was_cut_from)
    check("awkward edits still render", test_awkward_edits_still_render)
    check("junk from a model cannot corrupt an edit", test_junk_from_a_model_cannot_corrupt_an_edit)
    check("a broken edit explains itself", test_a_broken_edit_explains_itself)
    check("re-rendering replaces the file cleanly", test_the_editor_survives_a_rerender)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        for f in FAILURES:
            print("  -", f)
        sys.exit(1)
    print(f"all {PASSED} green")
    shutil.rmtree(WORK, ignore_errors=True)
