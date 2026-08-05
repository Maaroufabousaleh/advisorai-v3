"""Compliant deterministic acquisition ladder and browser escalation inputs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AcquisitionStep(StrEnum):
    OFFICIAL_BULK_API = "official_bulk_api"
    RSS_ATOM_HTTP = "rss_atom_http"
    DETERMINISTIC_PARSER = "deterministic_parser"
    PLAYWRIGHT = "playwright"
    CAMOUFOX = "camoufox"
    HERMES_DISCOVERY = "hermes_discovery"


@dataclass(frozen=True, slots=True)
class AcquisitionDecision:
    source: str
    step: AcquisitionStep
    public: bool
    robots_respected: bool
    rate_limit_respected: bool
    authentication_boundary_crossed: bool
    active_content_quarantined: bool
    admitted: bool
    reason: str


class AcquisitionPolicy:
    LADDER = (
        AcquisitionStep.OFFICIAL_BULK_API,
        AcquisitionStep.RSS_ATOM_HTTP,
        AcquisitionStep.DETERMINISTIC_PARSER,
        AcquisitionStep.PLAYWRIGHT,
        AcquisitionStep.CAMOUFOX,
        AcquisitionStep.HERMES_DISCOVERY,
    )

    def decide(
        self,
        *,
        source: str,
        step: AcquisitionStep,
        public: bool,
        robots_allowed: bool,
        rate_limit_allowed: bool,
        authentication_required: bool,
        active_content_quarantined: bool,
        lower_steps_failed: bool,
    ) -> AcquisitionDecision:
        if not source.strip():
            raise ValueError("acquisition source cannot be blank")
        escalated = step in {
            AcquisitionStep.PLAYWRIGHT,
            AcquisitionStep.CAMOUFOX,
            AcquisitionStep.HERMES_DISCOVERY,
        }
        admitted = all(
            (
                public,
                robots_allowed,
                rate_limit_allowed,
                not authentication_required,
                active_content_quarantined,
                (not escalated or lower_steps_failed),
            )
        )
        return AcquisitionDecision(
            source=source,
            step=step,
            public=public,
            robots_respected=robots_allowed,
            rate_limit_respected=rate_limit_allowed,
            authentication_boundary_crossed=authentication_required,
            active_content_quarantined=active_content_quarantined,
            admitted=admitted,
            reason="compliant acquisition step"
            if admitted
            else "access or security boundary failed",
        )
