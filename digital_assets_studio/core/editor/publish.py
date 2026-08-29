"""Publishing an edit straight from the editor.

The pipeline's upload step is the right thing when you are running a pipeline.
This is for the other case: you opened the editor, cut a video, and want it on
the channel without walking back through a twenty-step screen. Same API, same
resumable upload, same account resolution - the only difference is where the
title and description come from.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..publishing import youtube as yt

log = logging.getLogger(__name__)


class PublishError(RuntimeError):
    pass


def default_metadata(project, slug: str = "") -> dict:
    """Whatever the metadata step already wrote, so the form opens filled in.

    An editor that makes you retype a description the suite has already written
    is an editor people paste into by hand and get wrong."""
    slug = slug or str(project.answer("episode_slug", "") or "")
    raw = project.read_text(f"drafts/episodes/{slug}.metadata.json", "") if slug else ""
    data = {}
    if raw:
        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001
            data = {}
    titles = data.get("titles") or []
    return {
        "title": str(project.answer("final_title", "") or (titles[0] if titles else "")
                     or project.answer("episode_title", "") or project.name),
        "description": str(data.get("description", "") or ""),
        "tags": [str(t) for t in (data.get("tags") or [])][:30],
        "pinned_comment": str(data.get("pinned_comment", "") or ""),
    }


def publish(project, video: Path, title: str, description: str = "",
            tags: list[str] | None = None, privacy: str = "private",
            category: str = "Education", channel: str = "", thumbnail: Path | None = None,
            captions: Path | None = None, playlist: str = "", made_for_kids: bool = False,
            publish_at: str = "", language: str = "en", progress=None, note=None) -> dict:
    """Upload one edited file and decorate it.

    The upload itself is fatal if it fails - there is nothing to decorate. Every
    step after it is best-effort and reported: a thumbnail that would not set is
    not a reason to lose a video that is already on the channel.
    """
    video = Path(video)
    if not video.exists():
        raise PublishError(f"There is no file at {video}. Render the edit first.")
    if not str(title or "").strip():
        raise PublishError("A video needs a title before it can be uploaded.")

    slug = yt.resolve(channel)
    account = yt.get_account(slug)
    if note:
        note(f"Uploading {video.name} to {account.display or 'YouTube'}")

    result = yt.upload_video(
        video, title=title.strip()[:100], description=description or "",
        tags=list(tags or []), category=category, privacy=privacy,
        publish_at=publish_at or None, made_for_kids=made_for_kids, language=language,
        progress=(lambda f, m: progress(f * 0.85, m)) if progress else None,
        account=slug)

    video_id = str(result.get("id", ""))
    warnings: list[str] = []

    if thumbnail is not None and Path(thumbnail).exists():
        try:
            if progress:
                progress(0.9, "Setting the thumbnail")
            yt.set_thumbnail(video_id, Path(thumbnail), account=slug)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Thumbnail not set: {exc}")

    if captions is not None and Path(captions).exists():
        try:
            if progress:
                progress(0.94, "Uploading subtitles")
            yt.upload_caption(video_id, Path(captions), language=language, account=slug)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Subtitles not uploaded: {exc}")

    if playlist:
        try:
            if progress:
                progress(0.97, f"Adding to playlist {playlist}")
            yt.add_to_playlist(yt.ensure_playlist(playlist, account=slug), video_id,
                               account=slug)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Playlist not updated: {exc}")

    for message in warnings:
        log.warning(message)
        if note:
            note(message)

    url = f"https://youtu.be/{video_id}"
    project.write_text(f"build/{video.stem}.upload.json", json.dumps(result, indent=2)[:20000])
    project.set_answer("video_url", url)
    project.set_answer("video_id", video_id)
    project.save()
    if progress:
        progress(1.0, "Published")
    return {"id": video_id, "url": url, "channel": account.display or slug,
            "warnings": warnings}


__all__ = ["PublishError", "default_metadata", "publish"]
