"""Record one shadow day (or catch-up set) from a qualified view, hosted-safe.

Two modes share the recording code path (``run_shadow_days``):

- ``rehearsal``: deterministic March-2024 fixture view, fixed receipts.
  Safe anywhere; this is how the hosted workflow is rehearsed.
- ``live``: real qualified snapshot + forward segment. Refuses unless
  ``--allow-live`` is passed AND ``BTC_SHADOW_LIVE_ARMED=true`` is set;
  the workflow additionally gates live on the default branch plus an
  owner-held repository variable. Live scoring stays disabled until the
  owner completes the documented activation step.

State continuity: ``--prior-state-dir`` points at the previous run's
downloaded artifact. Its report hashes are re-verified (tamper/mixup
fails closed), its logs seed this run, and the new report links the
prior report hash. After recording, previously recorded dates must be
byte-identical (append-only; a rewrite fails the run).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from advisorai.research.btc_shadow_job import run_shadow_days
from advisorai.research.btc_shadow_log import MODEL_ID, bar_close_ms, load_view

REHEARSAL_SNAPSHOT_BARS = 64
REHEARSAL_SNAPSHOT_START = "2024-01-01"
REHEARSAL_FORWARD_START = "2024-03-05"
# (close, open) per forward day; matches the offline scoring rehearsal.
REHEARSAL_FORWARD = (
    ("200.0", "200.0"),
    ("201.0", "207.50"),
    ("202.0", "209.0"),
    ("203.0", "210.0"),
    ("204.0", "211.0"),
    ("205.0", "212.0"),
)
REHEARSAL_RECEIPT_LAG_MS = 3_600_000
STATE_FILES = ("decisions.jsonl", "fills.jsonl", "shadow_state.json")


def _rows(start: str, spec: list[tuple[str, str]]) -> list[dict]:
    base = datetime.fromisoformat(start).date()
    return [
        {
            "date": (base + timedelta(days=i)).isoformat(),
            "open": open_,
            "high": max(close, open_),
            "low": min(close, open_),
            "adj_close": close,
            "volume": "10.0",
        }
        for i, (close, open_) in enumerate(spec)
    ]


def _write_snapshot(root: Path, rows: list[dict]) -> str:
    payload = (json.dumps({"BTCUSDT": rows}, indent=1) + "\n").encode()
    digest = hashlib.sha256(payload).hexdigest()
    root.mkdir(parents=True, exist_ok=True)
    (root / "bars.json").write_bytes(payload)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "source": "rehearsal-fixture-v1",
                "symbol": "BTCUSDT",
                "interval": "1d",
                "timestamp_units": "ms-utc",
                "bars_sha256": digest,
            },
            indent=1,
        )
        + "\n"
    )
    return digest


def build_rehearsal_view(root: Path) -> tuple[tuple[dict, ...], dict[str, int], str]:
    """Deterministic fixture view + receipts + input hash (same every run)."""
    snapshot = root / "snapshot"
    forward = root / "forward"
    snapshot_digest = _write_snapshot(
        snapshot, _rows(REHEARSAL_SNAPSHOT_START, [("100.0", "100.0")] * 64)
    )
    forward_digest = _write_snapshot(
        forward, _rows(REHEARSAL_FORWARD_START, list(REHEARSAL_FORWARD))
    )
    view = load_view(snapshot, forward)
    receipts = {
        str(row["date"]): bar_close_ms(str(row["date"])) + REHEARSAL_RECEIPT_LAG_MS for row in view
    }
    forward_hash = hashlib.sha256(f"{snapshot_digest}:{forward_digest}".encode()).hexdigest()
    return view, receipts, forward_hash


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_prior(prior: Path, state_dir: Path) -> dict[str, str | None]:
    """Seed from the prior artifact after re-verifying its own report hashes."""
    report_path = prior / "report.json"
    if not report_path.is_file():
        raise ValueError(f"prior state {prior} has no report.json")
    report = json.loads(report_path.read_text())
    if report.get("mode") not in {"rehearsal", "live"}:
        raise ValueError(f"prior report has unknown mode: {report.get('mode')!r}")
    expected = report.get("log_hashes", {})
    state_dir.mkdir(parents=True, exist_ok=True)
    for name in STATE_FILES:
        source = prior / name
        if source.is_file():
            (state_dir / name).write_bytes(source.read_bytes())
    for name in STATE_FILES:
        actual = _sha256(state_dir / name)
        want = expected.get(name)
        if actual != want:
            raise ValueError(f"prior state {name} hash mismatch: artifact={actual} report={want}")
    return {"prior_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest()}


APPEND_ONLY_LOGS = ("decisions.jsonl", "fills.jsonl")


def _assert_state_continuity(state_dir: Path, prior_bytes: dict[str, bytes]) -> None:
    """Logs stay append-only; the state snapshot only moves forward."""
    for name, before in prior_bytes.items():
        after = (state_dir / name).read_bytes()
        if name in APPEND_ONLY_LOGS:
            if not after.startswith(before):
                raise ValueError(f"rewrite detected in {name}: recorded bytes changed")
            continue
        if name != "shadow_state.json":
            raise ValueError(f"unexpected prior file {name}")
        prior_state = json.loads(before.decode())
        new_state = json.loads(after.decode())
        prior_runs = prior_state.get("runs", [])
        new_runs = new_state.get("runs", [])
        if new_runs[: len(prior_runs)] != prior_runs or len(new_runs) < len(prior_runs):
            raise ValueError("rewrite detected in shadow_state.json: run history changed")
        if (new_state.get("last_completed_date") or "") < (
            prior_state.get("last_completed_date") or ""
        ):
            raise ValueError("rewrite detected in shadow_state.json: clock moved back")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("rehearsal", "live"))
    parser.add_argument("--dates", required=True, help="Comma-separated decision dates.")
    parser.add_argument("--recorded-at-ms", required=True, type=int)
    parser.add_argument("--state-dir", required=True, help="Output state directory.")
    parser.add_argument("--prior-state-dir", default=None, help="Previous run artifact.")
    parser.add_argument("--snapshot-dir", default=None, help="Live mode qualified snapshot.")
    parser.add_argument("--forward-dir", default=None, help="Live mode forward segment.")
    parser.add_argument("--receipts", default=None, help="Live mode receipts JSON.")
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args()

    dates = [item.strip() for item in args.dates.split(",") if item.strip()]
    if not dates:
        raise ValueError("at least one decision date is required")
    state_dir = Path(args.state_dir)
    prior_link: dict[str, str | None] = {}
    prior_bytes: dict[str, bytes] = {}
    if args.prior_state_dir:
        prior_link = _copy_prior(Path(args.prior_state_dir), state_dir)
        prior_bytes = {
            name: (state_dir / name).read_bytes()
            for name in STATE_FILES
            if (state_dir / name).is_file()
        }

    if args.mode == "live":
        if not args.allow_live or os.environ.get("BTC_SHADOW_LIVE_ARMED") != "true":
            raise ValueError("live recording is not armed (flag + BTC_SHADOW_LIVE_ARMED)")
        if not args.snapshot_dir or not args.forward_dir or not args.receipts:
            raise ValueError("live mode needs --snapshot-dir, --forward-dir, and --receipts")
        snapshot = Path(args.snapshot_dir)
        forward = Path(args.forward_dir)
        receipts = {str(k): int(v) for k, v in json.loads(Path(args.receipts).read_text()).items()}
        snapshot_hash = json.loads((snapshot / "manifest.json").read_text())["bars_sha256"]
        forward_hash_value = json.loads((forward / "manifest.json").read_text())["bars_sha256"]
        forward_hash = hashlib.sha256(f"{snapshot_hash}:{forward_hash_value}".encode()).hexdigest()
        view = load_view(snapshot, forward)
    else:
        if args.allow_live:
            raise ValueError("rehearsal mode takes no --allow-live")
        view, receipts, forward_hash = build_rehearsal_view(state_dir / "fixture-view")

    result = run_shadow_days(
        view=view,
        forward_hash=forward_hash,
        state_dir=state_dir,
        dates=dates,
        received_at=receipts,
        recorded_at_ms=args.recorded_at_ms,
    )
    _assert_state_continuity(state_dir, prior_bytes)
    report = {
        "mode": args.mode,
        "model_id": MODEL_ID,
        "dates": dates,
        "recorded_at_ms": args.recorded_at_ms,
        "forward_hash": forward_hash,
        "processed": result["processed"],
        "quarantined": result["quarantined"],
        "skipped": result["skipped"],
        "fills_logged": result["fills_logged"],
        "last_completed_date": result["last_completed_date"],
        "log_hashes": {name: _sha256(state_dir / name) for name in STATE_FILES},
        **prior_link,
    }
    (state_dir / "report.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps(report, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
