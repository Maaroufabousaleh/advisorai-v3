"""Unattended paper-soak, failure injection, and recovery gates."""

from .controller import FailureScenario, PaperSoakController, SoakGate, SoakSample
from .durable import (
    DurablePaperSoakRunner,
    SoakRecord,
    SoakRunConfig,
    SoakRunSummary,
    append_soak_record,
    make_soak_record,
    read_soak_records,
)

__all__ = [
    "DurablePaperSoakRunner",
    "FailureScenario",
    "PaperSoakController",
    "SoakGate",
    "SoakRecord",
    "SoakRunConfig",
    "SoakRunSummary",
    "SoakSample",
    "append_soak_record",
    "make_soak_record",
    "read_soak_records",
]
