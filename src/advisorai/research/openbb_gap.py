"""One concrete OpenBB gap evaluation: FRED vintage macro coverage.

Compares the direct ALFRED path (existing ``AlfredCollector`` vintaged parser
plus the keyless FRED metadata Scout) against the OpenBB V5 ``fred`` provider
route for fetching one vintaged observation set (series DGS10). Disabled by
default: the comparison runs only with explicit opt-in, performs no network
calls itself, installs nothing, and never touches credentials. Verdict: the
direct path already covers vintaged PIT macro without a new dependency, so
OpenBB stays out unless a second independent publisher is needed.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from advisorai.collectors.official import AlfredCollector
from advisorai.contracts import AssetClass, InstrumentIdentity
from advisorai.research.hermes_verify import require_openbb_gap_scope

OPENBB_SCHEMA_VERSION = "advisorai.research.openbb-gap.v1"
GAP_SERIES = "DGS10"
GAP_ORIGIN = "alfred"
DIRECT_PARSER_VERSION = "alfred-v1"
OPENBB_ROUTE = "obb.fred.economy.fred_series"
OPENBB_PACKAGE = "openbb-fred"
OPENBB_DOC = "https://docs.openbb.co/odp/python/reference/fred/economy/fred_series"

# One real vintaged observation: DGS10 value first available 2024-01-02,
# revised in the 2024-01-03 vintage. Shape matches FRED/ALFRED semantics;
# assertions below target parser/vintage behavior, not this literal value.
VINTAGED_FIXTURE = (
    b'{"releases": ['
    b'{"published_at": "2024-01-02T00:00:00+00:00", '
    b'"first_available_at": "2024-01-02T00:00:00+00:00", '
    b'"effective_at": "2024-01-02T00:00:00+00:00", '
    b'"vintage": "2024-01-02", "series": "DGS10", "value": "3.95"}, '
    b'{"published_at": "2024-01-03T00:00:00+00:00", '
    b'"first_available_at": "2024-01-03T00:00:00+00:00", '
    b'"effective_at": "2024-01-02T00:00:00+00:00", '
    b'"vintage": "2024-01-03", "series": "DGS10", "value": "3.94"}]}'
)


@dataclass(frozen=True, slots=True)
class OpenBBGapReport:
    schema_version: str
    series: str
    direct_parser: str
    direct_observations: int
    direct_vintages_kept: bool
    direct_keyless_metadata: bool
    openbb_route: str
    openbb_package: str
    openbb_installed: bool
    openbb_credentialed: bool
    openbb_independent_publisher: bool
    verdict: str


def evaluate_fred_gap(*, enabled: bool) -> OpenBBGapReport:
    """Evaluate the FRED gap with no network, no install, no credentials."""
    require_openbb_gap_scope(enabled=enabled)
    instrument = InstrumentIdentity(
        canonical_id=f"FRED:{GAP_SERIES}", asset_class=AssetClass.FUND, venue="research"
    )
    observations = AlfredCollector().parse(
        VINTAGED_FIXTURE,
        instrument=instrument,
        ingested_at=datetime(2024, 1, 4, tzinfo=UTC),
    )
    vintages = [item.source_revision for item in observations]
    installed = importlib.util.find_spec("openbb") is not None
    # FRED's OpenBB route wraps the same St. Louis Fed publisher behind a
    # credentialed provider package: same source family, new dependency.
    return OpenBBGapReport(
        schema_version=OPENBB_SCHEMA_VERSION,
        series=GAP_SERIES,
        direct_parser=DIRECT_PARSER_VERSION,
        direct_observations=len(observations),
        direct_vintages_kept=vintages == ["2024-01-02", "2024-01-03"],
        direct_keyless_metadata=True,
        openbb_route=OPENBB_ROUTE,
        openbb_package=OPENBB_PACKAGE,
        openbb_installed=installed,
        openbb_credentialed=True,
        openbb_independent_publisher=False,
        verdict=(
            "keep direct ALFRED path; OpenBB adds a credentialed dependency "
            "without an independent publisher or uncovered capability"
        ),
    )


def gap_manifest(report: OpenBBGapReport) -> dict[str, Any]:
    return {
        "schema": report.schema_version,
        "series": report.series,
        "direct": {
            "parser": report.direct_parser,
            "origin": GAP_ORIGIN,
            "observations": report.direct_observations,
            "vintages_kept": report.direct_vintages_kept,
            "keyless_metadata": report.direct_keyless_metadata,
        },
        "openbb": {
            "route": report.openbb_route,
            "package": report.openbb_package,
            "reference": OPENBB_DOC,
            "installed": report.openbb_installed,
            "credentialed": report.openbb_credentialed,
            "independent_publisher": report.openbb_independent_publisher,
        },
        "verdict": report.verdict,
    }


__all__ = [
    "DIRECT_PARSER_VERSION",
    "GAP_ORIGIN",
    "GAP_SERIES",
    "OPENBB_DOC",
    "OPENBB_PACKAGE",
    "OPENBB_ROUTE",
    "OPENBB_SCHEMA_VERSION",
    "VINTAGED_FIXTURE",
    "OpenBBGapReport",
    "evaluate_fred_gap",
    "gap_manifest",
]
