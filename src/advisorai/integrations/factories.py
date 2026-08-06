"""Explicit constructors that bind typed secrets to exactly one adapter."""

from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import SecretStr

from advisorai.config.secrets import SecretSettings
from advisorai.gateway import GatewayPolicyConfig, PolicyGateway, RouteProfile
from advisorai.gateway.core import GatewayRecorder
from advisorai.ports import GatewayRoute, ModelGatewayPort

from .http import HttpClientConfig, SafeHttpClient
from .llm import OpenAICompatibleGatewayAdapter
from .venue import HmacVenueSigner, PaperTestnetVenueTransport


def _host(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.hostname:
        raise ValueError("connector URL must contain a hostname")
    return parsed.hostname.lower().rstrip(".")


def build_direct_gateway(
    settings: SecretSettings,
    route: GatewayRoute,
    *,
    allowed_hosts: tuple[str, ...] | None = None,
    endpoint_path: str = "/chat/completions",
    input_price_per_million: float | None = None,
    output_price_per_million: float | None = None,
    request_price_usd: float | None = None,
) -> OpenAICompatibleGatewayAdapter:
    if not settings.llm_base_url:
        raise ValueError("ADVISORAI_LLM_BASE_URL is required for a direct gateway")
    api_key = settings.secret_for("ADVISORAI_LLM_API_KEY")
    if not api_key:
        raise ValueError("ADVISORAI_LLM_API_KEY is required for a direct gateway")
    if settings.llm_provider and settings.llm_provider.lower() != route.provider.lower():
        raise ValueError("gateway route provider does not match configured provider")
    if settings.llm_model and settings.llm_model != route.model:
        raise ValueError("gateway route model does not match configured model")
    hosts = allowed_hosts or (_host(settings.llm_base_url),)
    client = SafeHttpClient(
        HttpClientConfig(allowed_hosts=hosts, user_agent="advisorai-v3/llm"),
        base_url=settings.llm_base_url,
        secret_values={"ADVISORAI_LLM_API_KEY": api_key},
    )
    return OpenAICompatibleGatewayAdapter(
        route,
        client,
        api_key=api_key,
        endpoint_path=endpoint_path,
        input_price_per_million=input_price_per_million,
        output_price_per_million=output_price_per_million,
        request_price_usd=request_price_usd,
    )


def build_paper_venue_transport(
    settings: SecretSettings,
    *,
    allowed_hosts: tuple[str, ...] | None = None,
    orders_path: str = "/orders",
) -> PaperTestnetVenueTransport:
    if not settings.venue_base_url:
        raise ValueError("ADVISORAI_VENUE_BASE_URL is required for the paper venue")
    api_key = settings.secret_for("ADVISORAI_VENUE_API_KEY")
    api_secret = settings.secret_for("ADVISORAI_VENUE_API_SECRET")
    if not api_key or not api_secret:
        raise ValueError("paper venue API key and API secret are required")
    hosts = allowed_hosts or (_host(settings.venue_base_url),)
    client = SafeHttpClient(
        HttpClientConfig(allowed_hosts=hosts, user_agent="advisorai-v3/paper-venue"),
        base_url=settings.venue_base_url,
        secret_values={
            "ADVISORAI_VENUE_API_KEY": api_key,
            "ADVISORAI_VENUE_API_SECRET": api_secret,
            "ADVISORAI_VENUE_PASSPHRASE": settings.secret_for("ADVISORAI_VENUE_PASSPHRASE") or "",
        },
    )
    signer = HmacVenueSigner(
        api_key=api_key,
        api_secret=SecretStr(api_secret),
        passphrase=(
            SecretStr(value)
            if (value := settings.secret_for("ADVISORAI_VENUE_PASSPHRASE"))
            else None
        ),
    )
    return PaperTestnetVenueTransport(client, settings, signer=signer, orders_path=orders_path)


def build_policy_gateway(
    *,
    contributor: ModelGatewayPort | None,
    private: ModelGatewayPort | None,
    config: GatewayPolicyConfig,
    recorder: GatewayRecorder | None = None,
    profiles: tuple[RouteProfile, ...] | None = None,
) -> PolicyGateway:
    """Bind admitted contributor/private adapters behind the policy router.

    Credential construction stays in the provider-specific adapters.  This
    constructor only composes already-admitted routes, making it impossible
    for the policy layer to inspect or persist a provider secret.
    """

    return PolicyGateway(
        contributor=contributor,
        private=private,
        config=config,
        recorder=recorder,
        profiles=profiles,
    )


__all__ = [
    "build_direct_gateway",
    "build_paper_venue_transport",
    "build_policy_gateway",
]
