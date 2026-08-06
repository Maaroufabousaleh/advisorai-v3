import type { DashboardOverview } from './types'

const now = new Date()
const ago = (minutes: number) => new Date(now.getTime() - minutes * 60_000).toISOString()

export const demoOverview: DashboardOverview = {
  schema_version: 'dashboard.v1',
  as_of: now.toISOString(),
  environment: 'paper_testnet',
  synthetic: true,
  status: {
    environment: 'paper_testnet',
    operating_mode: 'standard',
    kill_switch: 'armed',
    reconciliation: 'clean',
    data_freshness_seconds: 4,
    resource_headroom_gib: 2.3,
    last_ledger_event_at: ago(0.25),
    api_state: 'healthy',
    synthetic: true,
  },
  metrics: [
    { key: 'nav', label: 'Net liquidation', value: '$101,690.00', delta: '+$1,690 / +1.69%', detail: 'Since paper baseline', tone: 'positive' },
    { key: 'pnl', label: 'Session P&L', value: '+$428.16', delta: '+0.42%', detail: 'Mark-to-market', tone: 'positive' },
    { key: 'gross', label: 'Gross exposure', value: '$42,780', delta: '42.1% of NAV', detail: 'Hard limit $100,000', tone: 'info' },
    { key: 'headroom', label: 'Risk headroom', value: '57.9%', delta: 'All checks green', detail: 'Policy risk-v3-core-v1', tone: 'positive' },
  ],
  equity_curve: [100000, 100420, 100180, 100760, 101120, 100940, 101360, 101690].map((value, index) => ({
    at: new Date(now.getTime() - (7 - index) * 3 * 3_600_000).toISOString(),
    value,
  })),
  exposures: [
    { instrument: 'BTC/USDT', asset_class: 'crypto', side: 'LONG', notional: '$18,460', weight: '18.2%', pnl: '+$286.42', tone: 'positive', mark: '$67,420.00' },
    { instrument: 'ETH/USDT', asset_class: 'crypto', side: 'LONG', notional: '$11,980', weight: '11.8%', pnl: '+$98.12', tone: 'positive', mark: '$3,482.10' },
    { instrument: 'SPY', asset_class: 'equity', side: 'LONG', notional: '$7,240', weight: '7.1%', pnl: '+$43.62', tone: 'info', mark: '$524.18' },
    { instrument: 'USD cash', asset_class: 'cash', side: 'FLAT', notional: '$58,910', weight: '58.0%', pnl: '—', tone: 'neutral', mark: '1.0000' },
  ],
  risk_limits: [
    { key: 'gross', label: 'Gross notional', used: '$42,780', limit: '$100,000', utilization_pct: 43, state: 'within limit', policy: 'risk-v3-core-v1' },
    { key: 'order', label: 'Max order notional', used: '$0', limit: '$25,000', utilization_pct: 0, state: 'awaiting order', policy: 'risk-v3-core-v1' },
    { key: 'turnover', label: 'Daily turnover', used: '$18,420', limit: '$50,000', utilization_pct: 37, state: 'within limit', policy: 'risk-v3-core-v1' },
    { key: 'margin', label: 'Margin used', used: '$0', limit: '$50,000', utilization_pct: 0, state: 'no margin', policy: 'risk-v3-core-v1' },
  ],
  data_quality: [
    { dataset: 'market', freshness: '4 sec', observations: '18,240', source_families: 'venue · native', state: 'validated', tone: 'positive', finding: 'Execution-grade feed is current' },
    { dataset: 'macro', freshness: '18 min', observations: '2,864', source_families: 'FRED · BLS', state: 'review', tone: 'warning', finding: 'One vintage availability check pending' },
    { dataset: 'news', freshness: '2 min', observations: '1,192', source_families: 'RSS · GDELT', state: 'validated', tone: 'positive', finding: 'Origin and syndication fields complete' },
  ],
  incidents: [
    { incident_id: 'INC-014', severity: 'medium', summary: 'Macro vintage availability review', owner: 'data-writer', status: 'open', opened: '09:41 UTC', tone: 'warning' },
    { incident_id: 'INC-011', severity: 'low', summary: 'Cold archive verification completed', owner: 'archive-worker', status: 'closed', opened: '08:12 UTC', tone: 'positive' },
  ],
  missions: [
    { mission_id: 'MSN-742', title: 'BTC/USDT regime review', mode: 'standard', state: 'risk approved', evidence: '3 families / 6 artifacts', confidence: '0.80', expires: 'in 52 min', dissent: '1 skeptic note', tone: 'positive' },
    { mission_id: 'MSN-739', title: 'Macro release impact scan', mode: 'deep', state: 'abstained', evidence: '1 family / 2 artifacts', confidence: '0.00', expires: 'expired', dissent: 'insufficient independence', tone: 'warning' },
  ],
  services: [
    { name: 'advisor-api', kind: 'always on', state: 'healthy', owns: 'mission routing · approval boundary', latency: '18 ms', tone: 'positive' },
    { name: 'market-node', kind: 'always on', state: 'healthy', owns: 'events · RiskKernel · OMS', latency: '6 ms', tone: 'positive' },
    { name: 'collector-node', kind: 'always on', state: 'degraded', owns: 'raw market · source health', latency: '220 ms', tone: 'warning' },
    { name: 'resource-governor', kind: 'always on', state: 'healthy', owns: 'admission · load shedding', latency: '4 ms', tone: 'positive' },
  ],
  audit: [
    { event_id: 'evt-9f3a', at: ago(0.25), actor: 'system', event_type: 'risk_snapshot', summary: 'Risk snapshot validated against risk-v3-core-v1', hash: '87a1d0487d4b', tone: 'positive' },
    { event_id: 'evt-71c2', at: ago(3), actor: 'collector-node', event_type: 'quality_finding', summary: 'Macro vintage availability review pending', hash: '5cb6ab32b7c0', tone: 'warning' },
    { event_id: 'evt-42bd', at: ago(9), actor: 'owner', event_type: 'mission_reviewed', summary: 'Standard mode council reviewed BTC/USDT state', hash: '92fe20ad7c81', tone: 'info' },
  ],
  live_readiness: {
    state: 'paper only',
    passed_checks: 3,
    total_checks: 8,
    blockers: ['Phase 7 paper soak evidence not admitted', 'Explicit human Phase 10 authorization missing', 'Live venue credentials are disabled'],
    approval: 'sealed by Phase 10 guard',
  },
}
