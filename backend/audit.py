"""Append-only audit trail: output/audit_trail.json.

Every agent-loop step (model response, tool results, start, end, stop) is
appended the moment it happens. Earlier entries are never edited or removed.
Text is shortened and redacted before it is written.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from models import AuditEvent

AUDIT_PATH = Path(__file__).resolve().parent.parent / "output" / "audit_trail.json"
MAX_TEXT = 500

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
SECRET_RE = re.compile(r"\b(sk|pk|pt)-[A-Za-z0-9_-]{8,}\b")


def clean(value: Any, limit: int = MAX_TEXT) -> str:
    """Shorten and redact anything headed for the audit file."""
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = EMAIL_RE.sub("[email]", text)
    text = CARD_RE.sub("[number]", text)
    text = SECRET_RE.sub("[secret]", text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


class AuditTrail:
    def __init__(self, path: Path = AUDIT_PATH):
        self.path = path
        self._lock = threading.Lock()

    def record(self, *, run_id: str, agent: str, event: str, step: int = 0, ticket_id: int | None = None,
               tool_calls: list[dict] | None = None, tool_results: list[dict] | None = None,
               text: Any = None, tokens: dict | None = None, detail: dict | None = None) -> None:
        entry = AuditEvent(
            timestamp=datetime.now().astimezone().isoformat(timespec="milliseconds"),
            run_id=run_id,
            ticket_id=ticket_id,
            agent=agent,
            step=step,
            event=event,
            tool_calls=[{"tool": c["tool"], "args": clean(c.get("args"), 300)} for c in tool_calls or []],
            tool_results=[{k: clean(v, 400) if k in ("result", "error") else v for k, v in r.items()}
                          for r in tool_results or []],
            text=clean(text) if text is not None else None,
            tokens=tokens,
            detail=detail,
        )
        self._append(entry.model_dump(mode="json"))

    def _append(self, entry: dict) -> None:
        with self._lock:
            self.path.parent.mkdir(exist_ok=True)
            existing = json.loads(self.path.read_text()) if self.path.exists() else []
            existing.append(entry)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(existing, indent=2) + "\n")
            tmp.replace(self.path)  # atomic: a crash mid-write can't leave a half-written file
