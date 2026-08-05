"""Escalation policy for one public browser job."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit


class AcquisitionMethod(StrEnum):
    OFFICIAL_API = "official_api"
    RSS_HTTP = "rss_http"
    DETERMINISTIC_PARSER = "deterministic_parser"
    PLAYWRIGHT = "playwright"
    CAMOUFOX = "camoufox"
    HERMES_DISCOVERY = "hermes_discovery"


@dataclass(frozen=True, slots=True)
class BrowserJob:
    url: str
    method: AcquisitionMethod
    public_only: bool
    authentication_required: bool
    prompt_injection_blocked: bool
    active_content_stripped: bool


class BrowserEscalationPolicy:
    LADDER = (
        AcquisitionMethod.OFFICIAL_API,
        AcquisitionMethod.RSS_HTTP,
        AcquisitionMethod.DETERMINISTIC_PARSER,
        AcquisitionMethod.PLAYWRIGHT,
        AcquisitionMethod.CAMOUFOX,
        AcquisitionMethod.HERMES_DISCOVERY,
    )

    def admit(
        self,
        *,
        url: str,
        method: AcquisitionMethod,
        public_page: bool,
        ordinary_method_failed: bool,
        robots_allowed: bool = True,
        rate_limit_allowed: bool = True,
        active_content_quarantined: bool = True,
    ) -> BrowserJob:
        normalized_url = self._validate_public_url(url)
        if not normalized_url:
            raise ValueError("browser jobs require a URL")
        if (
            method
            in {
                AcquisitionMethod.PLAYWRIGHT,
                AcquisitionMethod.CAMOUFOX,
                AcquisitionMethod.HERMES_DISCOVERY,
            }
            and not ordinary_method_failed
        ):
            raise PermissionError(
                "browser escalation requires documented deterministic-method failure"
            )
        if not public_page:
            raise PermissionError("browser collectors may not cross authentication boundaries")
        if not robots_allowed or not rate_limit_allowed:
            raise PermissionError("browser collectors must respect robots and rate limits")
        if not active_content_quarantined:
            raise PermissionError("browser collectors must quarantine active content")
        return BrowserJob(
            url=normalized_url,
            method=method,
            public_only=True,
            authentication_required=False,
            prompt_injection_blocked=True,
            active_content_stripped=True,
        )

    @staticmethod
    def _validate_public_url(url: str) -> str:
        normalized = url.strip()
        if not normalized:
            return ""
        parsed = urlsplit(normalized)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError("browser jobs require an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise PermissionError("browser jobs may not embed URL credentials")
        try:
            host = parsed.hostname
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if host.lower() == "localhost" or (
            address is not None
            and (address.is_private or address.is_loopback or address.is_link_local)
        ):
            raise PermissionError("browser jobs may not target private or local hosts")
        try:
            _port = parsed.port
        except ValueError as exc:
            raise ValueError("browser URL contains an invalid port") from exc
        return normalized
