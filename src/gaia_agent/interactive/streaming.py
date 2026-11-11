"""Stream only the public answer field of an ordered direct routing decision."""

import json
import re
from contextlib import contextmanager
from contextvars import ContextVar

_observer = ContextVar("public_answer_observer", default=None)
_first_text = ContextVar("first_stream_text", default=None)
_mode = ContextVar("public_answer_mode", default="decision")
_prefix = re.compile(r'^\s*\{\s*(?:"action"\s*:\s*"decide"\s*,\s*"tool_calls"\s*:\s*\[\s*\]\s*,\s*"decision"\s*:\s*\{\s*)?"route"\s*:\s*"(?:direct|clarify)"\s*,\s*"needs_verification"\s*:\s*false\s*,\s*"answer"\s*:\s*"')
_string = re.compile(r'(?:[^"\\]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*')


def run_event_updates(previous, current):
    """Translate authenticated, sanitized run states into individual UI events."""
    if previous is None:
        return [("run.snapshot", current)]
    events = []
    old_text, text = previous.get("partial_answer", ""), current.get("partial_answer", "")
    if text != old_text:
        if text.startswith(old_text):
            events.append(("answer.delta", {"delta": text[len(old_text):]}))
        else:
            events.append(("answer.reset", {"text": text}))
    old, new = previous.get("progress", []), current.get("progress", [])
    overlap = next((n for n in range(min(len(old), len(new)), 0, -1) if old[-n:] == new[:n]), 0)
    for entry in new[overlap:]:
        event = entry.get("event", "")
        if event.startswith("tool_call_"):
            kind = "tool." + event.removeprefix("tool_call_")
        elif event.startswith("verify_"):
            kind = "verification." + event.removeprefix("verify_")
        elif event == "validation_rejected":
            kind = "verification.rejected"
        else:
            kind = "stage.updated"
        events.append((kind, entry))
    if previous.get("status") != current.get("status"):
        events.append(("run." + current["status"], current))
    return events


def classify_run_updates(previous, current):
    """Classify safe snapshot changes; no tool arguments or model drafts are exposed."""
    if previous is None:
        return ["run.snapshot"]
    kinds = []
    if previous.get("partial_answer") != current.get("partial_answer"):
        kinds.append("answer.updated")
    old, new = previous.get("progress", []), current.get("progress", [])
    overlap = next((n for n in range(min(len(old), len(new)), 0, -1) if old[-n:] == new[:n]), 0)
    for entry in new[overlap:]:
        event = entry.get("event", "")
        if event.startswith("tool_call_"):
            kinds.append("tool." + event.removeprefix("tool_call_"))
        elif event.startswith("verify_"):
            kinds.append("verification." + event.removeprefix("verify_"))
        elif event == "validation_rejected":
            kinds.append("verification.rejected")
        else:
            kinds.append("stage.updated")
    if previous.get("status") != current.get("status"):
        kinds.append("run." + current.get("status", "unknown"))
    return kinds


@contextmanager
def observe_first_model_text(callback):
    # Shared across nested routing / memory calls, but isolated between tasks.
    token = _first_text.set(callback)
    try:
        yield
    finally:
        _first_text.reset(token)


def record_model_text(delta):
    callback = _first_text.get()
    if callback is not None and delta:
        callback()


@contextmanager
def answer_stream(observer, *, mode="decision"):
    token = _observer.set(observer)
    mode_token = _mode.set(mode)
    try:
        yield
    finally:
        _mode.reset(mode_token)
        _observer.reset(token)


def stream_mode():
    return _mode.get()


def observer():
    return _observer.get()


class PartialDecision:
    def __init__(self, emit):
        self.emit, self.raw, self.last = emit, "", ""

    def feed(self, delta):
        if not isinstance(delta, str) or len(self.raw) + len(delta) > 100000:
            return
        self.raw += delta
        prefix = _prefix.match(self.raw)
        if not prefix:
            return
        encoded = _string.match(self.raw, prefix.end()).group()
        try:
            value = json.loads('"' + encoded + '"')
            value.encode("utf-8")
        except (ValueError, UnicodeError):
            return
        if value != self.last and len(value) <= 12000:
            self.last = value
            self.emit(value)


class PartialCandidate:
    """Expose only a top-level specialist candidate string, never evidence or tool data."""
    def __init__(self, emit):
        self.emit, self.raw, self.last = emit, "", ""

    def feed(self, delta):
        if not isinstance(delta, str) or len(self.raw) + len(delta) > 100000:
            return
        self.raw += delta
        text = self.raw
        index = len(text) - len(text.lstrip())
        if index >= len(text) or text[index] != '{':
            return
        index += 1
        decoder = json.JSONDecoder()
        while index < len(text):
            while index < len(text) and text[index].isspace(): index += 1
            if index >= len(text) or text[index] == '}': return
            try:
                key, index = decoder.raw_decode(text, index)
            except ValueError:
                return
            if not isinstance(key, str): return
            while index < len(text) and text[index].isspace(): index += 1
            if index >= len(text) or text[index] != ':': return
            index += 1
            while index < len(text) and text[index].isspace(): index += 1
            if index >= len(text): return
            if key == 'candidate_answer':
                if text[index] != '"': return
                encoded = _string.match(text, index + 1).group()
                try:
                    value = json.loads('"' + encoded + '"')
                    value.encode('utf-8')
                except (ValueError, UnicodeError):
                    return
                if value != self.last and len(value) <= 12000:
                    self.last = value
                    self.emit(value)
                return
            try:
                _, index = decoder.raw_decode(text, index)
            except ValueError:
                return
            while index < len(text) and text[index].isspace(): index += 1
            if index >= len(text) or text[index] != ',': return
            index += 1
