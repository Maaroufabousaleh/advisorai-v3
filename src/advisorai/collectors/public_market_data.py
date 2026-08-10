"""Reviewed, read-only public market-data source cards.

These cards are deliberately separate from paper/testnet execution adapters.
They can describe a public production market-data host, but they cannot carry
credentials or expose any order, account, transfer, or withdrawal capability.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PublicMarketDataSource(BaseModel):
    """An operator-reviewed public read route with no execution authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1)
    role: Literal["primary_candidate", "context_only"]
    rest_base_url: str = Field(min_length=1)
    rest_host: str = Field(min_length=1)
    ws_url: str = Field(min_length=1)
    ws_host: str = Field(min_length=1)
    symbols: tuple[str, ...] = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    credentials_required: Literal[False] = False
    write_capability: Literal[False] = False

    @field_validator("source_id", "rest_host", "ws_host", "adapter_version")
    @classmethod
    def nonblank(cls, value: str) -> str:
        return value.strip()

    @field_validator("rest_host", "ws_host")
    @classmethod
    def normalize_host(cls, value: str) -> str:
        return value.strip().lower().rstrip(".")

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        symbols = tuple(item.strip().upper() for item in value if item.strip())
        if not symbols or len(symbols) != len(set(symbols)):
            raise ValueError("public market-data symbols must be unique and nonblank")
        return symbols

    @field_validator("rest_base_url")
    @classmethod
    def validate_rest_url(cls, value: str) -> str:
        parsed = urlsplit(value.rstrip("/"))
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("public market-data REST base must be a credential-free HTTPS root")
        return value.rstrip("/")

    @field_validator("ws_url")
    @classmethod
    def validate_ws_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        path = parsed.path.lower()
        if (
            parsed.scheme != "wss"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or any(token in path for token in ("/order", "/cancel", "/withdraw", "/transfer"))
        ):
            raise ValueError("public market-data WebSocket must be a credential-free read URL")
        return value

    @model_validator(mode="after")
    def validate_host_identity(self) -> PublicMarketDataSource:
        rest = urlsplit(self.rest_base_url)
        ws = urlsplit(self.ws_url)
        if rest.hostname is None or rest.hostname.lower().rstrip(".") != self.rest_host:
            raise ValueError("public market-data REST host does not match its card")
        if ws.hostname is None or ws.hostname.lower().rstrip(".") != self.ws_host:
            raise ValueError("public market-data WebSocket host does not match its card")
        return self


def reviewed_public_market_data_sources() -> tuple[PublicMarketDataSource, ...]:
    """Return the fixed public candidates used by the Phase-3 bake-off."""

    return (
        PublicMarketDataSource(
            source_id="binance_spot_public_market_data",
            role="primary_candidate",
            rest_base_url="https://api.binance.com",
            rest_host="api.binance.com",
            ws_url="wss://stream.binance.com:9443/ws",
            ws_host="stream.binance.com",
            symbols=("BTCUSDT", "ETHUSDT"),
            adapter_version="binance-public-market-data-v1",
        ),
        PublicMarketDataSource(
            source_id="coinbase_exchange_public_market_data",
            role="primary_candidate",
            rest_base_url="https://api.exchange.coinbase.com",
            rest_host="api.exchange.coinbase.com",
            ws_url="wss://ws-feed.exchange.coinbase.com",
            ws_host="ws-feed.exchange.coinbase.com",
            symbols=("BTC-USD", "ETH-USD"),
            adapter_version="coinbase-public-market-data-v1",
        ),
        PublicMarketDataSource(
            source_id="deribit_public_context",
            role="context_only",
            rest_base_url="https://www.deribit.com",
            rest_host="www.deribit.com",
            ws_url="wss://www.deribit.com/ws/api/v2",
            ws_host="www.deribit.com",
            symbols=("BTC-PERPETUAL", "ETH-PERPETUAL"),
            adapter_version="deribit-public-context-v1",
        ),
    )


__all__ = ["PublicMarketDataSource", "reviewed_public_market_data_sources"]
