"""Factory for the fixed V3-Core market/context collector set."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from advisorai.collectors.sources import (
    DeribitCollector,
    GDELTCollector,
    NativeVenueCollector,
    RawHttpSpool,
    RSSCollector,
    SourceDescriptor,
)
from advisorai.config.secrets import SecretSettings
from advisorai.contracts import SourceGrade

from .http import HttpClientConfig, SafeHttpClient, SourceHttpTransport


class SourceEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(min_length=1)
    allowed_host: str = Field(min_length=1)
    timeout_seconds: float = Field(default=15, gt=0, le=120)
    adapter_version: str = Field(default="http-source-v1", min_length=1)

    @field_validator("url")
    @classmethod
    def https_only(cls, value: str) -> str:
        if not value.startswith("https://") or any(
            token in value.lower() for token in ("/live", "/prod", "withdraw", "transfer")
        ):
            raise ValueError("source endpoints must be reviewed HTTPS read URLs")
        return value.rstrip("/")

    @field_validator("allowed_host")
    @classmethod
    def host_nonblank(cls, value: str) -> str:
        return value.strip().lower().rstrip(".")

    @model_validator(mode="after")
    def endpoint_host_matches_allowlist(self) -> SourceEndpoint:
        hostname = urlsplit(self.url).hostname
        if hostname is None or hostname.lower().rstrip(".") != self.allowed_host:
            raise ValueError("source endpoint hostname must match its allowed_host")
        return self


@dataclass(frozen=True, slots=True)
class V3CoreCollectors:
    native: NativeVenueCollector
    deribit: DeribitCollector
    rss: RSSCollector
    gdelt: GDELTCollector


def build_v3_core_collectors(
    *,
    settings: SecretSettings,
    native: SourceEndpoint,
    deribit: SourceEndpoint,
    rss: SourceEndpoint,
    gdelt: SourceEndpoint,
    raw_spool_dir: Path | None = None,
) -> V3CoreCollectors:
    """Construct collectors with one transport policy per reviewed host."""

    def transport(endpoint: SourceEndpoint, raw_spool: RawHttpSpool | None) -> SourceHttpTransport:
        client = SafeHttpClient(
            HttpClientConfig(
                allowed_hosts=(endpoint.allowed_host,),
                timeout_seconds=endpoint.timeout_seconds,
                user_agent="advisorai-v3/collector",
            ),
            base_url=endpoint.url,
            failed_response_sink=raw_spool.append if raw_spool is not None else None,
        )
        return SourceHttpTransport(client)

    def spool(name: str) -> RawHttpSpool | None:
        return RawHttpSpool(raw_spool_dir / f"{name}.jsonl") if raw_spool_dir else None

    if settings.venue_environment not in {"paper", "testnet", "paper_testnet"}:
        raise ValueError("V3-Core collector construction is paper/testnet only")
    native_spool = spool("native")
    deribit_spool = spool("deribit")
    rss_spool = spool("rss")
    gdelt_spool = spool("gdelt")
    return V3CoreCollectors(
        native=NativeVenueCollector(
            SourceDescriptor(
                name="native-venue",
                family="native_market",
                origin=settings.venue_name or "reviewed-paper-venue",
                grade=SourceGrade.EXECUTION,
                intended_use="execution_grade_market_truth",
                parser_version="native-v1",
            ),
            transport(native, native_spool),
            raw_spool=native_spool,
        ),
        deribit=DeribitCollector(
            SourceDescriptor(
                name="deribit",
                family="derivatives",
                origin="deribit",
                grade=SourceGrade.RESEARCH,
                intended_use="derivatives_context_only",
                parser_version="deribit-v1",
            ),
            transport(deribit, deribit_spool),
            raw_spool=deribit_spool,
        ),
        rss=RSSCollector(
            SourceDescriptor(
                name="official-rss",
                family="official_news",
                origin="operator_allowlisted_rss",
                grade=SourceGrade.CONTEXT,
                intended_use="context_only",
                parser_version="rss-v1",
            ),
            transport(rss, rss_spool),
            raw_spool=rss_spool,
        ),
        gdelt=GDELTCollector(
            SourceDescriptor(
                name="gdelt",
                family="news",
                origin="gdelt",
                grade=SourceGrade.CONTEXT,
                intended_use="context_only",
                parser_version="gdelt-v1",
            ),
            transport(gdelt, gdelt_spool),
            raw_spool=gdelt_spool,
        ),
    )


__all__ = ["SourceEndpoint", "V3CoreCollectors", "build_v3_core_collectors"]
