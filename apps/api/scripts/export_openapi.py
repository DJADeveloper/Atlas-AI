"""Export the OpenAPI spec (M09; ADR-0001).

The generated TypeScript client is the API contract's enforcement:
`pnpm gen` runs this, regenerates the client, and CI fails on any
uncommitted diff — drift is caught at PR time, not integration time.
Output is deterministic (sorted keys, stable separators) so the diff
is meaningful.
"""

import argparse
import json
import sys
from pathlib import Path

from atlas.config.settings import Settings
from atlas.presentation.app import create_app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    app = create_app(Settings())
    spec = app.openapi()
    payload = json.dumps(spec, indent=2, sort_keys=True) + "\n"
    args.output.write_text(payload)
    print(f"wrote {args.output} ({len(payload)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
