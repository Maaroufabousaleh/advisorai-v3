"""P07 fixture rehearsal: bounded intake -> handoff -> replay -> registry.

Offline rehearsal only (canned metadata transport, synthetic bars). Runs
the durable research chain and records per-step fingerprints so a stop or
crash resumes without duplicating artifacts. Evidence lives in the given
state dir; reruns with identical inputs reuse verified artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from advisorai.integrations import HttpClientConfig, SafeHttpClient
from advisorai.research import (
    ResearchMetadataProvider,
    ResearchMetadataScout,
)
from advisorai.research.replay import ExposureSpec
from advisorai.research.workflow import ResearchWorkflow, open_workflow_state

SOURCE_URL = "https://doi.org/10.1016/j.jfineco.2011.11.003"
PLAN = "p07 fixture rehearsal v1"
POLICY_ID = "p07-fixture-v1"
ENVIRONMENT_HASH = "cd" * 32

_CROSSREF_BODY = json.dumps(
    {
        "message": {
            "title": ["Time series momentum"],
            "author": [{"given": "Tobias J.", "family": "Moskowitz"}],
            "DOI": "10.1016/j.jfineco.2011.11.003",
            "published-print": {"date-parts": [[2012, 5]]},
            "license": [{"content-version": "vor", "URL": "https://example.invalid/license"}],
        }
    }
).encode()


def _bars(count: int = 300) -> tuple[dict[str, Any], ...]:
    base = datetime(2020, 1, 2, tzinfo=UTC).date()
    rows: list[dict[str, Any]] = []
    current = base
    added = 0
    while added < count:
        if current.weekday() < 5:
            price = 100 + added
            rows.append(
                {
                    "date": current.isoformat(),
                    "open": float(price),
                    "high": float(price + 1),
                    "low": float(price - 1),
                    "adj_close": float(price),
                    "volume": 1000,
                }
            )
            added += 1
        current += timedelta(days=1)
    return tuple(rows)


def _fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _open_workflow(state_dir: Path, snapshot_dir: Path) -> ResearchWorkflow:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    bars_path = snapshot_dir / "bars.json"
    if not bars_path.is_file():
        bars_path.write_text(
            json.dumps({"FIXA": list(_bars()), "FIXB": list(_bars()), "FIXC": list(_bars(5))})
        )
    registry, _ledgers, coordinator = open_workflow_state(state_dir)

    def requester(method: str, url: str, headers: Any, body: Any, timeout: float) -> Any:
        return 200, _CROSSREF_BODY, (("Content-Type", "application/json"),)

    client = SafeHttpClient(
        HttpClientConfig(allowed_hosts=("api.crossref.org",)), requester=requester
    )
    scout = ResearchMetadataScout(
        registry=registry,
        coordinator=coordinator,
        clients={ResearchMetadataProvider.CROSSREF_API: client},
    )
    return ResearchWorkflow(
        registry=registry,
        coordinator=coordinator,
        scout=scout,
        snapshot_root=snapshot_dir,
        state_dir=state_dir,
    )


def _load_progress(state_dir: Path) -> dict[str, Any]:
    path = state_dir / "rehearsal.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


def _save_progress(state_dir: Path, progress: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "rehearsal.json").write_text(json.dumps(progress, indent=1, sort_keys=True) + "\n")


def run_fixture_chain(state_dir: Path, snapshot_dir: Path) -> dict[str, Any]:
    """Run the fixture chain, resuming past verified steps."""
    workflow = _open_workflow(state_dir, snapshot_dir)
    progress = _load_progress(state_dir)

    scout_step = progress.get("scout")
    if scout_step is not None and workflow.registry.get(UUID(scout_step["paper_id"])) is not None:
        paper_id = scout_step["paper_id"]
    else:
        _, paper = workflow.scout_paper(
            source_url=SOURCE_URL, claimed_result="TSM persists", relevance="momentum"
        )
        assert paper is not None
        paper_id = str(paper.artifact_id)
        progress["scout"] = {"source_url": SOURCE_URL, "paper_id": paper_id}
        _save_progress(state_dir, progress)

    hypothesis_key = _fingerprint({"paper_id": paper_id, "plan": PLAN})
    hypothesis_step = progress.get("hypothesis")
    if (
        hypothesis_step is not None
        and hypothesis_step.get("key") == hypothesis_key
        and workflow.registry.get(UUID(hypothesis_step["hypothesis_id"])) is not None
    ):
        hypothesis_id = hypothesis_step["hypothesis_id"]
    else:
        paper = workflow.registry.get(UUID(paper_id))
        assert paper is not None
        _, hypothesis = workflow.librarian_hypothesis(
            paper=paper,  # type: ignore[arg-type]
            economic_rationale="continuation",
            universe="FIXA,FIXB",
            horizon="252d/21d",
            baseline="buy-and-hold",
            preregistered_plan=PLAN,
            owner="p07-rehearsal",
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
        )
        hypothesis_id = str(hypothesis.artifact_id)
        progress["hypothesis"] = {"key": hypothesis_key, "hypothesis_id": hypothesis_id}
        _save_progress(state_dir, progress)

    experiment_key = _fingerprint(
        {
            "hypothesis_id": hypothesis_id,
            "policy": POLICY_ID,
            "exposures": ["FIXA", "FIXB", "FIXC"],
        }
    )
    experiment_step = progress.get("experiments")
    if (
        experiment_step is not None
        and experiment_step.get("key") == experiment_key
        and all(
            workflow.registry.get(UUID(item)) is not None
            for item in experiment_step.get("experiment_ids", [])
        )
        and experiment_step.get("experiment_ids")
    ):
        experiment_ids = experiment_step["experiment_ids"]
        failed = experiment_step.get("failed", [])
    else:
        hypothesis = workflow.registry.get(UUID(hypothesis_id))
        assert hypothesis is not None
        _, outcomes = workflow.run_experiments(
            hypothesis=hypothesis,  # type: ignore[arg-type]
            exposures=(
                ExposureSpec(
                    symbol="FIXA",
                    fee_bps=Decimal("1"),
                    spread_bps=Decimal("1"),
                    slippage_bps=Decimal("2"),
                ),
                ExposureSpec(
                    symbol="FIXB",
                    fee_bps=Decimal("1"),
                    spread_bps=Decimal("1"),
                    slippage_bps=Decimal("2"),
                ),
                ExposureSpec(
                    symbol="FIXC",
                    fee_bps=Decimal("1"),
                    spread_bps=Decimal("1"),
                    slippage_bps=Decimal("2"),
                ),
            ),
            policy_id=POLICY_ID,
            environment_hash=ENVIRONMENT_HASH,
            require_all_windows=False,
        )
        experiment_ids = [str(item.experiment_id) for item in outcomes if item.succeeded]
        failed = sorted(item.symbol for item in outcomes if not item.succeeded)
        progress["experiments"] = {
            "key": experiment_key,
            "experiment_ids": experiment_ids,
            "failed": failed,
        }
        _save_progress(state_dir, progress)

    prior = workflow.prior_experiments("momentum")
    return {
        "paper_id": paper_id,
        "hypothesis_id": hypothesis_id,
        "experiment_ids": experiment_ids,
        "failed": failed,
        "prior_count": len(prior),
        "prior_succeeded": sum(1 for item in prior if item.succeeded),
        "prior_rejected": sum(1 for item in prior if not item.succeeded),
        "paper_rows": workflow.registry.count("paper"),
        "experiment_rows": workflow.registry.count("experiment"),
        "validation_rows": workflow.registry.count("validation"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, help="Out-of-tree rehearsal state.")
    parser.add_argument("--snapshot-dir", required=True, help="Out-of-tree fixture snapshot.")
    args = parser.parse_args()
    report = run_fixture_chain(Path(args.state_dir), Path(args.snapshot_dir))
    print(json.dumps(report, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
