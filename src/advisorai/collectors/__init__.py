"""Deterministic V3-Core source collectors and quality monitoring."""

from .acquisition import AcquisitionDecision, AcquisitionPolicy, AcquisitionStep
from .official import AlfredCollector, SecEdgarCollector, VintagedReleaseCollector
from .quality import DataQualityMonitor, DataQualityReport, QualityDashboard, QualityFinding
from .sources import (
    CcxtCollector,
    DeribitCollector,
    GDELTCollector,
    HttpResponse,
    LseCorroborationCollector,
    NativeVenueCollector,
    PredictionMarketCollector,
    RSSCollector,
    SourceDescriptor,
)

__all__ = [
    "DataQualityMonitor",
    "DataQualityReport",
    "QualityDashboard",
    "AcquisitionDecision",
    "AcquisitionPolicy",
    "AcquisitionStep",
    "CcxtCollector",
    "VintagedReleaseCollector",
    "SecEdgarCollector",
    "AlfredCollector",
    "GDELTCollector",
    "DeribitCollector",
    "HttpResponse",
    "LseCorroborationCollector",
    "NativeVenueCollector",
    "PredictionMarketCollector",
    "QualityFinding",
    "RSSCollector",
    "SourceDescriptor",
]
