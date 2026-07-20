#!/usr/bin/env python3
"""Conventional-commit message check (pre-commit commit-msg hook).

Format: type(scope)!: subject
Types match the categories used by the changelog pipeline
(docs/53-cicd-strategy.md).
"""

import re
import sys
from pathlib import Path

PATTERN = re.compile(
    r"^(feat|fix|docs|test|build|ci|chore|refactor|perf|style|revert)"
    r"(\([a-z0-9][a-z0-9-]*\))?!?: \S.+",
)
EXEMPT_PREFIXES = ("Merge ", "Revert ", "fixup! ", "squash! ")


def main() -> int:
    message_file = Path(sys.argv[1])
    subject = message_file.read_text(encoding="utf-8").splitlines()[0]
    if subject.startswith(EXEMPT_PREFIXES) or PATTERN.match(subject):
        return 0
    sys.stderr.write(
        f"Commit subject does not follow Conventional Commits:\n  {subject}\n"
        "Expected: type(scope)?: subject  e.g. 'feat(api): add readiness probe'\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
