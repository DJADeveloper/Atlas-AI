"""Citation-marker extraction, streaming-safe (M08; spine §10).

The failure mode this guards: markers arrive SPLIT across SSE deltas
("[1" in one chunk, "]" in the next). Extraction therefore always
operates on the ACCUMULATED buffer — never on a lone delta — and the
property test proves the reconstructed marker set is identical no
matter how the stream was chopped.

Markers are `[n]` with 1-3 digits. Extraction reports what the model
EMITTED, in first-appearance order, deduplicated; deciding whether an
emitted marker is valid (within the offered evidence range) is the
caller's job — a hallucinated `[9]` must be visible to the eval, not
silently repaired here.
"""

import re

_MARKER = re.compile(r"\[(\d{1,3})\]")


def extract_markers(text: str) -> list[int]:
    """Unique markers in first-appearance order."""
    seen: set[int] = set()
    ordered: list[int] = []
    for match in _MARKER.finditer(text):
        value = int(match.group(1))
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


class MarkerAccumulator:
    """Feed stream deltas; get newly completed markers back.

    Re-scans the accumulated buffer on every feed (chat answers are
    small; correctness beats cleverness here) so a marker split across
    any number of deltas completes exactly once, when its closing
    bracket arrives — the moment the SSE `citation` event should fire.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._seen: set[int] = set()
        self._ordered: list[int] = []

    def feed(self, delta: str) -> list[int]:
        self._buffer += delta
        fresh: list[int] = []
        for value in extract_markers(self._buffer):
            if value not in self._seen:
                self._seen.add(value)
                self._ordered.append(value)
                fresh.append(value)
        return fresh

    @property
    def markers(self) -> tuple[int, ...]:
        """All markers seen so far, first-appearance order."""
        return tuple(self._ordered)

    @property
    def text(self) -> str:
        return self._buffer
