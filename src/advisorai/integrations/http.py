"""Small HTTPS client with explicit host, retry, rate, and circuit guards."""

from __future__ import annotations

import json
import ssl
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from email.message import Message
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, field_validator

from advisorai.collectors.sources import HttpResponse, HttpTransport
from advisorai.config.secrets import redact, redacted_headers


class HttpTransportError(RuntimeError):
    """A safe, redacted external transport failure."""

    def __init__(
        self, message: str, *, status_code: int | None = None, retriable: bool = False
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retriable = retriable


class HttpClientConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_hosts: tuple[str, ...] = Field(min_length=1)
    timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.25, ge=0, le=30)
    requests_per_second: float = Field(default=5.0, gt=0, le=100)
    circuit_failure_threshold: int = Field(default=5, ge=1, le=100)
    circuit_reset_seconds: float = Field(default=60.0, gt=0, le=86_400)
    user_agent: str = Field(default="advisorai-v3/transition", min_length=1, max_length=200)

    @field_validator("allowed_hosts")
    @classmethod
    def normalize_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        hosts = tuple(item.strip().lower().rstrip(".") for item in value if item.strip())
        if not hosts:
            raise ValueError("at least one reviewed HTTPS host is required")
        if any("/" in item or ":" in item for item in hosts):
            raise ValueError("allowed_hosts must contain hostnames, not URLs or ports")
        return tuple(dict.fromkeys(hosts))


Requester = Callable[
    [str, str, Mapping[str, str], bytes | None, float],
    tuple[int, bytes, Sequence[tuple[str, str]]],
]


def _urllib_request(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
) -> tuple[int, bytes, Sequence[tuple[str, str]]]:
    request = Request(url=url, data=body, method=method.upper(), headers=dict(headers))
    try:
        with urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
            return int(response.status), response.read(), tuple(response.headers.items())
    except HTTPError as exc:
        # HTTPError is also a response; retain the body for provider diagnostics
        # while never including it verbatim in the exception text.
        return int(exc.code), exc.read(), tuple(exc.headers.items()) if exc.headers else ()


def _headers_tuple(headers: Sequence[tuple[str, str]] | Message) -> tuple[tuple[str, str], ...]:
    return tuple((str(key), str(value)) for key, value in headers)


class SafeHttpClient:
    """Synchronous HTTPS transport suitable for collector worker threads."""

    def __init__(
        self,
        config: HttpClientConfig,
        *,
        base_url: str | None = None,
        requester: Requester | None = None,
        secret_values: Mapping[str, str] | None = None,
        failed_response_sink: Callable[[HttpResponse], object] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.base_url = base_url.rstrip("/") if base_url else None
        if self.base_url is not None:
            parsed_base = urlsplit(self.base_url)
            if parsed_base.scheme != "https" or not parsed_base.hostname:
                raise ValueError("base_url must be an absolute HTTPS URL")
            if parsed_base.hostname.lower().rstrip(".") not in config.allowed_hosts:
                raise ValueError("base_url host must be listed in allowed_hosts")
        self._requester = requester or _urllib_request
        self._secret_values = dict(secret_values or {})
        self._failed_response_sink = failed_response_sink
        self._clock = clock
        self._sleeper = sleeper
        self._next_request_at = 0.0
        self._failure_count = 0
        self._circuit_opened_at: float | None = None

    def _validate_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise HttpTransportError("external connectors require an absolute HTTPS URL")
        if parsed.username or parsed.password:
            raise HttpTransportError("external connector URLs cannot contain credentials")
        if any(token in parsed.path.lower() for token in ("/withdraw", "/transfer")):
            raise HttpTransportError("external connector rejected a transfer/withdrawal path")
        host = parsed.hostname.lower().rstrip(".")
        if host not in self.config.allowed_hosts:
            raise HttpTransportError(
                f"external host is not reviewed: {redact(host, secrets=self._secret_values)}"
            )

    def _before_request(self) -> None:
        now = self._clock()
        if self._circuit_opened_at is not None:
            if now - self._circuit_opened_at < self.config.circuit_reset_seconds:
                raise HttpTransportError("external connector circuit is open", retriable=True)
            self._circuit_opened_at = None
            self._failure_count = 0
        wait = self._next_request_at - now
        if wait > 0:
            self._sleeper(wait)
        self._next_request_at = max(self._next_request_at, self._clock()) + (
            1 / self.config.requests_per_second
        )

    def _failure(self) -> None:
        self._failure_count += 1
        if self._failure_count >= self.config.circuit_failure_threshold:
            self._circuit_opened_at = self._clock()

    def _success(self) -> None:
        self._failure_count = 0

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        acceptable_statuses: frozenset[int] = frozenset({200}),
    ) -> HttpResponse:
        self._validate_url(url)
        request_headers = {"User-Agent": self.config.user_agent, **dict(headers or {})}
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            self._before_request()
            try:
                status, response_body, response_headers = self._requester(
                    method.upper(), url, request_headers, body, self.config.timeout_seconds
                )
            except (OSError, URLError, TimeoutError) as exc:
                self._failure()
                last_error = exc
                if attempt >= self.config.max_retries:
                    raise HttpTransportError(
                        "external connector request failed", retriable=True
                    ) from exc
                self._sleeper(self.config.retry_backoff_seconds * (2**attempt))
                continue
            response = HttpResponse(
                status_code=status,
                body=response_body,
                headers=_headers_tuple(response_headers),
                fetched_at=datetime.now(UTC),
                url=url,
            )
            if status in acceptable_statuses:
                self._success()
                return response
            if self._failed_response_sink is not None:
                try:
                    self._failed_response_sink(response)
                except Exception as exc:
                    raise HttpTransportError(
                        "external connector failed-response spool rejected the response",
                        status_code=status,
                        retriable=False,
                    ) from exc
            retriable = status == 429 or status >= 500
            self._failure() if retriable else None
            if retriable and attempt < self.config.max_retries:
                self._sleeper(self.config.retry_backoff_seconds * (2**attempt))
                continue
            safe_headers = redacted_headers(dict(response.headers), secrets=self._secret_values)
            detail = redact(
                response.body[:256].decode("utf-8", errors="replace"), secrets=self._secret_values
            )
            raise HttpTransportError(
                f"external connector returned HTTP {status}; headers={safe_headers}; body={detail!r}",
                status_code=status,
                retriable=retriable,
            )
        raise HttpTransportError(
            "external connector request failed", retriable=True
        ) from last_error

    def get(self, url: str, *, headers: Mapping[str, str] | None = None) -> HttpResponse:
        return self.request("GET", url, headers=headers)

    def post_json(
        self, url: str, payload: Mapping[str, object], *, headers: Mapping[str, str] | None = None
    ) -> HttpResponse:
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        merged = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            **dict(headers or {}),
        }
        return self.request(
            "POST",
            url,
            headers=merged,
            body=encoded,
            acceptable_statuses=frozenset({200, 201, 202, 204}),
        )


class SourceHttpTransport(HttpTransport):
    """Adapter from the concrete client to the existing source collector port."""

    def __init__(self, client: SafeHttpClient) -> None:
        self.client = client

    def get(self, url: str) -> HttpResponse:
        return self.client.get(url)


__all__ = ["HttpClientConfig", "HttpTransportError", "SafeHttpClient", "SourceHttpTransport"]
