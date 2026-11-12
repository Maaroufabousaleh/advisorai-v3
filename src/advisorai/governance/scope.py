"""Typed Trading Scope & Authority Matrix V1.

This module is a non-executing policy boundary. It can classify a proposed
action, but it cannot create an order, load credentials, change RiskKernel or
OMS policy, approve a human authorization, or grant execution authority.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .authorization import ActorType, HumanAuthorization, authorization_is_valid
from .decisions import DecisionOutcome
from .hashing import canonical_sha256


class ScopeClass(StrEnum):
    LIVE_ELIGIBLE = "LIVE_ELIGIBLE"
    PAPER_ELIGIBLE = "PAPER_ELIGIBLE"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    DISABLED = "DISABLED"
    SYSTEM_FORBIDDEN = "SYSTEM_FORBIDDEN"
    HUMAN_TECHNICAL_GATE = "HUMAN_TECHNICAL_GATE"
    AUTONOMOUS_PROTECTIVE = "AUTONOMOUS_PROTECTIVE"


class ScopeDecisionOutcome(StrEnum):
    ALLOW_WITHIN_GOVERNANCE = "ALLOW_WITHIN_GOVERNANCE"
    ALLOW_AUTONOMOUS = "ALLOW_WITHIN_GOVERNANCE"  # compatibility alias
    ALLOW_HUMAN_GATED = "ALLOW_HUMAN_GATED"
    REQUIRE_HUMAN = "REQUIRE_HUMAN"
    REQUIRE_TECHNICAL_GATE = "REQUIRE_TECHNICAL_GATE"
    REQUIRE_HUMAN_AND_TECHNICAL_GATE = "REQUIRE_HUMAN_AND_TECHNICAL_GATE"
    REQUIRE_HUMAN_TECHNICAL_GATE = "REQUIRE_HUMAN_AND_TECHNICAL_GATE"  # compatibility alias
    PAPER_ELIGIBLE = "PAPER_ELIGIBLE"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    DISABLED = "DISABLED"
    SYSTEM_FORBIDDEN = "SYSTEM_FORBIDDEN"
    HARD_BLOCK = "HARD_BLOCK"


class AuthorityClass(StrEnum):
    SYSTEM_FIXED = "SYSTEM_FIXED"
    HUMAN_ONLY = "HUMAN_ONLY"
    HUMAN_AND_TECHNICAL_GATE = "HUMAN_AND_TECHNICAL_GATE"
    AUTONOMOUS_WITHIN_LIMITS = "AUTONOMOUS_WITHIN_LIMITS"
    AUTONOMOUS_RISK_REDUCTION = "AUTONOMOUS_RISK_REDUCTION"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    PAPER_ONLY = "PAPER_ONLY"


class HumanAuthorizationState(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    VALID = "VALID"
    MISSING = "MISSING"
    EXPIRED = "EXPIRED"
    NON_HUMAN = "NON_HUMAN"
    INVALID = "INVALID"


class EligibilityTier(StrEnum):
    RESEARCH_ELIGIBLE = "RESEARCH_ELIGIBLE"
    PAPER_ELIGIBLE = "PAPER_ELIGIBLE"
    LIVE_ELIGIBLE = "LIVE_ELIGIBLE"


class ScopeAssetClass(StrEnum):
    CRYPTO_SPOT = "CRYPTO_SPOT"
    EQUITY = "EQUITY"
    ETF = "ETF"
    FUTURE = "FUTURE"
    OPTION = "OPTION"
    CFD = "CFD"
    OTHER = "OTHER"


AssetClass = ScopeAssetClass


class MarketType(StrEnum):
    SPOT = "SPOT"
    MARGIN = "MARGIN"
    FUTURES = "FUTURES"
    PERPETUAL = "PERPETUAL"
    OPTIONS = "OPTIONS"
    CFDS = "CFDS"
    CFD = "CFDS"  # compatibility alias
    PERPETUAL_FUTURES = "PERPETUAL"  # descriptive alias
    LEVERAGED_TOKENS = "LEVERAGED_TOKENS"
    LEVERAGED_TOKEN = "LEVERAGED_TOKENS"  # descriptive alias
    SYNTHETIC_LEVERAGED_EXPOSURE = "SYNTHETIC_LEVERAGED_EXPOSURE"


class PositionDirection(StrEnum):
    LONG = "LONG"
    FLAT = "FLAT"
    SHORT = "SHORT"


class TradingCapability(StrEnum):
    SHORT_SELLING = "SHORT_SELLING"
    MARGIN_BORROWING = "MARGIN_BORROWING"
    FUTURES = "FUTURES"
    PERPETUAL_FUTURES = "PERPETUAL_FUTURES"
    OPTIONS = "OPTIONS"
    CFDS = "CFDS"
    LEVERAGED_TOKENS = "LEVERAGED_TOKENS"
    SYNTHETIC_LEVERAGED_EXPOSURE = "SYNTHETIC_LEVERAGED_EXPOSURE"


class CredentialCapability(StrEnum):
    MARKET_DATA_READ = "MARKET_DATA_READ"
    ACCOUNT_READ = "ACCOUNT_READ"
    ORDER_READ = "ORDER_READ"
    ORDER_CREATE = "ORDER_CREATE"
    ORDER_CANCEL = "ORDER_CANCEL"
    WITHDRAW_CRYPTO = "WITHDRAW_CRYPTO"
    WITHDRAW_FIAT = "WITHDRAW_FIAT"
    EXTERNAL_TRANSFER = "EXTERNAL_TRANSFER"
    INTERNAL_ACCOUNT_TRANSFER = "INTERNAL_ACCOUNT_TRANSFER"
    CHANGE_WITHDRAWAL_ADDRESS = "CHANGE_WITHDRAWAL_ADDRESS"
    WHITELIST_WITHDRAWAL_ADDRESS = "WHITELIST_WITHDRAWAL_ADDRESS"
    API_KEY_ADMINISTRATION = "API_KEY_ADMINISTRATION"
    ACCOUNT_SECURITY_ADMINISTRATION = "ACCOUNT_SECURITY_ADMINISTRATION"


class ModelLifecycle(StrEnum):
    RESEARCH = "RESEARCH"
    CHALLENGER = "CHALLENGER"
    SHADOW = "SHADOW"
    PAPER = "PAPER"
    ADMITTED = "ADMITTED"
    RETIRED = "RETIRED"


class StrategyLifecycle(StrEnum):
    PROPOSED = "PROPOSED"
    SCREENED = "SCREENED"
    VALIDATED = "VALIDATED"
    SHADOW = "SHADOW"
    PAPER = "PAPER"
    PROMOTED = "PROMOTED"
    RETIRED = "RETIRED"


class ScopeAction(StrEnum):
    TRADE = "TRADE"
    OPEN_BTC_LONG_SPOT = "OPEN_BTC_LONG_SPOT"
    OPEN_ETH_LONG_SPOT = "OPEN_ETH_LONG_SPOT"
    INCREASE_BTC_POSITION = "INCREASE_BTC_POSITION"
    INCREASE_ETH_POSITION = "INCREASE_ETH_POSITION"
    REDUCE_BTC_POSITION = "REDUCE_BTC_POSITION"
    CLOSE_BTC_POSITION = "CLOSE_BTC_POSITION"
    SHORT_BTC = "SHORT_BTC"
    TRADE_SOL = "TRADE_SOL"
    TRADE_EQUITY = "TRADE_EQUITY"
    TRADE_OPTION = "TRADE_OPTION"
    TRADE_FUTURE = "TRADE_FUTURE"
    USE_MARGIN = "USE_MARGIN"
    PAPER_TRADE = "PAPER_TRADE"
    REDUCE_RISK = "REDUCE_RISK"
    EMERGENCY_PROTECTIVE = "EMERGENCY_PROTECTIVE"
    STOP_TRADING_DUE_EMERGENCY = "STOP_TRADING_DUE_EMERGENCY"
    CANCEL_UNSAFE_ORDER = "CANCEL_UNSAFE_ORDER"
    CANCEL_NORMAL_WORKING_ORDER = "CANCEL_NORMAL_WORKING_ORDER"
    RESEARCH = "RESEARCH"
    START_RESEARCH_EXPERIMENT = "START_RESEARCH_EXPERIMENT"
    RUN_BACKTEST = "RUN_BACKTEST"
    RUN_PAPER_STRATEGY = "RUN_PAPER_STRATEGY"
    WITHDRAW = "WITHDRAW"
    WITHDRAW_FUNDS = "WITHDRAW_FUNDS"
    WITHDRAW_CRYPTO = "WITHDRAW_CRYPTO"
    WITHDRAW_FIAT = "WITHDRAW_FIAT"
    EXTERNAL_TRANSFER = "EXTERNAL_TRANSFER"
    TRANSFER_FUNDS = "TRANSFER_FUNDS"
    INTERNAL_ACCOUNT_TRANSFER = "INTERNAL_ACCOUNT_TRANSFER"
    INITIATE_DEPOSIT = "INITIATE_DEPOSIT"
    CREATE_PAYMENT_INSTRUCTION = "CREATE_PAYMENT_INSTRUCTION"
    CHANGE_WITHDRAWAL_ADDRESS = "CHANGE_WITHDRAWAL_ADDRESS"
    WHITELIST_WITHDRAWAL_ADDRESS = "WHITELIST_WITHDRAWAL_ADDRESS"
    MODIFY_WITHDRAWAL_PERMISSIONS = "MODIFY_WITHDRAWAL_PERMISSIONS"
    API_KEY_ADMINISTRATION = "API_KEY_ADMINISTRATION"
    ACCOUNT_SECURITY_ADMINISTRATION = "ACCOUNT_SECURITY_ADMINISTRATION"
    ROTATE_EXECUTION_CREDENTIALS = "ROTATE_EXECUTION_CREDENTIALS"
    ADD_ASSET = "ADD_ASSET"
    PROPOSE_NEW_INSTRUMENT = "PROPOSE_NEW_INSTRUMENT"
    ACTIVATE_NEW_LIVE_INSTRUMENT = "ACTIVATE_NEW_LIVE_INSTRUMENT"
    ADD_BROKER_OR_VENUE = "ADD_BROKER_OR_VENUE"
    ADD_BROKER = "ADD_BROKER"
    CHANGE_BROKER = "CHANGE_BROKER"
    CHANGE_VENUE = "CHANGE_VENUE"
    SWITCH_VENUE = "SWITCH_VENUE"
    SWITCH_EXECUTION_VENUE = "SWITCH_EXECUTION_VENUE"
    CHANGE_PRODUCTION_ENDPOINT = "CHANGE_PRODUCTION_ENDPOINT"
    ENABLE_PRODUCTION_FROM_TESTNET = "ENABLE_PRODUCTION_FROM_TESTNET"
    ENABLE_SHORTING = "ENABLE_SHORTING"
    ENABLE_DERIVATIVES = "ENABLE_DERIVATIVES"
    ENABLE_DERIVATIVE_CLASS = "ENABLE_DERIVATIVE_CLASS"
    ENABLE_LEVERAGE = "ENABLE_LEVERAGE"
    INCREASE_LEVERAGE = "INCREASE_LEVERAGE"
    DECREASE_LEVERAGE = "DECREASE_LEVERAGE"
    CHANGE_MODEL = "CHANGE_MODEL"
    PROMOTE_MODEL = "PROMOTE_MODEL"
    CHANGE_STRATEGY = "CHANGE_STRATEGY"
    PROMOTE_STRATEGY = "PROMOTE_STRATEGY"
    RELAX_RISK_LIMITS = "RELAX_RISK_LIMITS"
    CHANGE_DAILY_LOSS_THRESHOLD = "CHANGE_DAILY_LOSS_THRESHOLD"
    CHANGE_DRAWDOWN_THRESHOLD = "CHANGE_DRAWDOWN_THRESHOLD"
    CHANGE_POSITION_LIMIT = "CHANGE_POSITION_LIMIT"
    CHANGE_CAPITAL_ALLOCATION = "CHANGE_CAPITAL_ALLOCATION"
    INCREASE_CAPITAL_STAGE = "INCREASE_CAPITAL_STAGE"
    RESUME_AFTER_HARD_KILL = "RESUME_AFTER_HARD_KILL"


class ScopeReasonCode(StrEnum):
    LIVE_SPOT_LONG_FLAT = "LIVE_SPOT_LONG_FLAT"
    LIVE_CAPITAL_DISABLED = "LIVE_CAPITAL_DISABLED"
    LIVE_DISABLED = "LIVE_DISABLED"
    INSTRUMENT_NOT_LIVE_AUTHORIZED = "INSTRUMENT_NOT_LIVE_AUTHORIZED"
    ASSET_CLASS_NOT_AUTHORIZED = "ASSET_CLASS_NOT_AUTHORIZED"
    GOVERNANCE_DECISION_REQUIRED = "GOVERNANCE_DECISION_REQUIRED"
    QUALIFICATION_REQUIRED = "QUALIFICATION_REQUIRED"
    TECHNICAL_QUALIFICATION_REQUIRED = "TECHNICAL_QUALIFICATION_REQUIRED"
    FORMAL_EVIDENCE_REQUIRED = "FORMAL_EVIDENCE_REQUIRED"
    RISK_KERNEL_REQUIRED = "RISK_KERNEL_REQUIRED"
    RISK_KERNEL_REJECTED = "RISK_KERNEL_REJECTED"
    OMS_STATE_REQUIRED = "OMS_STATE_REQUIRED"
    OMS_STATE_AMBIGUOUS = "OMS_STATE_AMBIGUOUS"
    VENUE_NOT_APPROVED = "VENUE_NOT_APPROVED"
    VENUE_NOT_AUTHORIZED = "VENUE_NOT_AUTHORIZED"
    NEW_ASSET = "NEW_ASSET"
    SHORTS_DISABLED = "SHORTS_DISABLED"
    MARGIN_DISABLED = "MARGIN_DISABLED"
    FUTURES_DISABLED = "FUTURES_DISABLED"
    PERPETUALS_DISABLED = "PERPETUALS_DISABLED"
    OPTIONS_DISABLED = "OPTIONS_DISABLED"
    CFDS_DISABLED = "CFDS_DISABLED"
    LEVERAGED_TOKENS_DISABLED = "LEVERAGED_TOKENS_DISABLED"
    SYNTHETIC_LEVERAGE_DISABLED = "SYNTHETIC_LEVERAGE_DISABLED"
    DERIVATIVES_DISABLED = "DERIVATIVES_DISABLED"
    LEVERAGE_DISABLED = "LEVERAGE_DISABLED"
    SYSTEM_FORBIDDEN_ACTION = "SYSTEM_FORBIDDEN_ACTION"
    TRANSFER_FORBIDDEN = "TRANSFER_FORBIDDEN"
    WITHDRAWAL_FORBIDDEN = "WITHDRAWAL_FORBIDDEN"
    HUMAN_AUTHORIZATION_REQUIRED = "HUMAN_AUTHORIZATION_REQUIRED"
    HUMAN_AUTHORIZATION_EXPIRED = "HUMAN_AUTHORIZATION_EXPIRED"
    TECHNICAL_GATE_REQUIRED = "TECHNICAL_GATE_REQUIRED"
    HUMAN_TECHNICAL_GATE_SATISFIED = "HUMAN_TECHNICAL_GATE_SATISFIED"
    HUMAN_ACTOR_REQUIRED = "HUMAN_ACTOR_REQUIRED"
    RESEARCH_SCOPE_ONLY = "RESEARCH_SCOPE_ONLY"
    PAPER_SCOPE_ONLY = "PAPER_SCOPE_ONLY"
    PAPER_QUALIFICATION_REQUIRED = "PAPER_QUALIFICATION_REQUIRED"
    SCOPE_INPUT_UNKNOWN = "SCOPE_INPUT_UNKNOWN"
    PROTECTIVE_ACTION = "PROTECTIVE_ACTION"
    PROTECTIVE_TRIGGER_REQUIRED = "PROTECTIVE_TRIGGER_REQUIRED"
    EXISTING_POSITION_REQUIRED = "EXISTING_POSITION_REQUIRED"
    NEGATIVE_EXPOSURE_FORBIDDEN = "NEGATIVE_EXPOSURE_FORBIDDEN"
    CAPITAL_INPUT_REQUIRED = "CAPITAL_INPUT_REQUIRED"
    CAPITAL_AUTHORIZATION_REQUIRED = "CAPITAL_AUTHORIZATION_REQUIRED"
    CAPITAL_SCOPE_INVALID = "CAPITAL_SCOPE_INVALID"
    CREDENTIAL_CAPABILITIES_REQUIRED = "CREDENTIAL_CAPABILITIES_REQUIRED"
    CREDENTIAL_CAPABILITY_MISSING = "CREDENTIAL_CAPABILITY_MISSING"
    CREDENTIAL_CAPABILITY_FORBIDDEN = "CREDENTIAL_CAPABILITY_FORBIDDEN"
    MODEL_NOT_ADMITTED = "MODEL_NOT_ADMITTED"
    STRATEGY_NOT_PROMOTED = "STRATEGY_NOT_PROMOTED"
    HARD_KILL_ACTIVE = "HARD_KILL_ACTIVE"
    RESUME_AUTHORIZATION_REQUIRED = "RESUME_AUTHORIZATION_REQUIRED"
    RISK_LIMIT_TIGHTENING = "RISK_LIMIT_TIGHTENING"
    RISK_LIMIT_RELAXATION = "RISK_LIMIT_RELAXATION"
    POSITION_LIMIT = "POSITION_LIMIT"


class CredentialCapabilitySet(BaseModel):
    """Declared credential capabilities, never actual secrets or credentials."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    enabled: tuple[CredentialCapability, ...] = ()

    @field_validator("enabled")
    @classmethod
    def normalize_enabled(
        cls, value: tuple[CredentialCapability, ...]
    ) -> tuple[CredentialCapability, ...]:
        if len(value) != len(set(value)):
            raise ValueError("credential capabilities must be unique")
        return value

    def missing(
        self, required: tuple[CredentialCapability, ...]
    ) -> tuple[CredentialCapability, ...]:
        enabled = set(self.enabled)
        return tuple(capability for capability in required if capability not in enabled)

    def forbidden(
        self, forbidden: tuple[CredentialCapability, ...]
    ) -> tuple[CredentialCapability, ...]:
        forbidden_set = set(forbidden)
        return tuple(capability for capability in self.enabled if capability in forbidden_set)


class ActionMatrixEntry(BaseModel):
    """One explicit authority row; no row can grant direct LLM execution."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    action: ScopeAction
    authority_class: AuthorityClass
    required_technical_gates: tuple[str, ...] = ()
    human_authorization_action: ScopeAction | None = None
    autonomous_execution_possible: bool = False
    llm_may_propose: bool = False
    llm_may_execute: bool = False
    refusal_reasons: tuple[ScopeReasonCode, ...] = ()

    @field_validator("required_technical_gates")
    @classmethod
    def validate_gate_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("technical gate names must be unique and non-blank")
        return normalized

    @model_validator(mode="after")
    def prohibit_llm_execution(self) -> ActionMatrixEntry:
        if self.llm_may_execute:
            raise ValueError("LLM execution authority is prohibited")
        if self.authority_class is AuthorityClass.SYSTEM_FIXED and self.llm_may_propose:
            raise ValueError("system-forbidden actions cannot be proposed as executable work")
        return self


def _matrix_entry(
    action: ScopeAction,
    authority_class: AuthorityClass,
    *,
    gates: tuple[str, ...] = (),
    human_action: ScopeAction | None = None,
    autonomous: bool = False,
    llm_propose: bool = False,
    reasons: tuple[ScopeReasonCode, ...] = (),
) -> ActionMatrixEntry:
    return ActionMatrixEntry(
        action=action,
        authority_class=authority_class,
        required_technical_gates=gates,
        human_authorization_action=human_action,
        autonomous_execution_possible=autonomous,
        llm_may_propose=llm_propose,
        refusal_reasons=reasons,
    )


_DISABLED_MATRIX_REASONS = (ScopeReasonCode.ASSET_CLASS_NOT_AUTHORIZED,)
_SYSTEM_FORBIDDEN_MATRIX_REASONS = (ScopeReasonCode.SYSTEM_FORBIDDEN_ACTION,)
_HUMAN_GATE_GATES = ("technical_qualification", "human_authorization")

DEFAULT_ACTION_AUTHORITY_MATRIX: tuple[ActionMatrixEntry, ...] = (
    _matrix_entry(
        ScopeAction.OPEN_BTC_LONG_SPOT,
        AuthorityClass.AUTONOMOUS_WITHIN_LIMITS,
        gates=("live_activation", "governance", "risk_kernel", "oms", "venue"),
        autonomous=True,
    ),
    _matrix_entry(
        ScopeAction.OPEN_ETH_LONG_SPOT,
        AuthorityClass.AUTONOMOUS_WITHIN_LIMITS,
        gates=("live_activation", "governance", "risk_kernel", "oms", "venue"),
        autonomous=True,
    ),
    _matrix_entry(
        ScopeAction.INCREASE_BTC_POSITION,
        AuthorityClass.AUTONOMOUS_WITHIN_LIMITS,
        gates=("live_activation", "governance", "risk_kernel", "oms", "venue"),
        autonomous=True,
    ),
    _matrix_entry(
        ScopeAction.INCREASE_ETH_POSITION,
        AuthorityClass.AUTONOMOUS_WITHIN_LIMITS,
        gates=("live_activation", "governance", "risk_kernel", "oms", "venue"),
        autonomous=True,
    ),
    _matrix_entry(
        ScopeAction.REDUCE_BTC_POSITION,
        AuthorityClass.AUTONOMOUS_RISK_REDUCTION,
        gates=("existing_long", "risk_kernel", "oms"),
        autonomous=True,
    ),
    _matrix_entry(
        ScopeAction.CLOSE_BTC_POSITION,
        AuthorityClass.AUTONOMOUS_RISK_REDUCTION,
        gates=("existing_long", "risk_kernel", "oms"),
        autonomous=True,
    ),
    _matrix_entry(
        ScopeAction.TRADE,
        AuthorityClass.AUTONOMOUS_WITHIN_LIMITS,
        gates=("live_scope", "live_activation", "governance", "risk_kernel", "oms"),
        autonomous=True,
    ),
    _matrix_entry(
        ScopeAction.PAPER_TRADE,
        AuthorityClass.PAPER_ONLY,
        gates=("paper_qualification",),
        autonomous=True,
    ),
    _matrix_entry(
        ScopeAction.REDUCE_RISK,
        AuthorityClass.AUTONOMOUS_RISK_REDUCTION,
        gates=("deterministic_trigger", "risk_kernel", "oms"),
        autonomous=True,
    ),
    _matrix_entry(
        ScopeAction.EMERGENCY_PROTECTIVE,
        AuthorityClass.AUTONOMOUS_RISK_REDUCTION,
        gates=("deterministic_trigger", "risk_kernel", "oms"),
        autonomous=True,
    ),
    _matrix_entry(
        ScopeAction.STOP_TRADING_DUE_EMERGENCY,
        AuthorityClass.AUTONOMOUS_RISK_REDUCTION,
        gates=("deterministic_trigger", "risk_kernel", "oms"),
        autonomous=True,
    ),
    _matrix_entry(
        ScopeAction.CANCEL_UNSAFE_ORDER,
        AuthorityClass.AUTONOMOUS_RISK_REDUCTION,
        gates=("deterministic_trigger", "oms"),
        autonomous=True,
    ),
    _matrix_entry(
        ScopeAction.CANCEL_NORMAL_WORKING_ORDER,
        AuthorityClass.AUTONOMOUS_WITHIN_LIMITS,
        gates=("oms",),
        autonomous=True,
    ),
    _matrix_entry(
        ScopeAction.DECREASE_LEVERAGE,
        AuthorityClass.AUTONOMOUS_RISK_REDUCTION,
        gates=("risk_kernel", "oms"),
        autonomous=True,
    ),
    *(
        _matrix_entry(action, AuthorityClass.SYSTEM_FIXED, reasons=_DISABLED_MATRIX_REASONS)
        for action in (
            ScopeAction.SHORT_BTC,
            ScopeAction.TRADE_SOL,
            ScopeAction.TRADE_EQUITY,
            ScopeAction.TRADE_OPTION,
            ScopeAction.TRADE_FUTURE,
            ScopeAction.USE_MARGIN,
        )
    ),
    *(
        _matrix_entry(action, AuthorityClass.SYSTEM_FIXED, reasons=_SYSTEM_FORBIDDEN_MATRIX_REASONS)
        for action in (
            ScopeAction.WITHDRAW,
            ScopeAction.WITHDRAW_FUNDS,
            ScopeAction.WITHDRAW_CRYPTO,
            ScopeAction.WITHDRAW_FIAT,
            ScopeAction.EXTERNAL_TRANSFER,
            ScopeAction.TRANSFER_FUNDS,
            ScopeAction.INTERNAL_ACCOUNT_TRANSFER,
            ScopeAction.INITIATE_DEPOSIT,
            ScopeAction.CREATE_PAYMENT_INSTRUCTION,
            ScopeAction.CHANGE_WITHDRAWAL_ADDRESS,
            ScopeAction.WHITELIST_WITHDRAWAL_ADDRESS,
            ScopeAction.MODIFY_WITHDRAWAL_PERMISSIONS,
            ScopeAction.API_KEY_ADMINISTRATION,
            ScopeAction.ACCOUNT_SECURITY_ADMINISTRATION,
            ScopeAction.ROTATE_EXECUTION_CREDENTIALS,
        )
    ),
    *(
        _matrix_entry(
            action,
            AuthorityClass.HUMAN_AND_TECHNICAL_GATE,
            gates=_HUMAN_GATE_GATES,
            human_action=action,
            llm_propose=True,
        )
        for action in (
            ScopeAction.ADD_ASSET,
            ScopeAction.ACTIVATE_NEW_LIVE_INSTRUMENT,
            ScopeAction.ADD_BROKER_OR_VENUE,
            ScopeAction.ADD_BROKER,
            ScopeAction.CHANGE_BROKER,
            ScopeAction.CHANGE_VENUE,
            ScopeAction.SWITCH_VENUE,
            ScopeAction.SWITCH_EXECUTION_VENUE,
            ScopeAction.CHANGE_PRODUCTION_ENDPOINT,
            ScopeAction.ENABLE_PRODUCTION_FROM_TESTNET,
            ScopeAction.ENABLE_SHORTING,
            ScopeAction.ENABLE_DERIVATIVES,
            ScopeAction.ENABLE_DERIVATIVE_CLASS,
            ScopeAction.ENABLE_LEVERAGE,
            ScopeAction.INCREASE_LEVERAGE,
            ScopeAction.CHANGE_MODEL,
            ScopeAction.PROMOTE_MODEL,
            ScopeAction.CHANGE_STRATEGY,
            ScopeAction.PROMOTE_STRATEGY,
            ScopeAction.RELAX_RISK_LIMITS,
            ScopeAction.RESUME_AFTER_HARD_KILL,
        )
    ),
    *(
        _matrix_entry(
            action,
            AuthorityClass.HUMAN_ONLY,
            gates=("human_authorization",),
            human_action=action,
            llm_propose=True,
        )
        for action in (
            ScopeAction.CHANGE_DAILY_LOSS_THRESHOLD,
            ScopeAction.CHANGE_DRAWDOWN_THRESHOLD,
            ScopeAction.CHANGE_CAPITAL_ALLOCATION,
            ScopeAction.INCREASE_CAPITAL_STAGE,
        )
    ),
    _matrix_entry(
        ScopeAction.CHANGE_POSITION_LIMIT,
        AuthorityClass.HUMAN_AND_TECHNICAL_GATE,
        gates=("limit_direction", "hard_ceiling", "human_authorization"),
        human_action=ScopeAction.RELAX_RISK_LIMITS,
        llm_propose=True,
    ),
    _matrix_entry(
        ScopeAction.PROPOSE_NEW_INSTRUMENT,
        AuthorityClass.RESEARCH_ONLY,
        gates=("research_snapshot",),
        llm_propose=True,
    ),
    *(
        _matrix_entry(
            action,
            AuthorityClass.RESEARCH_ONLY,
            gates=("research_snapshot",),
            llm_propose=True,
        )
        for action in (
            ScopeAction.RESEARCH,
            ScopeAction.START_RESEARCH_EXPERIMENT,
            ScopeAction.RUN_BACKTEST,
        )
    ),
    _matrix_entry(
        ScopeAction.RUN_PAPER_STRATEGY,
        AuthorityClass.PAPER_ONLY,
        gates=("paper_qualification",),
        autonomous=True,
        llm_propose=True,
    ),
)


class TradingScopePolicy(BaseModel):
    """Frozen V1 scope matrix stacked on the human-governance policy."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    human_governance_policy_version: str = Field(min_length=1)
    live_activation_permitted: bool = False
    live_asset_class: ScopeAssetClass = ScopeAssetClass.CRYPTO_SPOT
    live_symbols: tuple[str, ...]
    live_market_type: MarketType = MarketType.SPOT
    live_directions: tuple[PositionDirection, ...]
    paper_enabled: bool = True
    paper_symbols: tuple[str, ...] = ()
    approved_spot_venues: tuple[str, ...] = ()
    disabled_market_types: tuple[MarketType, ...]
    disabled_capabilities: tuple[TradingCapability, ...] = tuple(TradingCapability)
    system_forbidden_actions: tuple[ScopeAction, ...]
    human_gate_actions: tuple[ScopeAction, ...]
    required_live_credential_capabilities: tuple[CredentialCapability, ...] = (
        CredentialCapability.MARKET_DATA_READ,
        CredentialCapability.ACCOUNT_READ,
        CredentialCapability.ORDER_READ,
        CredentialCapability.ORDER_CREATE,
        CredentialCapability.ORDER_CANCEL,
    )
    forbidden_live_credential_capabilities: tuple[CredentialCapability, ...] = (
        CredentialCapability.WITHDRAW_CRYPTO,
        CredentialCapability.WITHDRAW_FIAT,
        CredentialCapability.EXTERNAL_TRANSFER,
        CredentialCapability.INTERNAL_ACCOUNT_TRANSFER,
        CredentialCapability.CHANGE_WITHDRAWAL_ADDRESS,
        CredentialCapability.WHITELIST_WITHDRAWAL_ADDRESS,
        CredentialCapability.API_KEY_ADMINISTRATION,
        CredentialCapability.ACCOUNT_SECURITY_ADMINISTRATION,
    )
    max_single_asset_fraction: Decimal = Decimal("0.15")
    research_enabled: bool = True
    execution_authority: bool = False
    action_matrix: tuple[ActionMatrixEntry, ...] = DEFAULT_ACTION_AUTHORITY_MATRIX
    content_hash: str = ""

    @field_validator("live_symbols", "paper_symbols")
    @classmethod
    def normalize_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().upper() for item in value)
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("scope symbols must be unique and non-blank")
        return normalized

    @field_validator("approved_spot_venues")
    @classmethod
    def normalize_venues(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().lower() for item in value)
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("approved venues must be unique and non-blank")
        return normalized

    @field_validator("max_single_asset_fraction")
    @classmethod
    def validate_asset_cap(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0 or value > Decimal("0.15"):
            raise ValueError("V1 single-asset cap must be in (0, 0.15]")
        return value

    @field_validator("content_hash")
    @classmethod
    def validate_content_hash(cls, value: str) -> str:
        if value and (
            len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError("content_hash must be a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def validate_matrix(self) -> TradingScopePolicy:
        if self.live_asset_class is not ScopeAssetClass.CRYPTO_SPOT:
            raise ValueError("V1 live asset class must remain CRYPTO_SPOT")
        if self.live_symbols != ("BTCUSDT", "ETHUSDT"):
            raise ValueError("V1 live scope must remain BTCUSDT and ETHUSDT")
        if self.live_market_type is not MarketType.SPOT:
            raise ValueError("V1 live scope must remain spot")
        if set(self.live_directions) != {PositionDirection.LONG, PositionDirection.FLAT}:
            raise ValueError("V1 live scope must allow only long and flat directions")
        required_disabled_markets = {
            MarketType.MARGIN,
            MarketType.FUTURES,
            MarketType.PERPETUAL,
            MarketType.OPTIONS,
            MarketType.CFDS,
            MarketType.LEVERAGED_TOKENS,
            MarketType.SYNTHETIC_LEVERAGED_EXPOSURE,
        }
        if not required_disabled_markets.issubset(self.disabled_market_types):
            raise ValueError("V1 disabled market types are incomplete")
        if not set(TradingCapability).issubset(self.disabled_capabilities):
            raise ValueError("V1 disabled capability set is incomplete")
        required_forbidden = {
            ScopeAction.WITHDRAW,
            ScopeAction.WITHDRAW_FUNDS,
            ScopeAction.WITHDRAW_CRYPTO,
            ScopeAction.WITHDRAW_FIAT,
            ScopeAction.EXTERNAL_TRANSFER,
            ScopeAction.TRANSFER_FUNDS,
            ScopeAction.INTERNAL_ACCOUNT_TRANSFER,
            ScopeAction.INITIATE_DEPOSIT,
            ScopeAction.CREATE_PAYMENT_INSTRUCTION,
            ScopeAction.CHANGE_WITHDRAWAL_ADDRESS,
            ScopeAction.WHITELIST_WITHDRAWAL_ADDRESS,
            ScopeAction.MODIFY_WITHDRAWAL_PERMISSIONS,
            ScopeAction.API_KEY_ADMINISTRATION,
            ScopeAction.ACCOUNT_SECURITY_ADMINISTRATION,
            ScopeAction.ROTATE_EXECUTION_CREDENTIALS,
        }
        if not required_forbidden.issubset(self.system_forbidden_actions):
            raise ValueError("V1 system-forbidden action set is incomplete")
        required_gates = {
            ScopeAction.ADD_ASSET,
            ScopeAction.ACTIVATE_NEW_LIVE_INSTRUMENT,
            ScopeAction.ADD_BROKER_OR_VENUE,
            ScopeAction.CHANGE_VENUE,
            ScopeAction.ENABLE_LEVERAGE,
            ScopeAction.PROMOTE_MODEL,
            ScopeAction.PROMOTE_STRATEGY,
            ScopeAction.RELAX_RISK_LIMITS,
            ScopeAction.RESUME_AFTER_HARD_KILL,
        }
        if not required_gates.issubset(self.human_gate_actions):
            raise ValueError("V1 human/technical gate set is incomplete")
        matrix_actions = tuple(entry.action for entry in self.action_matrix)
        if len(set(matrix_actions)) != len(matrix_actions):
            raise ValueError("action matrix entries must be unique")
        required_matrix_actions = {
            ScopeAction.OPEN_BTC_LONG_SPOT,
            ScopeAction.OPEN_ETH_LONG_SPOT,
            ScopeAction.INCREASE_BTC_POSITION,
            ScopeAction.REDUCE_BTC_POSITION,
            ScopeAction.CLOSE_BTC_POSITION,
            ScopeAction.SHORT_BTC,
            ScopeAction.TRADE_SOL,
            ScopeAction.TRADE_EQUITY,
            ScopeAction.TRADE_OPTION,
            ScopeAction.TRADE_FUTURE,
            ScopeAction.USE_MARGIN,
            ScopeAction.INCREASE_LEVERAGE,
            ScopeAction.DECREASE_LEVERAGE,
            ScopeAction.CANCEL_UNSAFE_ORDER,
            ScopeAction.CANCEL_NORMAL_WORKING_ORDER,
            ScopeAction.ADD_BROKER,
            ScopeAction.SWITCH_VENUE,
            ScopeAction.CHANGE_PRODUCTION_ENDPOINT,
            ScopeAction.CHANGE_MODEL,
            ScopeAction.PROMOTE_MODEL,
            ScopeAction.PROMOTE_STRATEGY,
            ScopeAction.ADD_ASSET,
            ScopeAction.CHANGE_DAILY_LOSS_THRESHOLD,
            ScopeAction.CHANGE_POSITION_LIMIT,
            ScopeAction.CHANGE_DRAWDOWN_THRESHOLD,
            ScopeAction.CHANGE_CAPITAL_ALLOCATION,
            ScopeAction.TRANSFER_FUNDS,
            ScopeAction.WITHDRAW_FUNDS,
            ScopeAction.START_RESEARCH_EXPERIMENT,
            ScopeAction.RUN_BACKTEST,
            ScopeAction.RUN_PAPER_STRATEGY,
            ScopeAction.STOP_TRADING_DUE_EMERGENCY,
            ScopeAction.RESUME_AFTER_HARD_KILL,
        }
        if not required_matrix_actions.issubset(matrix_actions):
            raise ValueError("V1 action matrix is incomplete")
        required_credentials = {
            CredentialCapability.MARKET_DATA_READ,
            CredentialCapability.ACCOUNT_READ,
            CredentialCapability.ORDER_READ,
            CredentialCapability.ORDER_CREATE,
            CredentialCapability.ORDER_CANCEL,
        }
        forbidden_credentials = set(self.forbidden_live_credential_capabilities)
        if not required_credentials.issubset(self.required_live_credential_capabilities):
            raise ValueError("live credential read/order requirements are incomplete")
        if forbidden_credentials.intersection(self.required_live_credential_capabilities):
            raise ValueError("required and forbidden live credential capabilities overlap")
        if self.execution_authority:
            raise ValueError("scope policy cannot grant execution authority")
        expected_hash = self._computed_content_hash()
        if self.content_hash and self.content_hash != expected_hash:
            raise ValueError("scope policy content_hash does not match policy content")
        object.__setattr__(self, "content_hash", expected_hash)
        return self

    def _canonical_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"content_hash"})

    def _computed_content_hash(self) -> str:
        return canonical_sha256(self._canonical_payload())

    @property
    def policy_hash(self) -> str:
        return self.content_hash

    def authority_for(self, action: ScopeAction) -> ActionMatrixEntry | None:
        return next((entry for entry in self.action_matrix if entry.action is action), None)


class TradingScopeRequest(BaseModel):
    """A proposed scope action; no field confers execution capability."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    action: ScopeAction
    input_snapshot_hash: str = Field(min_length=64, max_length=64)
    requested_tier: EligibilityTier = EligibilityTier.LIVE_ELIGIBLE
    instrument: str | None = None
    asset_class: ScopeAssetClass | None = None
    market_type: MarketType | None = None
    direction: PositionDirection | None = None
    venue: str | None = None
    venue_approved: bool | None = None
    paper_qualified: bool | None = None
    live_activation_permitted: bool | None = None
    qualification_valid: bool | None = None
    technical_gate_valid: bool | None = None
    formal_evidence_gate_valid: bool | None = None
    model_lifecycle: ModelLifecycle | None = None
    strategy_lifecycle: StrategyLifecycle | None = None
    governance_outcome: DecisionOutcome | None = None
    risk_kernel_approved: bool | None = None
    oms_state_unambiguous: bool | None = None
    deterministic_trigger_valid: bool | None = None
    existing_long_position: bool | None = None
    creates_net_negative_exposure: bool | None = None
    credential_capabilities: CredentialCapabilitySet | None = None
    planned_advisorai_capital: Decimal | None = None
    active_allocation_stage: int | None = None
    authorized_capital_amount: Decimal | None = None
    capital_authorization_valid: bool | None = None
    current_risk_limit: Decimal | None = None
    proposed_risk_limit: Decimal | None = None
    human_authorization: HumanAuthorization | None = None
    evaluated_at: datetime

    @field_validator("input_snapshot_hash")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value):
            raise ValueError("input_snapshot_hash must be a lowercase SHA-256 digest")
        return value

    @field_validator("instrument")
    @classmethod
    def normalize_instrument(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("instrument cannot be blank")
        return normalized

    @field_validator("venue")
    @classmethod
    def normalize_venue(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("venue cannot be blank")
        return normalized

    @field_validator(
        "planned_advisorai_capital",
        "authorized_capital_amount",
        "current_risk_limit",
        "proposed_risk_limit",
    )
    @classmethod
    def validate_decimal_inputs(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and (not value.is_finite() or value < 0):
            raise ValueError("scope decimal inputs must be finite and non-negative")
        return value

    @field_validator("active_allocation_stage")
    @classmethod
    def validate_stage(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("allocation stage cannot be negative")
        return value

    @field_validator("evaluated_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scope evaluation time must include a timezone")
        return value.astimezone(UTC)


class TradingScopeDecision(BaseModel):
    """Immutable scope result; ``execution_authority`` is permanently false."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    action: ScopeAction
    instrument: str | None = None
    asset_class: ScopeAssetClass | None = None
    market_type: MarketType | None = None
    direction: PositionDirection | None = None
    requested_tier: EligibilityTier = EligibilityTier.LIVE_ELIGIBLE
    scope_class: ScopeClass
    authority_class: AuthorityClass
    outcome: ScopeDecisionOutcome
    reason_codes: tuple[ScopeReasonCode, ...]
    technical_gate_valid: bool | None = None
    human_authorization_state: HumanAuthorizationState = HumanAuthorizationState.NOT_REQUIRED
    policy_id: str
    policy_version: str
    policy_hash: str
    input_snapshot_hash: str
    evaluated_at: datetime
    authorization_valid: bool = False
    human_authorization_id: UUID | None = None
    execution_authority: bool = False
    decision_hash: str = ""

    @field_validator("policy_hash", "input_snapshot_hash")
    @classmethod
    def validate_hashes(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("scope decision hashes must be lowercase SHA-256")
        return value

    @field_validator("evaluated_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scope decision time must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def finalize_decision(self) -> TradingScopeDecision:
        if self.execution_authority:
            raise ValueError("scope decisions cannot grant execution authority")
        expected = canonical_sha256(self.model_dump(mode="json", exclude={"decision_hash"}))
        if self.decision_hash and self.decision_hash != expected:
            raise ValueError("scope decision_hash does not match decision content")
        object.__setattr__(self, "decision_hash", expected)
        return self


def _reasons(reasons: list[ScopeReasonCode]) -> tuple[ScopeReasonCode, ...]:
    return tuple(dict.fromkeys(reasons))


def _authorization_state(
    authorization: HumanAuthorization | None,
    *,
    at: datetime,
    expected_action: ScopeAction,
    policy_version: str,
) -> HumanAuthorizationState:
    if authorization is None:
        return HumanAuthorizationState.MISSING
    if authorization.actor_type is not ActorType.HUMAN:
        return HumanAuthorizationState.NON_HUMAN
    if authorization.expires_at is not None and at >= authorization.expires_at:
        return HumanAuthorizationState.EXPIRED
    if not authorization_is_valid(
        authorization,
        at=at,
        action_type=expected_action.value,
        policy_version=policy_version,
    ):
        return HumanAuthorizationState.INVALID
    return HumanAuthorizationState.VALID


def _decision(
    *,
    policy: TradingScopePolicy,
    request: TradingScopeRequest,
    scope_class: ScopeClass,
    authority_class: AuthorityClass,
    outcome: ScopeDecisionOutcome,
    reasons: list[ScopeReasonCode],
    authorization_state: HumanAuthorizationState = HumanAuthorizationState.NOT_REQUIRED,
) -> TradingScopeDecision:
    authorization = request.human_authorization
    return TradingScopeDecision(
        action=request.action,
        instrument=request.instrument,
        asset_class=request.asset_class,
        market_type=request.market_type,
        direction=request.direction,
        requested_tier=request.requested_tier,
        scope_class=scope_class,
        authority_class=authority_class,
        outcome=outcome,
        reason_codes=_reasons(reasons),
        technical_gate_valid=request.technical_gate_valid,
        human_authorization_state=authorization_state,
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        policy_hash=policy.policy_hash,
        input_snapshot_hash=request.input_snapshot_hash,
        evaluated_at=request.evaluated_at,
        authorization_valid=authorization_state is HumanAuthorizationState.VALID,
        human_authorization_id=authorization.authorization_id if authorization else None,
    )


def _human_gate(
    policy: TradingScopePolicy,
    request: TradingScopeRequest,
    reasons: list[ScopeReasonCode],
    *,
    required_action: ScopeAction | None = None,
    require_qualification: bool = False,
    technical_required: bool = True,
) -> TradingScopeDecision:
    expected_action = required_action or request.action
    state = _authorization_state(
        request.human_authorization,
        at=request.evaluated_at,
        expected_action=expected_action,
        policy_version=policy.human_governance_policy_version,
    )
    technical_valid = not technical_required or request.technical_gate_valid is True
    if require_qualification and request.qualification_valid is not True:
        technical_valid = False
        reasons.append(ScopeReasonCode.TECHNICAL_QUALIFICATION_REQUIRED)
    if request.action in {ScopeAction.PROMOTE_MODEL, ScopeAction.PROMOTE_STRATEGY}:
        if request.formal_evidence_gate_valid is not True:
            technical_valid = False
            reasons.append(ScopeReasonCode.FORMAL_EVIDENCE_REQUIRED)
    if state is HumanAuthorizationState.NON_HUMAN:
        reasons.append(ScopeReasonCode.HUMAN_ACTOR_REQUIRED)
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.HUMAN_TECHNICAL_GATE,
            authority_class=AuthorityClass.HUMAN_AND_TECHNICAL_GATE,
            outcome=ScopeDecisionOutcome.HARD_BLOCK,
            reasons=reasons,
            authorization_state=state,
        )
    if state is HumanAuthorizationState.VALID:
        auth_ok = True
    else:
        auth_ok = False
        reasons.append(
            ScopeReasonCode.HUMAN_AUTHORIZATION_EXPIRED
            if state is HumanAuthorizationState.EXPIRED
            else ScopeReasonCode.HUMAN_AUTHORIZATION_REQUIRED
        )
        if request.action is ScopeAction.RESUME_AFTER_HARD_KILL:
            reasons.extend(
                (
                    ScopeReasonCode.HARD_KILL_ACTIVE,
                    ScopeReasonCode.RESUME_AUTHORIZATION_REQUIRED,
                )
            )
    if not technical_valid:
        reasons.append(ScopeReasonCode.TECHNICAL_GATE_REQUIRED)
    if auth_ok and technical_valid:
        reasons.append(ScopeReasonCode.HUMAN_TECHNICAL_GATE_SATISFIED)
        outcome = ScopeDecisionOutcome.ALLOW_HUMAN_GATED
    elif auth_ok:
        outcome = ScopeDecisionOutcome.REQUIRE_TECHNICAL_GATE
    elif technical_valid:
        outcome = ScopeDecisionOutcome.REQUIRE_HUMAN
    else:
        outcome = ScopeDecisionOutcome.REQUIRE_HUMAN_AND_TECHNICAL_GATE
    return _decision(
        policy=policy,
        request=request,
        scope_class=ScopeClass.HUMAN_TECHNICAL_GATE,
        authority_class=AuthorityClass.HUMAN_AND_TECHNICAL_GATE,
        outcome=outcome,
        reasons=reasons,
        authorization_state=state,
    )


def _system_forbidden(
    policy: TradingScopePolicy, request: TradingScopeRequest
) -> TradingScopeDecision:
    if request.action in {
        ScopeAction.WITHDRAW,
        ScopeAction.WITHDRAW_FUNDS,
        ScopeAction.WITHDRAW_CRYPTO,
        ScopeAction.WITHDRAW_FIAT,
        ScopeAction.CHANGE_WITHDRAWAL_ADDRESS,
        ScopeAction.WHITELIST_WITHDRAWAL_ADDRESS,
        ScopeAction.MODIFY_WITHDRAWAL_PERMISSIONS,
    }:
        reason = ScopeReasonCode.WITHDRAWAL_FORBIDDEN
    elif request.action in {
        ScopeAction.EXTERNAL_TRANSFER,
        ScopeAction.TRANSFER_FUNDS,
        ScopeAction.INTERNAL_ACCOUNT_TRANSFER,
        ScopeAction.INITIATE_DEPOSIT,
        ScopeAction.CREATE_PAYMENT_INSTRUCTION,
    }:
        reason = ScopeReasonCode.TRANSFER_FORBIDDEN
    else:
        reason = ScopeReasonCode.SYSTEM_FORBIDDEN_ACTION
    return _decision(
        policy=policy,
        request=request,
        scope_class=ScopeClass.SYSTEM_FORBIDDEN,
        authority_class=AuthorityClass.SYSTEM_FIXED,
        outcome=ScopeDecisionOutcome.SYSTEM_FORBIDDEN,
        reasons=[ScopeReasonCode.SYSTEM_FORBIDDEN_ACTION, reason],
    )


def _protective_decision(
    policy: TradingScopePolicy, request: TradingScopeRequest
) -> TradingScopeDecision:
    reasons = [ScopeReasonCode.PROTECTIVE_ACTION]
    is_order_cancellation = request.action in {
        ScopeAction.CANCEL_UNSAFE_ORDER,
        ScopeAction.CANCEL_NORMAL_WORKING_ORDER,
    }
    is_limit_change = request.action is ScopeAction.CHANGE_POSITION_LIMIT
    if request.deterministic_trigger_valid is not True and (
        request.action is not ScopeAction.CANCEL_NORMAL_WORKING_ORDER and not is_limit_change
    ):
        reasons.append(ScopeReasonCode.PROTECTIVE_TRIGGER_REQUIRED)
    if request.risk_kernel_approved is not True:
        reasons.append(
            ScopeReasonCode.RISK_KERNEL_REJECTED
            if request.risk_kernel_approved is False
            else ScopeReasonCode.RISK_KERNEL_REQUIRED
        )
    if request.oms_state_unambiguous is not True:
        reasons.append(
            ScopeReasonCode.OMS_STATE_AMBIGUOUS
            if request.oms_state_unambiguous is False
            else ScopeReasonCode.OMS_STATE_REQUIRED
        )
    if not is_order_cancellation and not is_limit_change:
        if request.instrument not in policy.live_symbols:
            reasons.append(ScopeReasonCode.INSTRUMENT_NOT_LIVE_AUTHORIZED)
        if request.asset_class is not ScopeAssetClass.CRYPTO_SPOT:
            reasons.append(ScopeReasonCode.ASSET_CLASS_NOT_AUTHORIZED)
        if request.market_type is not MarketType.SPOT:
            reasons.append(ScopeReasonCode.ASSET_CLASS_NOT_AUTHORIZED)
        if request.direction is PositionDirection.SHORT or request.creates_net_negative_exposure:
            reasons.append(ScopeReasonCode.NEGATIVE_EXPOSURE_FORBIDDEN)
        if request.action is ScopeAction.REDUCE_RISK and request.existing_long_position is not True:
            reasons.append(ScopeReasonCode.EXISTING_POSITION_REQUIRED)
        if request.venue_approved is not True:
            reasons.append(ScopeReasonCode.VENUE_NOT_AUTHORIZED)
    if len(reasons) > 1:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.AUTONOMOUS_PROTECTIVE,
            authority_class=AuthorityClass.AUTONOMOUS_RISK_REDUCTION,
            outcome=ScopeDecisionOutcome.HARD_BLOCK,
            reasons=reasons,
        )
    return _decision(
        policy=policy,
        request=request,
        scope_class=ScopeClass.AUTONOMOUS_PROTECTIVE,
        authority_class=AuthorityClass.AUTONOMOUS_RISK_REDUCTION,
        outcome=ScopeDecisionOutcome.ALLOW_WITHIN_GOVERNANCE,
        reasons=reasons,
    )


def _paper_decision(
    policy: TradingScopePolicy, request: TradingScopeRequest
) -> TradingScopeDecision:
    if not policy.paper_enabled or request.paper_qualified is not True:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.PAPER_ELIGIBLE,
            authority_class=AuthorityClass.PAPER_ONLY,
            outcome=ScopeDecisionOutcome.HARD_BLOCK,
            reasons=[ScopeReasonCode.PAPER_QUALIFICATION_REQUIRED],
        )
    if policy.paper_symbols and request.instrument not in policy.paper_symbols:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.PAPER_ELIGIBLE,
            authority_class=AuthorityClass.PAPER_ONLY,
            outcome=ScopeDecisionOutcome.HARD_BLOCK,
            reasons=[ScopeReasonCode.INSTRUMENT_NOT_LIVE_AUTHORIZED],
        )
    return _decision(
        policy=policy,
        request=request,
        scope_class=ScopeClass.PAPER_ELIGIBLE,
        authority_class=AuthorityClass.PAPER_ONLY,
        outcome=ScopeDecisionOutcome.PAPER_ELIGIBLE,
        reasons=[ScopeReasonCode.PAPER_SCOPE_ONLY],
    )


def _capital_reasons(request: TradingScopeRequest) -> list[ScopeReasonCode]:
    reasons: list[ScopeReasonCode] = []
    if request.planned_advisorai_capital is None or request.active_allocation_stage is None:
        reasons.append(ScopeReasonCode.CAPITAL_INPUT_REQUIRED)
    if request.authorized_capital_amount is None or request.capital_authorization_valid is not True:
        reasons.append(ScopeReasonCode.CAPITAL_AUTHORIZATION_REQUIRED)
    if (
        request.planned_advisorai_capital is not None
        and request.authorized_capital_amount is not None
        and request.authorized_capital_amount > request.planned_advisorai_capital
    ):
        reasons.append(ScopeReasonCode.CAPITAL_SCOPE_INVALID)
    return reasons


def _credential_reasons(
    policy: TradingScopePolicy, request: TradingScopeRequest
) -> list[ScopeReasonCode]:
    if request.credential_capabilities is None:
        return [ScopeReasonCode.CREDENTIAL_CAPABILITIES_REQUIRED]
    reasons: list[ScopeReasonCode] = []
    if request.credential_capabilities.missing(policy.required_live_credential_capabilities):
        reasons.append(ScopeReasonCode.CREDENTIAL_CAPABILITY_MISSING)
    if request.credential_capabilities.forbidden(policy.forbidden_live_credential_capabilities):
        reasons.append(ScopeReasonCode.CREDENTIAL_CAPABILITY_FORBIDDEN)
    return reasons


def _disabled_trade_reason(market_type: MarketType) -> ScopeReasonCode:
    return {
        MarketType.MARGIN: ScopeReasonCode.MARGIN_DISABLED,
        MarketType.FUTURES: ScopeReasonCode.FUTURES_DISABLED,
        MarketType.PERPETUAL: ScopeReasonCode.PERPETUALS_DISABLED,
        MarketType.OPTIONS: ScopeReasonCode.OPTIONS_DISABLED,
        MarketType.CFDS: ScopeReasonCode.CFDS_DISABLED,
        MarketType.LEVERAGED_TOKENS: ScopeReasonCode.LEVERAGED_TOKENS_DISABLED,
        MarketType.SYNTHETIC_LEVERAGED_EXPOSURE: ScopeReasonCode.SYNTHETIC_LEVERAGE_DISABLED,
    }.get(market_type, ScopeReasonCode.SCOPE_INPUT_UNKNOWN)


def _evaluate_live_trade(
    policy: TradingScopePolicy, request: TradingScopeRequest
) -> TradingScopeDecision:
    if request.requested_tier is not EligibilityTier.LIVE_ELIGIBLE:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.DISABLED,
            authority_class=AuthorityClass.SYSTEM_FIXED,
            outcome=ScopeDecisionOutcome.HARD_BLOCK,
            reasons=[ScopeReasonCode.PAPER_SCOPE_ONLY],
        )
    missing = [
        ScopeReasonCode.SCOPE_INPUT_UNKNOWN
        for value in (
            request.instrument,
            request.asset_class,
            request.market_type,
            request.direction,
            request.venue,
        )
        if value is None
    ]
    if missing:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.DISABLED,
            authority_class=AuthorityClass.SYSTEM_FIXED,
            outcome=ScopeDecisionOutcome.HARD_BLOCK,
            reasons=missing,
        )
    assert request.instrument is not None
    assert request.asset_class is not None
    assert request.market_type is not None
    assert request.direction is not None
    assert request.venue is not None
    if request.market_type in policy.disabled_market_types:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.DISABLED,
            authority_class=AuthorityClass.SYSTEM_FIXED,
            outcome=ScopeDecisionOutcome.DISABLED,
            reasons=[_disabled_trade_reason(request.market_type)],
        )
    if request.direction is PositionDirection.SHORT:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.DISABLED,
            authority_class=AuthorityClass.SYSTEM_FIXED,
            outcome=ScopeDecisionOutcome.DISABLED,
            reasons=[ScopeReasonCode.SHORTS_DISABLED],
        )
    if request.asset_class is not ScopeAssetClass.CRYPTO_SPOT:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.DISABLED,
            authority_class=AuthorityClass.SYSTEM_FIXED,
            outcome=ScopeDecisionOutcome.HARD_BLOCK,
            reasons=[ScopeReasonCode.ASSET_CLASS_NOT_AUTHORIZED],
        )
    if request.instrument not in policy.live_symbols:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.DISABLED,
            authority_class=AuthorityClass.SYSTEM_FIXED,
            outcome=ScopeDecisionOutcome.HARD_BLOCK,
            reasons=[ScopeReasonCode.INSTRUMENT_NOT_LIVE_AUTHORIZED],
        )
    if request.market_type is not policy.live_market_type:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.DISABLED,
            authority_class=AuthorityClass.SYSTEM_FIXED,
            outcome=ScopeDecisionOutcome.HARD_BLOCK,
            reasons=[ScopeReasonCode.ASSET_CLASS_NOT_AUTHORIZED],
        )
    expected_specific = {
        ScopeAction.OPEN_BTC_LONG_SPOT: ("BTCUSDT", PositionDirection.LONG),
        ScopeAction.OPEN_ETH_LONG_SPOT: ("ETHUSDT", PositionDirection.LONG),
        ScopeAction.INCREASE_BTC_POSITION: ("BTCUSDT", PositionDirection.LONG),
        ScopeAction.INCREASE_ETH_POSITION: ("ETHUSDT", PositionDirection.LONG),
    }.get(request.action)
    if expected_specific and (request.instrument, request.direction) != expected_specific:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.DISABLED,
            authority_class=AuthorityClass.SYSTEM_FIXED,
            outcome=ScopeDecisionOutcome.HARD_BLOCK,
            reasons=[ScopeReasonCode.INSTRUMENT_NOT_LIVE_AUTHORIZED],
        )
    if policy.approved_spot_venues and request.venue not in policy.approved_spot_venues:
        return _human_gate(
            policy,
            request,
            [ScopeReasonCode.VENUE_NOT_AUTHORIZED],
            required_action=ScopeAction.ADD_BROKER_OR_VENUE,
        )
    if request.venue_approved is not True:
        return _human_gate(
            policy,
            request,
            [ScopeReasonCode.VENUE_NOT_APPROVED],
            required_action=ScopeAction.ADD_BROKER_OR_VENUE,
        )
    reasons: list[ScopeReasonCode] = [ScopeReasonCode.LIVE_SPOT_LONG_FLAT]
    if (
        policy.live_activation_permitted is not True
        or request.live_activation_permitted is not True
    ):
        reasons.extend((ScopeReasonCode.LIVE_DISABLED, ScopeReasonCode.LIVE_CAPITAL_DISABLED))
    if request.qualification_valid is not True:
        reasons.append(ScopeReasonCode.QUALIFICATION_REQUIRED)
    if request.governance_outcome is not DecisionOutcome.ALLOW_AUTONOMOUS:
        reasons.append(ScopeReasonCode.GOVERNANCE_DECISION_REQUIRED)
    if request.risk_kernel_approved is not True:
        reasons.append(
            ScopeReasonCode.RISK_KERNEL_REJECTED
            if request.risk_kernel_approved is False
            else ScopeReasonCode.RISK_KERNEL_REQUIRED
        )
    if request.oms_state_unambiguous is not True:
        reasons.append(
            ScopeReasonCode.OMS_STATE_AMBIGUOUS
            if request.oms_state_unambiguous is False
            else ScopeReasonCode.OMS_STATE_REQUIRED
        )
    reasons.extend(_capital_reasons(request))
    reasons.extend(_credential_reasons(policy, request))
    if (
        request.model_lifecycle is not None
        and request.model_lifecycle is not ModelLifecycle.ADMITTED
    ):
        reasons.append(ScopeReasonCode.MODEL_NOT_ADMITTED)
    if (
        request.strategy_lifecycle is not None
        and request.strategy_lifecycle is not StrategyLifecycle.PROMOTED
    ):
        reasons.append(ScopeReasonCode.STRATEGY_NOT_PROMOTED)
    if reasons != [ScopeReasonCode.LIVE_SPOT_LONG_FLAT]:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.LIVE_ELIGIBLE,
            authority_class=AuthorityClass.AUTONOMOUS_WITHIN_LIMITS,
            outcome=ScopeDecisionOutcome.HARD_BLOCK,
            reasons=reasons,
        )
    return _decision(
        policy=policy,
        request=request,
        scope_class=ScopeClass.LIVE_ELIGIBLE,
        authority_class=AuthorityClass.AUTONOMOUS_WITHIN_LIMITS,
        outcome=ScopeDecisionOutcome.ALLOW_WITHIN_GOVERNANCE,
        reasons=reasons,
    )


def _evaluate_position_limit(
    policy: TradingScopePolicy, request: TradingScopeRequest
) -> TradingScopeDecision:
    if request.current_risk_limit is None or request.proposed_risk_limit is None:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.HUMAN_TECHNICAL_GATE,
            authority_class=AuthorityClass.HUMAN_AND_TECHNICAL_GATE,
            outcome=ScopeDecisionOutcome.HARD_BLOCK,
            reasons=[ScopeReasonCode.SCOPE_INPUT_UNKNOWN],
        )
    if request.proposed_risk_limit > policy.max_single_asset_fraction:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.DISABLED,
            authority_class=AuthorityClass.SYSTEM_FIXED,
            outcome=ScopeDecisionOutcome.HARD_BLOCK,
            reasons=[ScopeReasonCode.POSITION_LIMIT],
        )
    if request.proposed_risk_limit <= request.current_risk_limit:
        return _protective_decision(
            policy,
            request.model_copy(
                update={
                    "deterministic_trigger_valid": True,
                    "action": ScopeAction.CHANGE_POSITION_LIMIT,
                }
            ),
        )
    return _human_gate(
        policy,
        request,
        [ScopeReasonCode.RISK_LIMIT_RELAXATION],
        required_action=ScopeAction.RELAX_RISK_LIMITS,
        require_qualification=True,
    )


def evaluate_trading_scope(
    policy: TradingScopePolicy, request: TradingScopeRequest
) -> TradingScopeDecision:
    """Evaluate scope and authority without creating an execution capability."""

    if request.action in policy.system_forbidden_actions:
        return _system_forbidden(policy, request)
    if request.action in {
        ScopeAction.RESEARCH,
        ScopeAction.START_RESEARCH_EXPERIMENT,
        ScopeAction.RUN_BACKTEST,
        ScopeAction.PROPOSE_NEW_INSTRUMENT,
    }:
        if not policy.research_enabled:
            return _decision(
                policy=policy,
                request=request,
                scope_class=ScopeClass.DISABLED,
                authority_class=AuthorityClass.SYSTEM_FIXED,
                outcome=ScopeDecisionOutcome.DISABLED,
                reasons=[ScopeReasonCode.SCOPE_INPUT_UNKNOWN],
            )
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.RESEARCH_ONLY,
            authority_class=AuthorityClass.RESEARCH_ONLY,
            outcome=ScopeDecisionOutcome.RESEARCH_ONLY,
            reasons=[ScopeReasonCode.RESEARCH_SCOPE_ONLY],
        )
    if request.action in {ScopeAction.PAPER_TRADE, ScopeAction.RUN_PAPER_STRATEGY}:
        return _paper_decision(policy, request)
    if request.action in policy.human_gate_actions:
        matrix_entry = policy.authority_for(request.action)
        return _human_gate(
            policy,
            request,
            [],
            require_qualification=request.action
            in {
                ScopeAction.ACTIVATE_NEW_LIVE_INSTRUMENT,
                ScopeAction.PROMOTE_MODEL,
                ScopeAction.PROMOTE_STRATEGY,
            },
            technical_required=(
                matrix_entry is None
                or matrix_entry.authority_class is AuthorityClass.HUMAN_AND_TECHNICAL_GATE
            ),
        )
    if request.action in {
        ScopeAction.REDUCE_RISK,
        ScopeAction.EMERGENCY_PROTECTIVE,
        ScopeAction.STOP_TRADING_DUE_EMERGENCY,
        ScopeAction.CANCEL_UNSAFE_ORDER,
        ScopeAction.CANCEL_NORMAL_WORKING_ORDER,
        ScopeAction.DECREASE_LEVERAGE,
    }:
        return _protective_decision(policy, request)
    if request.action is ScopeAction.CHANGE_POSITION_LIMIT:
        return _evaluate_position_limit(policy, request)
    if request.action in {
        ScopeAction.SHORT_BTC,
        ScopeAction.TRADE_SOL,
        ScopeAction.TRADE_EQUITY,
        ScopeAction.TRADE_OPTION,
        ScopeAction.TRADE_FUTURE,
        ScopeAction.USE_MARGIN,
    }:
        return _decision(
            policy=policy,
            request=request,
            scope_class=ScopeClass.DISABLED,
            authority_class=AuthorityClass.SYSTEM_FIXED,
            outcome=ScopeDecisionOutcome.HARD_BLOCK,
            reasons=[ScopeReasonCode.ASSET_CLASS_NOT_AUTHORIZED],
        )
    if request.action in {
        ScopeAction.TRADE,
        ScopeAction.OPEN_BTC_LONG_SPOT,
        ScopeAction.OPEN_ETH_LONG_SPOT,
        ScopeAction.INCREASE_BTC_POSITION,
        ScopeAction.INCREASE_ETH_POSITION,
    }:
        return _evaluate_live_trade(policy, request)
    return _decision(
        policy=policy,
        request=request,
        scope_class=ScopeClass.DISABLED,
        authority_class=AuthorityClass.SYSTEM_FIXED,
        outcome=ScopeDecisionOutcome.HARD_BLOCK,
        reasons=[ScopeReasonCode.SCOPE_INPUT_UNKNOWN],
    )


def load_trading_scope_policy(path: Path) -> TradingScopePolicy:
    """Load one local, pinned YAML scope policy without network access."""

    with path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"trading scope policy {path} must contain an object")
    return TradingScopePolicy.model_validate(payload)
