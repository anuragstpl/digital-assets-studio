"""The video editor: an edit document, a renderer, an AI that edits it, and the
one call that puts the result on YouTube.

    from ..core import editor
    doc = editor.ai.assemble(project)
    editor.ai.auto_edit(doc, "cut the slow open and title the hook", project.dir)
    editor.render.render(doc, project.build / "cut.mp4", project.dir)
    editor.publish.publish(project, out, title="...")
"""
from __future__ import annotations

from . import ai, analyze, publish, render, timeline
from .ai import EditorError
from .render import RenderError
from .timeline import Audio, Clip, Text, Timeline, load

__all__ = ["ai", "analyze", "publish", "render", "timeline",
           "Audio", "Clip", "EditorError", "RenderError", "Text", "Timeline", "load"]
