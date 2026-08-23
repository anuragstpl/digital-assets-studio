"""Pipeline registry."""
from __future__ import annotations

from ..core.pipeline import Pipeline
from .audiobook.pipeline import AUDIOBOOK_PIPELINE
from .books.pipeline import BOOK_PIPELINE
from .course.pipeline import COURSE_PIPELINE
from .mobile.pipeline import MOBILE_PIPELINE
from .podcast.pipeline import PODCAST_PIPELINE
from .printables.pipeline import PRINTABLES_PIPELINE
from .youtube.pipeline import YOUTUBE_PIPELINE

PIPELINES: list[Pipeline] = [
    BOOK_PIPELINE,
    PRINTABLES_PIPELINE,
    AUDIOBOOK_PIPELINE,
    YOUTUBE_PIPELINE,
    PODCAST_PIPELINE,
    COURSE_PIPELINE,
    MOBILE_PIPELINE,
]
BY_ID = {p.id: p for p in PIPELINES}


def get(pipeline_id: str) -> Pipeline | None:
    return BY_ID.get(pipeline_id)
