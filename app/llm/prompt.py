"""The WhatsApp system prompt. Edit system_prompt.md, not the callers."""

from pathlib import Path

_PATH = Path(__file__).resolve().parent / "system_prompt.md"


def load_system_prompt() -> str:
    return _PATH.read_text(encoding="utf-8").strip()
