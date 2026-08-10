export type Tone = 'positive' | 'info' | 'warning' | 'critical' | 'neutral'

export type DashboardEnvironment = 'paper_testnet' | 'live_locked'

export interface DashboardStatus {
  environment: DashboardEnvironment
  operating_mode: string
  kill_switch: string
  reconciliation: string
  data_freshness_seconds: number
  resource_headroom_gib: number
  last_ledger_event_at: string
  api_state: string
  synthetic: boolean
}

export interface DashboardMetric {
  key: string
  label: string
  value: string
  delta: string
  detail: string
  tone: Tone
}

export interface EquityPoint {
  at: string
  value: number
}

export interface ExposureRow {
  instrument: string
  asset_class: string
  side: string
  notional: string
  weight: string
  pnl: string
  tone: Tone
  mark: string
}

export interface RiskLimitView {
  key: string
  label: string
  used: string
  limit: string
  utilization_pct: number
  state: string
  policy: string
}

export interface DataQualityView {
  dataset: string
  freshness: string
  observations: string
  source_families: string
  state: string
  tone: Tone
  finding: string
}

export interface SourceHealthView {
  source_id: string
  symbol: string
  state: string
  last_event_age_seconds: number | null
  freshness: string
  reconnect_count: number
  sequence_gap_count: number
  disagreement_state: string
  snapshot_recovery_state: string
  actual_provider_identity: string
  fail_closed: boolean
}

export interface IncidentView {
  incident_id: string
  severity: string
  summary: string
  owner: string
  status: string
  opened: string
  tone: Tone
}

export interface MissionView {
  mission_id: string
  title: string
  mode: string
  state: string
  evidence: string
  confidence: string
  expires: string
  dissent: string
  tone: Tone
}

export interface ServiceView {
  name: string
  kind: string
  state: string
  owns: string
  latency: string
  tone: Tone
}

export interface AuditEventView {
  event_id: string
  at: string
  actor: string
  event_type: string
  summary: string
  hash: string
  tone: Tone
}

export interface LiveReadinessView {
  state: string
  passed_checks: number
  total_checks: number
  blockers: string[]
  approval: string
}

export interface DashboardOverview {
  schema_version: string
  as_of: string
  environment: DashboardEnvironment
  synthetic: boolean
  status: DashboardStatus
  metrics: DashboardMetric[]
  equity_curve: EquityPoint[]
  exposures: ExposureRow[]
  risk_limits: RiskLimitView[]
  data_quality: DataQualityView[]
  incidents: IncidentView[]
  missions: MissionView[]
  services: ServiceView[]
  audit: AuditEventView[]
  live_readiness: LiveReadinessView
  source_health: SourceHealthView[]
}

export type CommandKind =
  | 'halt_paper'
  | 'resume_paper'
  | 'set_mode'
  | 'propose_config'
  | 'rollback_config'
  | 'refresh_data'

export interface CommandRequest {
  command: CommandKind
  idempotency_key: string
  reason: string
  confirmed: boolean
  step_up_token?: string
  requested_mode?: string
  config_patch?: Record<string, string>
}

export interface CommandReceipt {
  command_id: string
  command: CommandKind
  status: string
  message: string
  accepted_at: string
  safe_state: string
  audit_event_id: string
  requested_mode?: string | null
  config_patch?: Record<string, string> | null
  config_hash?: string | null
}

export interface AuthStatus {
  auth_required: boolean
  configured: boolean
  authenticated: boolean
  subject: string
}
