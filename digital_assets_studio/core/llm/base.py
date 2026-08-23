"""Provider-agnostic types for text and image generation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Protocol


@dataclass
class Message:
    role: str      # "system" | "user" | "assistant"
    content: str


@dataclass
class Completion:
    text: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    raw: dict | None = field(default=None, repr=False)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class ImageResult:
    images: list[bytes]
    model: str = ""
    note: str = ""


class LLMError(RuntimeError):
    """Any failure talking to a provider, already made human-readable."""

    def __init__(self, message: str, status: int | None = None, provider: str = ""):
        super().__init__(message)
        self.status = status
        self.provider = provider


class TextProvider(Protocol):
    kind: str

    def complete(
        self,
        messages: list[Message],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout: float = 180.0,
    ) -> Completion: ...

    def list_models(self) -> list[str]: ...

    def test(self) -> str: ...


class ImageProvider(Protocol):
    kind: str

    def generate(self, prompt: str, model: str, count: int = 1,
                 size: str = "1024x1024", timeout: float = 300.0) -> ImageResult: ...

    def test(self) -> str: ...


def join_system(messages: Iterable[Message]) -> tuple[str, list[Message]]:
    """Split leading system messages out - several APIs want them separately."""
    system_parts: list[str] = []
    rest: list[Message] = []
    for m in messages:
        if m.role == "system":
            system_parts.append(m.content)
        else:
            rest.append(m)
    return "\n\n".join(system_parts).strip(), rest
