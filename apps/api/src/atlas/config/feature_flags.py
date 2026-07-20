"""Feature flags — M02 in-memory stub.

M03 adds the `feature_flags` table as an override layer on top of these
seed values; the read interface stays the same so call sites never change.
Unknown flags are OFF: a typo can never silently enable behavior.
"""

from collections.abc import Mapping


class FeatureFlags:
    def __init__(self, seed: Mapping[str, bool] | None = None) -> None:
        self._flags: dict[str, bool] = dict(seed or {})

    def is_enabled(self, flag: str) -> bool:
        return self._flags.get(flag, False)

    def snapshot(self) -> dict[str, bool]:
        """A defensive copy for diagnostics and the future settings UI."""
        return dict(self._flags)
