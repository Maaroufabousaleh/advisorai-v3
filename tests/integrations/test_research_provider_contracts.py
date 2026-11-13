"""Offline contracts for read-only public data integrations."""

import json

import pytest

from advisorai.config.secrets import (
    CredentialScope,
    InfisicalCredentialResolver,
)
from advisorai.integrations.public_data import (
    BeaDataClient,
    BlsV2Client,
    EiaApiClient,
    ProviderRequestError,
)
from advisorai.integrations.research_metadata import (
    CrossrefWorksClient,
    OpenAlexWorksClient,
)


def test_infisical_resolver_reads_only_exact_provider_name():
    calls = []

    def reader(name, project, environment, path):
        calls.append((name, project, environment, path))
        return "synthetic-eia-key"

    resolver = InfisicalCredentialResolver(
        reader, project_id="synthetic-project", environment="dev"
    )
    assert resolver.get(CredentialScope.EIA_PUBLIC_DATA, "EIA_API_KEY") == "synthetic-eia-key"
    assert calls == [("EIA_API_KEY", "synthetic-project", "dev", "/")]
    with pytest.raises(ValueError):
        resolver.get(CredentialScope.EIA_PUBLIC_DATA, "BEA_API_KEY")
    with pytest.raises(ValueError):
        InfisicalCredentialResolver(reader, project_id="x", environment="prod")


def test_eia_smoke_discards_echo_and_returns_only_sanitized_metadata():
    dummy = "synthetic-eia-key"
    seen = {}

    def requester(method, url, headers, body, timeout):
        seen["url"] = url
        return 200, json.dumps({"request": {"params": {"api_key": dummy}}, "response": {"data": [{"period": "2023", "price": 12.3}]}}).encode(), ()

    result = EiaApiClient(requester=requester).smoke_test(dummy)
    assert result.status_code == 200 and result.row_count == 1
    assert dummy not in repr(result)
    assert dummy in seen["url"]  # Confirms the dummy credential reached only the injected transport.


def test_eia_errors_do_not_disclose_credential_or_url():
    dummy = "synthetic-eia-key"

    def requester(*args):
        raise OSError(f"failure URL contained {dummy}")

    with pytest.raises(ProviderRequestError) as error:
        EiaApiClient(requester=requester).smoke_test(dummy)
    assert dummy not in str(error.value)
    assert "api_key=" not in str(error.value)


def test_bls_v2_uses_post_body_and_maps_contract_rows():
    dummy = "synthetic-bls-key"
    seen = {}

    def requester(method, url, headers, body, timeout):
        seen.update(method=method, url=url, body=json.loads(body))
        response = {"status": "REQUEST_SUCCEEDED", "Results": {"series": [{"seriesID": "CUUR0000SA0", "data": [{"year": "2023", "period": "M01", "periodName": "January", "value": "300.2", "footnotes": []}]}]}}
        return 200, json.dumps(response).encode(), ()

    rows = BlsV2Client(requester=requester).fetch_series(
        "CUUR0000SA0", start_year=2023, end_year=2023, registration_key=dummy
    )
    assert seen["method"] == "POST" and "?" not in seen["url"]
    assert seen["body"]["registrationkey"] == dummy
    assert len(rows) == 1 and rows[0].value


def test_bea_client_rejects_reserved_overrides_and_scrubs_errors():
    with pytest.raises(ValueError):
        BeaDataClient().fetch_data("NIPA", series_id="X", parameters={"UserID": "bad"}, user_id="synthetic")

    def requester(*args):
        return 200, json.dumps({"BEAAPI": {"Results": {"Error": {"UserID": "synthetic-bea-key"}}}}).encode(), ()

    with pytest.raises(ProviderRequestError) as error:
        BeaDataClient(requester=requester).fetch_data(
            "NIPA", series_id="X", parameters={}, user_id="synthetic-bea-key"
        )
    assert "synthetic-bea-key" not in str(error.value)


def test_openalex_and_crossref_are_metadata_only_citation_cards():
    openalex = OpenAlexWorksClient(requester=lambda _url: {"results": [{
        "id": "https://openalex.org/W123", "title": "Macro evidence",
        "publication_year": 2024, "authorships": [{"author": {"display_name": "A Researcher"}}],
        "abstract_inverted_index": {"should": "not persist"},
    }]})
    crossref = CrossrefWorksClient(requester=lambda _url: {"message": {"items": [{
        "DOI": "10.5555/example", "title": ["Market study"],
        "author": [{"family": "Researcher"}], "published-print": {"date-parts": [[2022]]},
        "abstract": "should not persist",
    }]}})
    a = openalex.search("macro policy").cards[0]
    c = crossref.search("market structure").cards[0]
    assert a.source_url == "https://openalex.org/W123" and a.authors == ("A Researcher",)
    assert c.source_url == "https://doi.org/10.5555/example" and c.authors == ("Researcher",)
    assert "abstract" not in a.model_dump_json().lower()
    assert "abstract" not in c.model_dump_json().lower()

