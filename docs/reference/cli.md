# CLI and scripts reference

The package does not declare a `project.scripts` console entry point. Human-facing workflows are exposed as Python modules, shell scripts, or evidence/qualification scripts.

## Local dashboard

```bash
./scripts/launch_dashboard.sh
./scripts/launch_dashboard.sh --protected
```

The first form is local development mode and uses the synthetic dashboard path. The protected form requires the dashboard password/TOTP environment to be configured. See [Operator console](../guides/operator-console.md).

## Service registry inspection

```bash
uv run python -m advisorai.services
```

This prints the dependency-first order from `ServiceRegistry`. It is an ownership/dependency manifest, not a supervisor and does not start processes.

## Acceptance suites

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run python scripts/verify_acceptance.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run python scripts/verify_acceptance.py --phase 2
```

The optional `--phase` value is an integer from `0` through `10`. The runner starts a separate pytest process per selected phase and stops on the first failure. It verifies executable local evidence only; timed Phase 0/7 evidence and human Phase 10 approval remain separate records.

## Evidence and qualification scripts

These scripts write to ignored output roots and are intentionally explicit about their scope:

| Command | Purpose |
| --- | --- |
| `uv run python scripts/run_phase1_local_rebuild.py --output artifacts/phase1/local-rebuild` | Credential-free local config-bundle rollback and Bronze rebuild drill |
| `uv run python scripts/run_phase0_component_bakeoff.py --output artifacts/phase0/component-bakeoff` | Bounded component availability/fixture evidence; no provider or venue admission |
| `uv run python scripts/run_phase8_capability_evidence.py --output artifacts/phase8/capability` | Credential-free Hermes/RSS fixture and capability lifecycle evidence |
| `uv run python scripts/probe_phase8_os_sandbox.py --evidence-dir artifacts/phase8/sandbox` | Bounded OS sandbox probe; availability is not production admission |
| `uv run python scripts/check_transition_config.py --secrets secrets.env` | Parse and validate transition identities/endpoints without making a request |

The exact flags for a particular qualification script are discoverable with:

```bash
uv run python scripts/<script-name>.py --help
```

Do not run real-provider, venue, archive, or remote-model scripts casually. Read the matching runbook, review the endpoint/credential scope, and use an explicit ignored output root. A network-capable script can still be a qualification probe rather than an authorization to trade.

## Module entry points

The dashboard API is started by the launcher or directly with:

```bash
uv run --extra dashboard python -m advisorai.api.dashboard_server
```

The module binds to `127.0.0.1:8787` in the current implementation. Prefer the launcher when the React console is also needed.
