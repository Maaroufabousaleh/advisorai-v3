#!/usr/bin/env python3
"""Write the offline Phase-4 paper-utility input contract.

This command performs no network calls, loads no model weights or credentials,
and does not evaluate or promote any model.  It records the exact baseline,
candidate, provenance, and cost fields required once a real Phase-3 gate record
exists.
"""

from __future__ import annotations

import argparse
import json
import os
from hashlib import sha256
from pathlib import Path

from advisorai.phase4 import build_preparation_manifest


def _write_immutable(path: Path, payload: object) -> str:
    encoded = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise FileExistsError(f"immutable preparation evidence differs: {path}")
    else:
        with path.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    return sha256(encoded).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.output_root.resolve()
    if root.exists():
        raise SystemExit("output root must be new; preparation evidence is immutable")
    manifest = build_preparation_manifest()
    digest = _write_immutable(root / "phase4-utility-preparation.json", manifest)
    (root / "phase4-utility-preparation.sha256").write_text(
        f"{digest}  phase4-utility-preparation.json\n", encoding="ascii"
    )
    print(
        json.dumps(
            {
                "state": manifest["state"],
                "phase4_admission_opened": manifest["phase4_admission_opened"],
                "evidence": str(root / "phase4-utility-preparation.json"),
                "sha256": digest,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
