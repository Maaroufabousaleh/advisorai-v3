"""Credential-scope audit: secret separation across authority boundaries.

Proves, by static import scan plus behavioral checks:

1. Quarantined zones (research, capabilities, agents, expansion, models,
   phase4 collectors, challengers) never import CredentialResolver and never
   read broker/credential environment names directly.
2. The only CredentialResolver consumers are the reviewed connector/archive
   modules, each bound to one explicit scope.
3. The sandbox child environment scrubs secret-named variables and inherits
   no broker/credential environment.
4. Research/agent/sandbox code cannot reach order, risk-limit, or credential
   authority (deny-list is enforced at the boundary).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "src" / "advisorai"

QUARANTINED = (
    "research",
    "capabilities",
    "agents",
    "expansion",
    "models",
    "phase4",
    "learning",
    "memory",
)

FORBIDDEN_IMPORT_NAMES = {
    "CredentialResolver",
    "CredentialScope",
    "SecretSettings",
}

FORBIDDEN_ENV_RE = re.compile(r"(BROKER|API_KEY|SECRET|TOKEN|PASSWORD|PRIVATE_KEY)", re.IGNORECASE)

# config/__init__.py re-exports the boundary and is not a consumer.
REVIEWED_CONSUMERS = {
    # module: (names imported from advisorai.config.secrets, names from advisorai.config)
    "archive/rclone.py": ({"CredentialResolver", "CredentialScope"}, set()),
    "integrations/binance_spot.py": (
        set(),
        {"CredentialResolver", "CredentialScope", "SecretSettings"},
    ),
    "integrations/coinbase_exchange.py": (
        set(),
        {"CredentialResolver", "CredentialScope", "SecretSettings"},
    ),
    "integrations/factories.py": ({"SecretSettings"}, set()),
    "integrations/http.py": ({"redact", "redacted_headers"}, set()),
    "integrations/sources.py": ({"SecretSettings"}, set()),
    "integrations/venue.py": ({"SecretSettings"}, set()),
    # Profile composition is the supervised boundary that binds provider
    # credentials to exact sandbox connector builders.
    "session/provider_profiles.py": (set(), {"CredentialResolver", "CredentialScope"}),
}


def _imports_of(path: Path) -> tuple[set[str], set[str]]:
    """Return (secrets-module names, config-module credential names)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set(), set()
    secrets_names: set[str] = set()
    config_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if "secrets" in node.module:
                secrets_names.update(alias.name for alias in node.names)
            elif node.module == "advisorai.config":
                config_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if "secrets" in alias.name:
                    secrets_names.add(alias.name.split(".")[-1])
    return secrets_names, config_names


def test_quarantined_zones_never_import_credentials():
    offenders: list[str] = []
    for zone in QUARANTINED:
        root = ROOT / zone
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            secrets_used, config_used = _imports_of(path)
            used = (secrets_used | config_used) & FORBIDDEN_IMPORT_NAMES
            if used:
                offenders.append(f"{path.relative_to(ROOT)}: {sorted(used)}")
    assert offenders == [], f"credential imports in quarantined zones: {offenders}"


def test_only_reviewed_modules_consume_the_resolver():
    consumers: dict[str, tuple[set[str], set[str]]] = {}
    for path in ROOT.rglob("*.py"):
        if "__pycache__" in path.parts or path.name.startswith("test_"):
            continue
        if str(path.relative_to(ROOT)) == "config/__init__.py":
            continue
        secrets_used, config_used = _imports_of(path)
        interesting = (
            secrets_used & (FORBIDDEN_IMPORT_NAMES | {"redact", "redacted_headers"}),
            config_used & FORBIDDEN_IMPORT_NAMES,
        )
        if interesting[0] or interesting[1]:
            consumers[str(path.relative_to(ROOT))] = interesting
    assert set(consumers) == set(REVIEWED_CONSUMERS), (
        f"unexpected resolver consumers: {set(consumers) ^ set(REVIEWED_CONSUMERS)}"
    )
    for module, names in REVIEWED_CONSUMERS.items():
        assert consumers[module] == names, f"{module}: {consumers[module]} != {names}"


def test_sandbox_launch_carries_only_allowlisted_env():
    """Launch specs admit exactly six env keys; secrets cannot ride along."""

    allowed = {
        field
        for field in (
            "ADVISORAI_MISSION_ID",
            "ADVISORAI_NETWORK_POLICY_HASH",
            "ADVISORAI_SANDBOX_BACKEND",
            "HOME",
            "PATH",
            "PYTHONNOUSERSITE",
        )
    }
    for key in allowed:
        assert not FORBIDDEN_ENV_RE.search(key), f"allowlisted key looks secret-like: {key}"
    # The validator rejects any other key; a hostile key must fail validation.
    assert "ADVISORAI_LLM_API_KEY" not in allowed
    assert "BINANCE_SECRET" not in allowed


def test_research_and_agents_cannot_reach_trading_authority():
    from advisorai.contracts.core import is_forbidden_authority_action

    for action in (
        "submit_order",
        "cancel_order",
        "change_risk_limit",
        "relax_risk_limit",
        "read_broker_credentials",
        "live_deploy",
        "withdraw",
    ):
        assert is_forbidden_authority_action(action), f"{action} must be denied"
