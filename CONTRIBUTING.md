# Contributing to AdvisorAI V3

Thanks for taking the time to improve the project. Contributions should preserve the repository's typed contracts, point-in-time guarantees, deterministic risk boundary, and explicit paper/live separation.

## Before you start

Read:

- [Architecture](docs/concepts/architecture.md)
- [Components reference](docs/reference/components.md)
- [Project status](docs/concepts/status.md)
- [Development setup](docs/development/setup.md)

If a change crosses a phase gate, provider credential boundary, order authority boundary, or service ownership boundary, inspect the matching decision/runbook before coding.

## Local setup

```bash
uv sync --group dev
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q
uv run ruff check .
```

For dashboard work:

```bash
uv sync --extra dashboard
npm ci --prefix dashboard
npm run build --prefix dashboard
```

Use ignored `artifacts/` paths for evidence and temporary state. Do not commit credentials, generated databases, provider responses containing secrets, model weights, or screenshots containing private state.

## Where code belongs

- Domain artifacts and cross-boundary types belong in `src/advisorai/contracts`.
- Replaceable infrastructure belongs behind `src/advisorai/ports.py`.
- Source acquisition and provenance belong in `collectors`; immutable persistence belongs in `lake`.
- Mission/evidence proposal logic belongs in `agents`/`api`; order authority belongs in `execution`/`runtime`.
- Gate and live-state changes belong in `gates`/`live` with an accompanying plan/runbook update.
- Dashboard presentation belongs in `dashboard` and its typed API projection; it must not become a second risk or ledger authority.

See [Extending AdvisorAI](docs/development/extending.md) for concrete port and collector patterns.

## Tests and checks

Run the relevant targeted tests while iterating, then the full checks before opening a pull request:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q tests/<owning-area>
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -q
uv run ruff check .
npm run build --prefix dashboard
```

If a test needs a network, provider credential, optional runtime, or external timed evidence, keep it opt-in and document the required runbook. Prefer deterministic injected transports and fixtures in the default suite.

## Documentation changes

Use implementation behavior as the source of truth. Mark target architecture, experimental adapters, and external gates explicitly. Verify commands, file paths, configuration names, and internal links. Keep diagrams in Mermaid or repository-owned assets so they can be maintained with the code.

## Pull requests

Please include:

- a concise problem statement and scope;
- the architectural boundary affected;
- tests/checks run and any opt-in checks not run;
- documentation updates for new behavior or interfaces;
- explicit notes for behavior, configuration, schema, or gate changes.

Keep unrelated refactors out of focused changes. Do not represent a passing local test, installed dependency, or synthetic dashboard fixture as live/provider readiness.

## License and governance

AdvisorAI's original source is proprietary and All Rights Reserved under [`LICENSE`](LICENSE). Third-party software, models, datasets, and provider data retain their own licenses and terms; do not add a copied or vendored component without recording its provenance in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and the [compliance inventory](configs/compliance/third-party-licenses.yaml).

Before submitting a contribution, contributors must have the right to submit the work and must identify copied, adapted, generated, or externally licensed material. This repository does not currently make a blanket inbound contribution or CLA promise; the maintainer must approve contribution terms before accepting material intended for redistribution. Do not add a license notice that purports to relicense third-party code.

Run the offline compliance check when changing licensing, dependencies, models, data, or distribution metadata:

```bash
uv run python scripts/check_license_policy.py
```
