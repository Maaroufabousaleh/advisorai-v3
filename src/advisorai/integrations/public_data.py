"""Read-only official data API clients with credential-safe request handling.

Credentialed official APIs are deliberately kept out of ``SafeHttpClient``'s
raw-response/spool contract. EIA and BEA place credentials in query parameters,
and BLS accepts one in the JSON body. This module never returns or persists the
request URL, credential-bearing request body, raw response, or provider error
body. The EIA smoke path is intended for an isolated trusted runtime only.
"""

from __future__ import annotations

import json
import re
import ssl
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from advisorai.contracts import (
    AssetClass,
    InstrumentIdentity,
    PointInTimeObservation,
    SourceGrade,
)

EIA_BASE_URL = "https://api.eia.gov"
EIA_HOST = "api.eia.gov"
BLS_V2_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BLS_HOST = "api.bls.gov"
BEA_DATA_URL = "https://apps.bea.gov/api/data/"
BEA_HOST = "apps.bea.gov"

Requester = Callable[
    [str, str, Mapping[str, str], bytes | None, float],
    tuple[int, bytes, Sequence[tuple[str, str]]],
]


class ProviderRequestError(RuntimeError):
    """Sanitized provider error; it never retains a URL or response body."""

    def __init__(self, provider: str, reason: str, status_code: int | None = None) -> None:
        self.provider = provider
        self.reason = reason
        self.status_code = status_code
        suffix = f" HTTP {status_code}" if status_code is not None else ""
        super().__init__(f"{provider} read request failed: {reason}{suffix}")


@dataclass(frozen=True, slots=True)
class SmokeTestOutcome:
    provider: str
    status_code: int
    row_count: int
    retrieved_at: datetime

    def __post_init__(self) -> None:
        if self.provider != "eia" or self.status_code != 200 or self.row_count < 1:
            raise ValueError("smoke-test outcome must contain one successful EIA row")
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("smoke-test timestamp must include a timezone")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _urllib_requester(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
) -> tuple[int, bytes, Sequence[tuple[str, str]]]:
    """Minimal non-logging transport. Redirects are disabled to avoid key forwarding."""

    request = Request(url=url, data=body, method=method, headers=dict(headers))
    opener = build_opener(
        _NoRedirect,
        HTTPSHandler(context=ssl.create_default_context()),
    )
    try:
        try:
            with opener.open(request, timeout=timeout) as response:
                return int(response.status), response.read(), tuple(response.headers.items())
        except HTTPError as error:
            # HTTPError also represents non-2xx responses. The caller sanitizes
            # its body and never exposes the error's URL or message.
            return int(error.code), error.read(), tuple(error.headers.items()) if error.headers else ()
        except Exception:
            # Suppress transport exceptions here: many retain the full URL in
            # their args. The caller receives only an integer sentinel.
            return -1, b"", ()
    finally:
        request = None
        opener = None
        url = ""
        body = None
        headers = {}


def _contains_secret_key(key: object) -> bool:
    return bool(
        re.search(
            r"(?:api[_-]?key|userid|registrationkey|authorization|token|secret)",
            str(key),
            flags=re.IGNORECASE,
        )
    )


def _scrub_payload(value: object, secrets: tuple[str, ...]) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]"
            if _contains_secret_key(key)
            else _scrub_payload(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub_payload(item, secrets) for item in value]
    if isinstance(value, str):
        result = value
        for secret in secrets:
            result = result.replace(secret, "[REDACTED]")
            result = result.replace(quote(secret, safe=""), "[REDACTED]")
        return result
    return value


def _safe_json_request(
    *,
    provider: str,
    endpoint: str,
    allowed_host: str,
    method: str,
    params: Mapping[str, object] | None = None,
    payload: Mapping[str, object] | None = None,
    secret_parameter: str | None = None,
    secret_value: str | None = None,
    requester: Requester | None = None,
    timeout_seconds: float = 12.0,
    user_agent: str = "advisorai-v3/read-only-provider",
) -> tuple[Mapping[str, object], int, datetime]:
    """Issue one JSON request and return only scrubbed structured data.

    Do not add request/response logging, tracing hooks, raw-response spools, or
    exception chaining here. A trusted runtime must also run this path without
    auto-instrumentation that records URL or request-body locals.
    """

    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or parsed.hostname != allowed_host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ProviderRequestError(provider, "endpoint policy rejected")
    if secret_parameter is not None and not secret_value:
        raise ProviderRequestError(provider, "required credential is unavailable")

    query = dict(params or {})
    if secret_parameter is not None:
        if secret_parameter in query:
            raise ProviderRequestError(provider, "credential parameter is reserved")
        query[secret_parameter] = secret_value or ""
    url = endpoint
    if query:
        url = f"{endpoint}?{urlencode(query, doseq=True)}"
    request_body: bytes | None = None
    headers = {"Accept": "application/json", "User-Agent": user_agent}
    if payload is not None:
        request_body = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        headers["Content-Type"] = "application/json"

    network_failed = False
    status_code = 0
    raw_body = b""
    try:
        status_code, raw_body, _headers = (requester or _urllib_requester)(
            method.upper(), url, headers, request_body, timeout_seconds
        )
    except Exception:
        # Do not retain the exception object: urllib errors can include the
        # credential-bearing URL. Raise only after leaving the exception block.
        network_failed = True
    if status_code < 0:
        network_failed = True
    # Clear credential-bearing locals before any branch can raise. This also
    # reduces exposure if an outer crash collector inspects this frame.
    url = ""
    request_body = None
    query.clear()
    if payload is not None:
        payload = None
    secret_value = None
    if network_failed:
        raw_body = b""
        secrets = ()
        raise ProviderRequestError(provider, "network failure") from None
    if status_code != 200:
        raw_body = b""
        raise ProviderRequestError(provider, "HTTP response rejected", status_code)

    secrets = (secret_value,) if secret_value else ()
    safe_body = raw_body
    for secret in secrets:
        safe_body = safe_body.replace(secret.encode("utf-8"), b"[REDACTED]")
        safe_body = safe_body.replace(quote(secret, safe="").encode("ascii"), b"[REDACTED]")
    parse_failed = False
    parsed_body: object = None
    try:
        parsed_body = json.loads(safe_body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        parse_failed = True
    raw_body = b""
    safe_body = b""
    if parse_failed or not isinstance(parsed_body, Mapping):
        raise ProviderRequestError(provider, "invalid JSON response") from None
    scrubbed = _scrub_payload(parsed_body, secrets)
    if not isinstance(scrubbed, Mapping):
        raise ProviderRequestError(provider, "invalid JSON response")
    return scrubbed, status_code, datetime.now(UTC)


def _instrument(provider: str, series_id: str) -> InstrumentIdentity:
    return InstrumentIdentity(
        canonical_id=f"macro:{provider}:{series_id}",
        asset_class=AssetClass.OTHER,
        venue=provider,
        venue_symbol=series_id,
    )


def _period_start(value: object) -> datetime | None:
    """Convert a published period label to its start; unknown labels stay unknown."""

    text = str(value).strip()
    match = re.fullmatch(r"(\d{4})-?(\d{2})", text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            return datetime(year, month, 1, tzinfo=UTC)
    match = re.fullmatch(r"(\d{4})Q([1-4])", text, re.IGNORECASE)
    if match:
        year, quarter = int(match.group(1)), int(match.group(2))
        return datetime(year, 1 + (quarter - 1) * 3, 1, tzinfo=UTC)
    match = re.fullmatch(r"(\d{4})M(\d{2})", text, re.IGNORECASE)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            return datetime(year, month, 1, tzinfo=UTC)
    match = re.fullmatch(r"\d{4}", text)
    if match:
        return datetime(int(text), 1, 1, tzinfo=UTC)
    return None


def _observation(
    *,
    provider: str,
    series_id: str,
    record: Mapping[str, object],
    period: object,
    retrieved_at: datetime,
    source_family: str,
) -> PointInTimeObservation:
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    period_start = _period_start(period)
    return PointInTimeObservation(
        instrument=_instrument(provider, series_id),
        entity_id=series_id,
        event_time=period_start,
        effective_time=period_start,
        source_published_at=None,
        first_available_at=retrieved_at,
        ingested_at=retrieved_at,
        source_revision=(str(record.get("vintage")) if record.get("vintage") else None),
        raw_artifact_hash=sha256(encoded.encode("utf-8")).hexdigest(),
        parser_version=f"{provider}-read-v1",
        source_family=source_family,
        origin=provider,
        quality_grade=SourceGrade.RESEARCH,
        intended_use="point_in_time_research_context",
        value=encoded,
    )


class BlsV2Client:
    """Read-only BLS v2 series client; API key is optional and stays in POST body."""

    def __init__(self, *, requester: Requester | None = None) -> None:
        self._requester = requester

    def fetch_series(
        self,
        series_id: str,
        *,
        start_year: int,
        end_year: int,
        registration_key: str | None = None,
    ) -> tuple[PointInTimeObservation, ...]:
        if not re.fullmatch(r"[A-Z0-9]{4,30}", series_id):
            raise ValueError("BLS series ID is malformed")
        if start_year < 1900 or end_year < start_year or end_year > datetime.now(UTC).year + 1:
            raise ValueError("BLS year range is invalid")
        payload: dict[str, object] = {
            "seriesid": [series_id],
            "startyear": str(start_year),
            "endyear": str(end_year),
        }
        if registration_key:
            payload["registrationkey"] = registration_key
        try:
            response, _status, retrieved_at = _safe_json_request(
                provider="bls",
                endpoint=BLS_V2_URL,
                allowed_host=BLS_HOST,
                method="POST",
                payload=payload,
                secret_value=registration_key,
                requester=self._requester,
            )
        finally:
            payload.pop("registrationkey", None)
            registration_key = ""
        if response.get("status") != "REQUEST_SUCCEEDED":
            raise ProviderRequestError("bls", "provider status rejected")
        results = response.get("Results", response.get("results"))
        series = results.get("series") if isinstance(results, Mapping) else None
        if not isinstance(series, list):
            raise ProviderRequestError("bls", "response schema rejected")
        selected = next(
            (item for item in series if isinstance(item, Mapping) and item.get("seriesID") == series_id),
            None,
        )
        if not isinstance(selected, Mapping) or not isinstance(selected.get("data"), list):
            raise ProviderRequestError("bls", "response schema rejected")
        output: list[PointInTimeObservation] = []
        for item in selected["data"]:
            if not isinstance(item, Mapping) or "value" not in item:
                continue
            record = {
                "series_id": series_id,
                "year": item.get("year"),
                "period": item.get("period"),
                "period_name": item.get("periodName"),
                "value": item.get("value"),
                "footnotes": item.get("footnotes", []),
            }
            period = f"{item.get('year', '')}{item.get('period', '')}"
            output.append(
                _observation(
                    provider="bls",
                    series_id=series_id,
                    record=record,
                    period=period,
                    retrieved_at=retrieved_at,
                    source_family="official_bls_macro",
                )
            )
        return tuple(output)


class BeaDataClient:
    """Read-only BEA Data API client; never reuses the previously exposed key."""

    def __init__(self, *, requester: Requester | None = None) -> None:
        self._requester = requester

    def fetch_data(
        self,
        dataset: str,
        *,
        series_id: str,
        parameters: Mapping[str, object],
        user_id: str,
    ) -> tuple[PointInTimeObservation, ...]:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,39}", dataset):
            raise ValueError("BEA dataset name is malformed")
        if not series_id.strip() or not user_id:
            raise ValueError("BEA series ID and replacement UserID are required")
        reserved = {"userid", "method", "datasetname", "resultformat"}
        if any(key.lower() in reserved for key in parameters):
            raise ValueError("BEA request parameters cannot override reserved fields")
        query: dict[str, object] = {
            "method": "GetData",
            "datasetname": dataset,
            "ResultFormat": "JSON",
            **parameters,
        }
        try:
            response, _status, retrieved_at = _safe_json_request(
                provider="bea",
                endpoint=BEA_DATA_URL,
                allowed_host=BEA_HOST,
                method="GET",
                params=query,
                secret_parameter="UserID",
                secret_value=user_id,
                requester=self._requester,
            )
        finally:
            query.pop("UserID", None)
            user_id = ""
        bea = response.get("BEAAPI")
        results = bea.get("Results") if isinstance(bea, Mapping) else None
        if not isinstance(results, Mapping) or results.get("Error"):
            raise ProviderRequestError("bea", "provider response rejected")
        records = results.get("Data")
        if not isinstance(records, list):
            raise ProviderRequestError("bea", "response schema rejected")
        output: list[PointInTimeObservation] = []
        for item in records:
            if not isinstance(item, Mapping) or "DataValue" not in item:
                continue
            record = {
                "series_id": series_id,
                "time_period": item.get("TimePeriod"),
                "value": item.get("DataValue"),
                "line_number": item.get("LineNumber"),
                "table_name": item.get("TableName"),
                "unit": item.get("Unit"),
                "unit_multiplier": item.get("UnitMult"),
            }
            output.append(
                _observation(
                    provider="bea",
                    series_id=series_id,
                    record=record,
                    period=item.get("TimePeriod"),
                    retrieved_at=retrieved_at,
                    source_family="official_bea_macro",
                )
            )
        return tuple(output)


class EiaApiClient:
    """EIA v2 client for a trusted runtime; raw response and URLs never escape."""

    def __init__(self, *, requester: Requester | None = None) -> None:
        self._requester = requester

    def query(
        self,
        path: str,
        *,
        parameters: Mapping[str, object],
        api_key: str,
    ) -> tuple[Mapping[str, object], int, datetime]:
        if not re.fullmatch(r"/v2/[A-Za-z0-9/_-]+/data/?", path):
            raise ValueError("EIA path must be a reviewed v2 data endpoint")
        if any(key.lower() == "api_key" for key in parameters):
            raise ValueError("EIA api_key is supplied only by the credential argument")
        try:
            response, status, fetched_at = _safe_json_request(
                provider="eia",
                endpoint=f"{EIA_BASE_URL}{path}",
                allowed_host=EIA_HOST,
                method="GET",
                params=parameters,
                secret_parameter="api_key",
                secret_value=api_key,
                requester=self._requester,
            )
        finally:
            api_key = ""
        data = response.get("response")
        if not isinstance(data, Mapping) or not isinstance(data.get("data"), list):
            raise ProviderRequestError("eia", "response schema rejected")
        # EIA echoes request metadata separately. It is intentionally discarded.
        return data, status, fetched_at

    def smoke_test(self, api_key: str) -> SmokeTestOutcome:
        try:
            data, status, retrieved_at = self.query(
                "/v2/electricity/retail-sales/data/",
                parameters={
                    "frequency": "annual",
                    "data[0]": "price",
                    "facets[sectorid][]": "RES",
                    "facets[stateid][]": "CO",
                    "start": "2023",
                    "end": "2023",
                    "sort[0][column]": "period",
                    "sort[0][direction]": "desc",
                    "offset": "0",
                    "length": "1",
                },
                api_key=api_key,
            )
        finally:
            api_key = ""
        rows = data.get("data")
        if not isinstance(rows, list) or not rows:
            raise ProviderRequestError("eia", "smoke-test returned no rows")
        return SmokeTestOutcome("eia", status, 1, retrieved_at)


__all__ = [
    "BEA_DATA_URL",
    "BLS_V2_URL",
    "EIA_BASE_URL",
    "BeaDataClient",
    "BlsV2Client",
    "EiaApiClient",
    "ProviderRequestError",
    "SmokeTestOutcome",
]
