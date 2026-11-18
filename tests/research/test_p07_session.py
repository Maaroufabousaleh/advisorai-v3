"""P07 session rehearsal: research chain + shadow day in one evidence bundle."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path

from advisorai.research.btc_shadow_job import run_shadow_days
from advisorai.research.btc_shadow_log import bar_close_ms, load_view

REHEARSAL_PATH = Path(__file__).resolve().parents[2] / "scripts" / "rehearse_p07_fixture.py"
HOUR_MS = 3_600_000


def _load_chain():
    spec = importlib.util.spec_from_file_location("rehearse_p07_fixture", REHEARSAL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def _write_snapshot(root: Path, rows: list[dict]) -> None:
    payload = (json.dumps({"BTCUSDT": rows}, indent=1) + "\n").encode()
    (root / "bars.json").write_bytes(payload)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "source": "x",
                "symbol": "BTCUSDT",
                "interval": "1d",
                "timestamp_units": "ms-utc",
                "cutoff_rules": "x",
                "bars_sha256": hashlib.sha256(payload).hexdigest(),
            },
            indent=1,
        )
        + "\n"
    )


def _file_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_session(session_dir: Path, session_date: str) -> dict:
    """Run one fixture session: research chain plus one shadow day, one manifest."""
    chain = _load_chain()
    research_state = session_dir / "research"
    chain_report = chain.run_fixture_chain(research_state, session_dir / "research_snapshot")

    snap = session_dir / "snap"
    snap.mkdir(exist_ok=True)
    _write_snapshot(snap, _rows("2024-01-01", [("100.0", "100.0")] * 64))
    forward = session_dir / "forward"
    forward.mkdir(exist_ok=True)
    _write_snapshot(forward, _rows("2024-03-05", [("200.0", "200.0"), ("201.0", "207.50")]))
    view = load_view(snap, forward)
    receipts = {str(row["date"]): bar_close_ms(str(row["date"])) + HOUR_MS for row in view}
    shadow_state = session_dir / "shadow"
    shadow_result = run_shadow_days(
        view=view,
        forward_hash="f" * 64,
        state_dir=shadow_state,
        dates=[session_date],
        received_at=receipts,
        recorded_at_ms=bar_close_ms(session_date) + 2 * HOUR_MS,
    )
    manifest = {
        "session_date": session_date,
        "chain": {
            "paper_id": chain_report["paper_id"],
            "hypothesis_id": chain_report["hypothesis_id"],
            "experiment_ids": chain_report["experiment_ids"],
            "failed": chain_report["failed"],
            "prior_count": chain_report["prior_count"],
            "prior_succeeded": chain_report["prior_succeeded"],
            "prior_rejected": chain_report["prior_rejected"],
        },
        "shadow": shadow_result,
        "artifacts": {
            "decisions_sha256": _file_hash(shadow_state / "decisions.jsonl"),
            "fills_sha256": _file_hash(shadow_state / "fills.jsonl"),
        },
    }
    (session_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    )
    return manifest


def test_session_bundles_chain_and_shadow_evidence(tmp_path):
    manifest = run_session(tmp_path / "session", "2024-03-05")
    assert manifest["session_date"] == "2024-03-05"
    assert manifest["chain"]["prior_count"] == 3
    assert manifest["chain"]["prior_succeeded"] == 2
    assert manifest["chain"]["prior_rejected"] == 1
    assert manifest["chain"]["failed"] == ["FIXC"]
    assert manifest["shadow"]["processed"] == ["2024-03-05"]
    assert manifest["shadow"]["quarantined"] == []
    assert manifest["artifacts"]["decisions_sha256"] is not None
    stored = json.loads((tmp_path / "session" / "manifest.json").read_text())
    assert stored == manifest


def test_session_rerun_reuses_both_halves(tmp_path):
    session = tmp_path / "session"
    first = run_session(session, "2024-03-05")
    second = run_session(session, "2024-03-05")
    assert second["chain"] == first["chain"]
    assert second["shadow"]["processed"] == []
    assert second["artifacts"] == first["artifacts"]
