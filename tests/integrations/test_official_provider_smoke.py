"""Offline contracts for the read-only BLS/BEA/EIA smoke script.

All credentials are synthetic fixtures for the injected transports. No
network call and no real ``secrets.env`` value is used here.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest
from smoke_official_providers import smoke_bea, smoke_bls, smoke_eia

from advisorai.config import CredentialResolver, CredentialScope


def _resolver(**values: str) -> CredentialResolver:
    return CredentialResolver.from_mapping(dict(values))


def _bls_requester(expected_key: str | None):
    seen: dict[str, object] = {}

    def requester(method, url, headers, body, timeout):
        seen.update(method=method, url=url, body=json.loads(body))
        if expected_key:
            assert seen["body"]["registrationkey"] == expected_key
        else:
            assert "registrationkey" not in seen["body"]
        assert "?" not in seen["url"], "BLS key must never appear in a URL"
        payload = {
            "status": "REQUEST_SUCCEEDED",
            "Results": {
                "series": [
                    {
                        "seriesID": "CUUR0000SA0",
                        "data": [
                            {
                                "year": "2024",
                                "period": "M01",
                                "periodName": "January",
                                "value": "300.1",
                                "footnotes": [],
                            }
                        ],
                    }
                ]
            },
        }
        return 200, json.dumps(payload).encode(), ()

    requester.seen = seen  # type: ignore[attr-defined]
    return requester


def _bea_requester(expected_key: str):
    seen: dict[str, object] = {}

    def requester(method, url, headers, body, timeout):
        seen.update(method=method, url=url)
        assert expected_key in seen["url"], "BEA UserID travels as the documented query param"
        assert body is None
        payload = {
            "BEAAPI": {
                "Results": {
                    "Data": [
                        {
                            "DataValue": "1.0",
                            "TimePeriod": "2023Q4",
                            "TableName": "T20100",
                            "UNIT_MULT": "Level",
                        }
                    ]
                }
            }
        }
        return 200, json.dumps(payload).encode(), ()

    requester.seen = seen  # type: ignore[attr-defined]
    return requester


def _eia_requester(expected_key: str):
    seen: dict[str, object] = {}

    def requester(method, url, headers, body, timeout):
        seen.update(method=method, url=url)
        assert expected_key in seen["url"]
        assert body is None
        payload = {
            "request": {"params": {"api_key": expected_key}},
            "response": {"data": [{"period": "2023", "price": 12.3}]},
        }
        return 200, json.dumps(payload).encode(), ()

    requester.seen = seen  # type: ignore[attr-defined]
    return requester


def test_bls_smoke_delivers_key_only_to_its_own_scope_in_post_body():
    resolver = _resolver(BLS_API_KEY="synthetic-bls")
    requester = _bls_requester("synthetic-bls")
    meta, outcome = smoke_bls(resolver=resolver, requester=requester)
    assert meta == {"credential_refs": ("BLS_API_KEY",), "mode": "keyed"}
    assert (outcome.provider, outcome.status_code, outcome.row_count) == ("bls", 200, 1)
    with pytest.raises(ValueError, match="not allowlisted"):
        resolver.get(CredentialScope.BLS_PUBLIC_DATA, "BEA_API_KEY")


def test_bls_smoke_labels_public_mode_and_never_silently_falls_back():
    resolver = _resolver()
    requester = _bls_requester(None)
    meta, outcome = smoke_bls(resolver=resolver, requester=requester)
    assert meta["mode"] == "public"
    assert meta["credential_refs"] == ()
    assert outcome.row_count == 1


def test_bea_smoke_requires_its_key_and_scrubs_errors():
    resolver = _resolver(BEA_API_KEY="synthetic-bea")
    meta, outcome = smoke_bea(
        resolver=resolver, requester=_bea_requester("synthetic-bea")
    )
    assert meta == {"credential_refs": ("BEA_API_KEY",)}
    assert (outcome.provider, outcome.status_code, outcome.row_count) == ("bea", 200, 1)
    with pytest.raises(RuntimeError, match="unavailable"):
        smoke_bea(resolver=_resolver())

    def failing(*args):
        raise OSError("failure carrying synthetic-bea in the URL")

    with pytest.raises(Exception) as error:
        smoke_bea(resolver=resolver, requester=failing)
    assert "synthetic-bea" not in str(error.value)


def test_eia_smoke_resolves_local_file_scope_and_discards_echo():
    resolver = _resolver(EIA_API_KEY="synthetic-eia")
    requester = _eia_requester("synthetic-eia")
    meta, outcome = smoke_eia(resolver=resolver, requester=requester)
    assert meta == {"credential_refs": ("EIA_API_KEY",)}
    assert (outcome.provider, outcome.status_code, outcome.row_count) == ("eia", 200, 1)
    assert "synthetic-eia" not in repr(outcome)
    with pytest.raises(RuntimeError, match="unavailable"):
        smoke_eia(resolver=_resolver())
