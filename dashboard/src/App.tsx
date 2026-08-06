import { useEffect, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  BarChart3,
  BookOpenCheck,
  Boxes,
  Check,
  ChevronRight,
  CircleDollarSign,
  ClipboardList,
  Cpu,
  Database,
  FileClock,
  Gauge,
  HardDrive,
  KeyRound,
  LayoutDashboard,
  LineChart,
  LockKeyhole,
  LogOut,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  PauseCircle,
  PlayCircle,
  Radio,
  RefreshCw,
  Search,
  ServerCog,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Wifi,
  X,
  Zap,
  type LucideIcon,
} from 'lucide-react'
import { dashboardApi } from './api'
import { demoOverview } from './demoData'
import type {
  AuthStatus,
  CommandKind,
  CommandRequest,
  DashboardOverview,
  DashboardMetric,
  Tone,
} from './types'
import './styles.css'

type Section = 'overview' | 'missions' | 'portfolio' | 'risk' | 'data' | 'system' | 'incidents' | 'audit' | 'settings'

interface NavItem {
  id: Section
  label: string
  hint: string
  icon: LucideIcon
}

const navItems: NavItem[] = [
  { id: 'overview', label: 'Overview', hint: 'Control room', icon: LayoutDashboard },
  { id: 'missions', label: 'Missions', hint: 'Evidence councils', icon: Sparkles },
  { id: 'portfolio', label: 'Portfolio', hint: 'Paper execution', icon: CircleDollarSign },
  { id: 'risk', label: 'Risk & limits', hint: 'Deterministic gate', icon: ShieldCheck },
  { id: 'data', label: 'Data & models', hint: 'Point-in-time spine', icon: Database },
  { id: 'system', label: 'System health', hint: 'Services & resources', icon: ServerCog },
  { id: 'incidents', label: 'Incidents', hint: 'Recovery runbooks', icon: AlertTriangle },
  { id: 'audit', label: 'Audit trail', hint: 'Immutable ledger', icon: ClipboardList },
  { id: 'settings', label: 'Settings', hint: 'Guarded controls', icon: Settings2 },
]

function toneClass(tone: Tone) {
  return `tone-${tone}`
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: 'UTC' }).format(new Date(value))
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC' }).format(new Date(value))
}

interface StepUpCredentials {
  password: string
  totpCode: string
}

function requiresStepUp(command: CommandKind) {
  return ['halt_paper', 'resume_paper', 'set_mode', 'propose_config', 'rollback_config'].includes(command)
}

function App() {
  const [section, setSection] = useState<Section>('overview')
  const [data, setData] = useState<DashboardOverview>(demoOverview)
  const [source, setSource] = useState<'live' | 'synthetic'>('synthetic')
  const [loading, setLoading] = useState(true)
  const [collapsed, setCollapsed] = useState(false)
  const [mobileNav, setMobileNav] = useState(false)
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null)
  const [showLogin, setShowLogin] = useState(false)
  const [modal, setModal] = useState<CommandKind | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const loadData = async () => {
    setRefreshing(true)
    try {
      const status = await dashboardApi.status()
      setAuthStatus(status)
      if (status.auth_required && !status.authenticated) {
        setShowLogin(true)
        setLoading(false)
        setRefreshing(false)
        return
      }
      const overview = await dashboardApi.overview()
      setData(overview)
      setSource(overview.synthetic ? 'synthetic' : 'live')
    } catch (error) {
      const status = (error as Error & { status?: number }).status
      if (status === 401) setShowLogin(true)
      setSource('synthetic')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  useEffect(() => {
    void loadData()
  }, [])

  useEffect(() => {
    if (!toast) return
    const timeout = window.setTimeout(() => setToast(null), 4500)
    return () => window.clearTimeout(timeout)
  }, [toast])

  const execute = async (
    command: CommandKind,
    reason: string,
    requestedMode?: string,
    configPatch?: Record<string, string>,
    stepUpCredentials?: StepUpCredentials,
  ) => {
    try {
      let stepUpToken: string | undefined
      if (requiresStepUp(command)) {
        if (authStatus?.auth_required) {
          if (!stepUpCredentials) throw new Error('Step-up credentials are required for this command.')
          const stepUp = await dashboardApi.stepUp(stepUpCredentials.password, stepUpCredentials.totpCode)
          stepUpToken = stepUp.step_up_token
        } else {
          stepUpToken = crypto.randomUUID()
        }
      }
      const payload: CommandRequest = {
        command,
        idempotency_key: `${command}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
        reason,
        confirmed: true,
        ...(stepUpToken ? { step_up_token: stepUpToken } : {}),
        ...(requestedMode ? { requested_mode: requestedMode } : {}),
        ...(configPatch ? { config_patch: configPatch } : {}),
      }
      const receipt = await dashboardApi.command(payload)
      setToast(receipt.message)
      setModal(null)
      await loadData()
    } catch (error) {
      const status = (error as Error & { status?: number }).status
      if (status === 401) setShowLogin(true)
      const detail = error instanceof Error ? error.message : 'command rejected'
      setToast(status ? `Command rejected: ${detail}` : `API unavailable; no command was applied. ${detail}`)
      setModal(null)
    }
  }

  const handleLogin = async (password: string, totpCode: string) => {
    const result = await dashboardApi.login(password, totpCode)
    sessionStorage.setItem('advisorai_csrf', result.csrf_token)
    setShowLogin(false)
    setToast(`Secure session established for ${result.subject}.`)
    await loadData()
  }

  const handleLogout = async () => {
    if (authStatus?.auth_required !== true) {
      setToast('Local development mode does not use sign-out.')
      return
    }
    await dashboardApi.logout().catch(() => undefined)
    sessionStorage.removeItem('advisorai_csrf')
    setShowLogin(true)
  }

  if (showLogin) {
    return <LoginScreen onLogin={handleLogin} configured={authStatus?.configured ?? true} />
  }

  const active = navItems.find((item) => item.id === section) ?? navItems[0]

  return (
    <div className={`app-shell ${collapsed ? 'nav-collapsed' : ''}`}>
      <aside className={`sidebar ${mobileNav ? 'mobile-open' : ''}`}>
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true"><span /><span /><span /></div>
          <div className="brand-copy"><span>ADVISOR</span><strong>AI / V3</strong></div>
          <button className="icon-button mobile-close" aria-label="Close navigation" onClick={() => setMobileNav(false)}><X size={16} /></button>
        </div>
        <div className="sidebar-rule" />
        <nav aria-label="Primary navigation">
          <span className="nav-kicker">OPERATING SURFACES</span>
          {navItems.map((item) => {
            const Icon = item.icon
            return (
              <button
                key={item.id}
                className={`nav-item ${section === item.id ? 'active' : ''}`}
                onClick={() => { setSection(item.id); setMobileNav(false) }}
                aria-current={section === item.id ? 'page' : undefined}
              >
                <Icon size={17} strokeWidth={1.8} />
                <span className="nav-label"><strong>{item.label}</strong><small>{item.hint}</small></span>
                {section === item.id && <span className="nav-active-line" />}
              </button>
            )
          })}
        </nav>
        <div className="sidebar-footer">
          <div className="footer-resource"><span className="signal-dot tone-positive" /> <span>Resource governor</span><strong>{data.status.resource_headroom_gib.toFixed(1)} GB</strong></div>
          <div className="footer-resource"><span className="signal-dot tone-info" /> <span>Ledger</span><strong>WAL / live</strong></div>
          <button className="collapse-button" onClick={() => setCollapsed((value) => !value)} aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}>
            {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}<span>{collapsed ? 'Expand rail' : 'Collapse rail'}</span>
          </button>
        </div>
      </aside>
      <div className="main-column">
        <header className="topbar">
          <div className="topbar-start">
            <button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setMobileNav(true)}><Menu size={18} /></button>
            <div className="breadcrumb"><span className="breadcrumb-root">V3 CONTROL ROOM</span><ChevronRight size={14} /><span>{active.label.toUpperCase()}</span></div>
          </div>
          <div className="topbar-state">
            <div className="state-cluster"><span className="signal-dot tone-positive" /><span>API {data.status.api_state}</span></div>
            <div className="state-cluster"><Wifi size={14} /><span>{data.status.data_freshness_seconds}s feed</span></div>
            <div className="state-cluster"><span className={`signal-dot ${data.status.kill_switch === 'armed' ? 'tone-positive' : 'tone-critical'}`} /><span>Kill {data.status.kill_switch}</span></div>
            <button className="operator-button" onClick={handleLogout} aria-label="Sign out"><span className="avatar">O</span><span>OWNER</span><LogOut size={14} /></button>
          </div>
        </header>
        <div className="environment-strip">
          <div className="environment-label"><span className="strip-pulse" /><strong>PAPER / TESTNET</strong><span className="strip-divider" /> <span>Live capital is sealed</span></div>
          <div className="environment-meta"><span>MODE <strong>{data.status.operating_mode.toUpperCase()}</strong></span><span>AS OF <strong>{formatTime(data.as_of)} UTC</strong></span><span className="synthetic-tag">{source === 'synthetic' ? 'SYNTHETIC SNAPSHOT' : 'LIVE PROJECTION'}</span></div>
        </div>
        <main className="workspace">
          {loading ? <LoadingState /> : section === 'overview' ? <Overview data={data} onCommand={setModal} onNavigate={setSection} /> : <WorkspaceView section={section} data={data} onCommand={setModal} onNavigate={setSection} />}
        </main>
      </div>
      <div className="quick-actions">
        <button className="quick-action refresh" onClick={() => void loadData()} disabled={refreshing} aria-label="Refresh dashboard"><RefreshCw size={16} className={refreshing ? 'spin' : ''} /></button>
        {data.status.kill_switch === 'armed' ? <button className="quick-action halt" onClick={() => setModal('halt_paper')}><PauseCircle size={17} /><span>HALT PAPER</span></button> : <button className="quick-action resume" onClick={() => setModal('resume_paper')}><PlayCircle size={17} /><span>RESUME PAPER</span></button>}
      </div>
      {modal && <CommandModal command={modal} currentMode={data.status.operating_mode} protectedMode={authStatus?.auth_required ?? false} onClose={() => setModal(null)} onConfirm={execute} />}
      {toast && <div className="toast" role="status"><Check size={16} /><span>{toast}</span><button onClick={() => setToast(null)} aria-label="Dismiss notification"><X size={14} /></button></div>}
    </div>
  )
}

function LoadingState() {
  return <div className="loading-state"><div className="loading-mark"><Activity size={24} /></div><span>Reading authoritative state…</span></div>
}

function LoginScreen({ onLogin, configured }: { onLogin: (password: string, totpCode: string) => Promise<void>; configured: boolean }) {
  const [password, setPassword] = useState('')
  const [totpCode, setTotpCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try { await onLogin(password, totpCode) } catch (loginError) { setError((loginError as Error).message); setBusy(false) }
  }
  return (
    <div className="login-shell">
      <div className="login-panel">
        <div className="brand-lockup login-brand"><div className="brand-mark" aria-hidden="true"><span /><span /><span /></div><div className="brand-copy"><span>ADVISOR</span><strong>AI / V3</strong></div></div>
        <div className="login-rule" />
        <div className="login-icon"><LockKeyhole size={23} /></div>
        <h1>Operator authentication</h1>
        <p className="login-copy">This control room sits behind a step-up boundary. Use your owner credentials and current authenticator code.</p>
        {!configured && <div className="login-warning"><AlertTriangle size={16} /><span>Authentication is not configured on the API. Set the dashboard password hash and TOTP secret before using controls.</span></div>}
        <form onSubmit={submit}>
          <label>Password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" required /></label>
          <label>Authenticator code<input inputMode="numeric" pattern="[0-9]{6}" maxLength={6} value={totpCode} onChange={(event) => setTotpCode(event.target.value)} autoComplete="one-time-code" required /></label>
          {error && <div className="form-error" role="alert"><ShieldAlert size={15} />{error}</div>}
          <button className="primary-button login-submit" disabled={busy || !configured}>{busy ? 'VERIFYING…' : 'ENTER CONTROL ROOM'}<ChevronRight size={16} /></button>
        </form>
        <div className="login-foot"><ShieldCheck size={14} /><span>Sessions are short-lived, CSRF-bound, and audit logged.</span></div>
      </div>
    </div>
  )
}

function Overview({ data, onCommand, onNavigate }: { data: DashboardOverview; onCommand: (command: CommandKind) => void; onNavigate: (section: Section) => void }) {
  return (
    <div className="page page-overview">
      <section className="hero-row">
        <div className="hero-copy">
          <div className="system-eyebrow"><span className="signal-dot tone-info" /> OWNER OPERATING CONSOLE <span className="system-eyebrow-divider" /> {data.status.reconciliation.toUpperCase()} RECONCILIATION</div>
          <h1>One spine.<br /><em>Every decision visible.</em></h1>
          <p>Federated evidence, deterministic risk, paper execution, and recovery state in one auditable field of view.</p>
          <div className="hero-actions"><button className="primary-button" onClick={() => onNavigate('missions')}><Sparkles size={16} /> REVIEW ACTIVE MISSIONS <ChevronRight size={15} /></button><button className="text-button" onClick={() => onNavigate('audit')}>OPEN LEDGER <ArrowUpRight size={15} /></button></div>
        </div>
        <LiveGate gate={data.live_readiness} onInspect={() => onNavigate('risk')} />
      </section>
      <section className="metrics-strip" aria-label="Portfolio summary">
        {data.metrics.map((metric) => <Metric metric={metric} key={metric.key} />)}
      </section>
      <section className="overview-grid">
        <EquityPanel data={data} />
        <RiskPanel data={data} onNavigate={onNavigate} />
      </section>
      <MissionBoard data={data} onNavigate={onNavigate} />
      <section className="lower-grid">
        <ExposurePanel data={data} onNavigate={onNavigate} />
        <QualityPanel data={data} onNavigate={onNavigate} />
        <ServicePanel data={data} onNavigate={onNavigate} />
      </section>
      <section className="audit-ribbon">
        <div className="ribbon-heading"><div className="section-kicker"><FileClock size={14} /> LATEST LEDGER ACTIVITY</div><button className="text-button" onClick={() => onNavigate('audit')}>VIEW ALL <ChevronRight size={14} /></button></div>
        <div className="audit-list">{data.audit.slice(0, 3).map((event) => <AuditLine event={event} key={event.event_id} />)}</div>
      </section>
    </div>
  )
}

function WorkspaceView({ section, data, onCommand, onNavigate }: { section: Section; data: DashboardOverview; onCommand: (command: CommandKind) => void; onNavigate: (section: Section) => void }) {
  const meta = navItems.find((item) => item.id === section) ?? navItems[0]
  const Icon = meta.icon
  return (
    <div className="page workspace-page">
      <div className="workspace-heading">
        <div><div className="system-eyebrow"><Icon size={14} /> {meta.hint.toUpperCase()}</div><h1>{meta.label}</h1><p>{workspaceDescription(section)}</p></div>
        <div className="workspace-actions"><button className="secondary-button" onClick={() => onCommand('refresh_data')}><RefreshCw size={15} /> REFRESH STATE</button>{section === 'settings' && <button className="danger-outline" onClick={() => onCommand('halt_paper')}><PauseCircle size={15} /> HALT PAPER</button>}</div>
      </div>
      {section === 'missions' && <MissionWorkspace data={data} onNavigate={onNavigate} />}
      {section === 'portfolio' && <PortfolioWorkspace data={data} onNavigate={onNavigate} />}
      {section === 'risk' && <RiskWorkspace data={data} onCommand={onCommand} />}
      {section === 'data' && <DataWorkspace data={data} />}
      {section === 'system' && <SystemWorkspace data={data} />}
      {section === 'incidents' && <IncidentWorkspace data={data} />}
      {section === 'audit' && <AuditWorkspace data={data} />}
      {section === 'settings' && <SettingsWorkspace data={data} onCommand={onCommand} />}
    </div>
  )
}

function workspaceDescription(section: Section) {
  const descriptions: Record<Section, string> = {
    overview: 'The authoritative operating picture.',
    missions: 'Inspect why a recommendation exists, where it is weak, and when it expires.',
    portfolio: 'See target state, marks, paper fills, and attribution without leaving the ledger boundary.',
    risk: 'Hard limits and gate outcomes owned by deterministic controls.',
    data: 'Point-in-time availability, source families, models, and quality findings.',
    system: 'Service ownership, measured resources, and load-shedding state.',
    incidents: 'Containment, reconciliation, runbooks, and corrective-test links.',
    audit: 'Append-only evidence for every mission, decision, control, and approval.',
    settings: 'Guarded configuration proposals and local security posture.',
  }
  return descriptions[section]
}

function Metric({ metric }: { metric: DashboardMetric }) {
  const positive = metric.delta.startsWith('+')
  return <div className={`metric ${toneClass(metric.tone)}`}><span className="metric-label">{metric.label}</span><strong>{metric.value}</strong><div className="metric-detail"><span className={positive ? 'delta-up' : ''}>{positive && <ArrowUpRight size={13} />}{metric.delta}</span><span>{metric.detail}</span></div></div>
}

function LiveGate({ gate, onInspect }: { gate: DashboardOverview['live_readiness']; onInspect: () => void }) {
  return <aside className="live-gate panel"><div className="gate-top"><div className="section-kicker"><LockKeyhole size={14} /> PHASE 10 GATE</div><span className="locked-label">LOCKED</span></div><div className="gate-icon"><LockKeyhole size={24} /></div><h2>Live capital sealed.</h2><p>Paper control is active. Every live-readiness blocker is explicit and reviewable.</p><div className="gate-progress"><div className="gate-progress-top"><span>Readiness checks</span><strong>{gate.passed_checks}/{gate.total_checks}</strong></div><div className="segments">{Array.from({ length: gate.total_checks }, (_, index) => <span key={index} className={index < gate.passed_checks ? 'filled' : ''} />)}</div></div><button className="gate-link" onClick={onInspect}>INSPECT BLOCKERS <ArrowUpRight size={14} /></button></aside>
}

function EquityPanel({ data }: { data: DashboardOverview }) {
  const values = data.equity_curve.map((point) => point.value)
  const min = Math.min(...values) - 220
  const max = Math.max(...values) + 220
  const points = data.equity_curve.map((point, index) => `${(index / (data.equity_curve.length - 1)) * 100},${100 - ((point.value - min) / (max - min)) * 100}`).join(' ')
  return <section className="panel chart-panel"><div className="panel-heading"><div><div className="section-kicker"><LineChart size={14} /> PAPER EQUITY</div><h2>Balance trajectory</h2></div><div className="panel-heading-meta"><span className="live-dot" /> MARKED <strong>3H RANGE</strong></div></div><div className="chart-wrap"><div className="chart-y-labels"><span>$102K</span><span>$101K</span><span>$100K</span></div><svg viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="Paper equity trajectory rising from one hundred thousand dollars to one hundred one thousand six hundred ninety dollars"><line x1="0" y1="18" x2="100" y2="18" /><line x1="0" y1="50" x2="100" y2="50" /><line x1="0" y1="82" x2="100" y2="82" /><polyline points={points} className="chart-line" /><circle cx="100" cy={points.split(' ').at(-1)?.split(',')[1]} r="1.8" className="chart-point" /></svg></div><div className="chart-axis"><span>{formatTime(data.equity_curve[0].at)}</span><span>{formatTime(data.equity_curve.at(-1)?.at ?? data.as_of)}</span></div><div className="chart-foot"><span><i className="chart-key positive" /> Equity mark</span><span>Snapshot <strong>{formatTime(data.as_of)} UTC</strong></span></div></section>
}

function RiskPanel({ data, onNavigate }: { data: DashboardOverview; onNavigate: (section: Section) => void }) {
  const maxUtilization = data.risk_limits.length
    ? Math.max(...data.risk_limits.map((limit) => limit.utilization_pct))
    : 0
  const hasBreach = data.risk_limits.some((limit) =>
    limit.utilization_pct >= 100 || /reject|breach|exceed|blocked/i.test(limit.state),
  )
  const riskTone: Tone = hasBreach ? 'critical' : maxUtilization >= 80 ? 'warning' : 'positive'
  const summary = hasBreach
    ? 'Risk gate blocked'
    : maxUtilization >= 80
      ? 'Limit requires review'
      : 'All hard limits within policy'
  const headroom = Math.max(0, 100 - maxUtilization)
  return <section className="panel risk-panel"><div className="panel-heading"><div><div className="section-kicker"><Gauge size={14} /> RISK KERNEL</div><h2>Limit utilization</h2></div><button className="icon-link" onClick={() => onNavigate('risk')} aria-label="Open risk limits"><ArrowUpRight size={15} /></button></div><div className="risk-summary"><div className="risk-score"><span>HEADROOM</span><strong className={toneClass(riskTone)}>{headroom}<small>%</small></strong><em className={toneClass(riskTone)}>{hasBreach ? 'policy blocked' : maxUtilization >= 80 ? 'review required' : 'policy pass'}</em></div><div className="risk-summary-copy"><span className={`status-line ${toneClass(riskTone)}`}><span className={`signal-dot ${toneClass(riskTone)}`} /><span>{summary}</span></span><p>AI cannot loosen limits. Current state hash is bound to the risk decision.</p></div></div><div className="risk-bars">{data.risk_limits.slice(0, 3).map((limit) => <RiskBar limit={limit} key={limit.key} />)}</div><button className="panel-footer-link" onClick={() => onNavigate('risk')}>OPEN FULL POLICY <ChevronRight size={14} /></button></section>
}

function RiskBar({ limit }: { limit: DashboardOverview['risk_limits'][number] }) {
  return <div className="risk-bar"><div className="risk-bar-label"><span>{limit.label}</span><strong>{limit.utilization_pct}%</strong></div><div className="bar-track"><span style={{ width: `${Math.max(limit.utilization_pct, 1)}%` }} className={limit.utilization_pct > 80 ? 'warning' : ''} /></div><div className="risk-bar-meta"><span>{limit.used}</span><span>{limit.limit}</span></div></div>
}

function MissionBoard({ data, onNavigate }: { data: DashboardOverview; onNavigate: (section: Section) => void }) {
  return <section className="panel mission-board"><div className="panel-heading board-heading"><div><div className="section-kicker"><Radio size={14} /> DECISION BOARD / LIVE</div><h2>Mission runway</h2></div><div className="board-heading-actions"><span className="board-count">{data.missions.length} ACTIVE THREADS</span><button className="text-button" onClick={() => onNavigate('missions')}>OPEN MISSIONS <ChevronRight size={14} /></button></div></div><div className="board-table" role="table" aria-label="Active research missions"><div className="board-row board-header" role="row"><span>MISSION</span><span>MODE</span><span>STATE</span><span>EVIDENCE</span><span>CONFIDENCE</span><span>EXPIRY</span><span /></div>{data.missions.map((mission) => <div className="board-row" role="row" key={mission.mission_id}><span className="mission-cell"><span className={`mini-signal ${toneClass(mission.tone)}`} /><strong>{mission.mission_id}</strong><em>{mission.title}</em></span><span className="flap-cell">{mission.mode.toUpperCase()}</span><span><StatusPill tone={mission.tone} label={mission.state} /></span><span className="data-cell">{mission.evidence}</span><span className="confidence-cell">{mission.confidence}</span><span className="data-cell">{mission.expires}</span><button className="row-arrow" aria-label={`Open ${mission.mission_id}`} onClick={() => onNavigate('missions')}><ChevronRight size={15} /></button></div>)}</div></section>
}

function ExposurePanel({ data, onNavigate }: { data: DashboardOverview; onNavigate: (section: Section) => void }) {
  return <section className="panel table-panel"><div className="panel-heading"><div><div className="section-kicker"><BarChart3 size={14} /> POSITION REGISTER</div><h2>Exposure at mark</h2></div><button className="icon-link" onClick={() => onNavigate('portfolio')} aria-label="Open portfolio"><ArrowUpRight size={15} /></button></div><div className="compact-table"><div className="compact-row compact-head"><span>ASSET</span><span>WEIGHT</span><span>P&L</span></div>{data.exposures.slice(0, 4).map((row) => <div className="compact-row" key={row.instrument}><span className="asset-cell"><strong>{row.instrument}</strong><small>{row.side} · {row.asset_class}</small></span><span>{row.weight}</span><span className={toneClass(row.tone)}>{row.pnl}</span></div>)}</div><button className="panel-footer-link" onClick={() => onNavigate('portfolio')}>OPEN PORTFOLIO <ChevronRight size={14} /></button></section>
}

function QualityPanel({ data, onNavigate }: { data: DashboardOverview; onNavigate: (section: Section) => void }) {
  return <section className="panel table-panel"><div className="panel-heading"><div><div className="section-kicker"><Database size={14} /> DATA SPINE</div><h2>Quality signals</h2></div><button className="icon-link" onClick={() => onNavigate('data')} aria-label="Open data and models"><ArrowUpRight size={15} /></button></div><div className="quality-list">{data.data_quality.map((row) => <div className="quality-row" key={row.dataset}><span className={`quality-icon ${toneClass(row.tone)}`}>{row.tone === 'positive' ? <Check size={13} /> : <AlertTriangle size={13} />}</span><span className="quality-copy"><strong>{row.dataset}</strong><small>{row.source_families}</small></span><span className="quality-state"><strong>{row.freshness}</strong><small>{row.state}</small></span></div>)}</div><button className="panel-footer-link" onClick={() => onNavigate('data')}>INSPECT LINEAGE <ChevronRight size={14} /></button></section>
}

function ServicePanel({ data, onNavigate }: { data: DashboardOverview; onNavigate: (section: Section) => void }) {
  return <section className="panel table-panel"><div className="panel-heading"><div><div className="section-kicker"><ServerCog size={14} /> SERVICE FABRIC</div><h2>Always-on health</h2></div><button className="icon-link" onClick={() => onNavigate('system')} aria-label="Open system health"><ArrowUpRight size={15} /></button></div><div className="service-list">{data.services.map((service) => <div className="service-row" key={service.name}><span className={`service-dot ${toneClass(service.tone)}`} /><span className="service-copy"><strong>{service.name}</strong><small>{service.owns}</small></span><span className="service-latency">{service.latency}</span></div>)}</div><button className="panel-footer-link" onClick={() => onNavigate('system')}>OPEN SERVICE MAP <ChevronRight size={14} /></button></section>
}

function AuditLine({ event }: { event: DashboardOverview['audit'][number] }) {
  return <div className="audit-line"><span className={`audit-mark ${toneClass(event.tone)}`}><Activity size={13} /></span><span className="audit-time">{formatTime(event.at)}</span><span className="audit-type">{event.event_type}</span><span className="audit-summary">{event.summary}</span><span className="audit-hash">#{event.hash.slice(0, 8)}</span></div>
}

function StatusPill({ tone, label }: { tone: Tone; label: string }) { return <span className={`status-pill ${toneClass(tone)}`}><span />{label}</span> }

function MissionWorkspace({ data, onNavigate }: { data: DashboardOverview; onNavigate: (section: Section) => void }) {
  return <div className="workspace-stack"><div className="feature-strip"><div className="feature-icon"><Sparkles size={20} /></div><div><strong>Evidence before action.</strong><span>Each mission expires, records dissent, and must pass source-family and factor-family gates before risk evaluation.</span></div><button className="text-button" onClick={() => onNavigate('risk')}>SEE GATE RULES <ArrowUpRight size={14} /></button></div><MissionBoard data={data} onNavigate={onNavigate} /><div className="detail-grid">{data.missions.map((mission) => <article className="detail-panel" key={mission.mission_id}><div className="detail-panel-top"><span className="section-kicker">{mission.mission_id}</span><StatusPill tone={mission.tone} label={mission.state} /></div><h2>{mission.title}</h2><div className="detail-facts"><span><small>MODE</small><strong>{mission.mode}</strong></span><span><small>CONFIDENCE</small><strong>{mission.confidence}</strong></span><span><small>EXPIRES</small><strong>{mission.expires}</strong></span></div><div className="evidence-line"><BookOpenCheck size={15} /><span>{mission.evidence}</span><em>{mission.dissent}</em></div><button className="secondary-button wide-button" onClick={() => onNavigate('audit')}>OPEN DECISION BUNDLE <ArrowUpRight size={14} /></button></article>)}</div></div>
}

function PortfolioWorkspace({ data, onNavigate }: { data: DashboardOverview; onNavigate: (section: Section) => void }) {
  return <div className="workspace-stack"><div className="portfolio-summary"><Metric metric={data.metrics[0]} /><Metric metric={data.metrics[1]} /><div className="portfolio-callout"><div className="section-kicker"><ShieldCheck size={14} /> EXECUTION BOUNDARY</div><strong>Paper / testnet only</strong><span>Target portfolios never become orders without RiskKernel approval.</span><button className="text-button" onClick={() => onNavigate('risk')}>VIEW POLICY <ArrowUpRight size={14} /></button></div></div><section className="panel wide-table"><div className="panel-heading"><div><div className="section-kicker"><BarChart3 size={14} /> POSITION REGISTER</div><h2>All positions at authoritative mark</h2></div><span className="panel-heading-meta">{data.exposures.length} LINES · {formatTime(data.as_of)} UTC</span></div><div className="data-table"><div className="data-table-row table-head"><span>INSTRUMENT</span><span>SIDE</span><span>MARK</span><span>NOTIONAL</span><span>WEIGHT</span><span>P&L</span><span>STATE</span></div>{data.exposures.map((row) => <div className="data-table-row" key={row.instrument}><span><strong>{row.instrument}</strong><small>{row.asset_class}</small></span><span className="mono">{row.side}</span><span className="mono">{row.mark}</span><span className="mono">{row.notional}</span><span className="mono">{row.weight}</span><span className={toneClass(row.tone)}>{row.pnl}</span><span><StatusPill tone={row.tone} label="marked" /></span></div>)}</div></section></div>
}

function RiskWorkspace({ data, onCommand }: { data: DashboardOverview; onCommand: (command: CommandKind) => void }) {
  return <div className="workspace-stack"><div className="risk-banner"><ShieldCheck size={22} /><div><strong>Deterministic veto path is healthy.</strong><span>Policy <code>risk-v3-core-v1</code> · AI cannot loosen limits · kill switch independent</span></div><button className="danger-outline" onClick={() => onCommand('halt_paper')}><PauseCircle size={15} /> EMERGENCY HALT</button></div><div className="limits-grid">{data.risk_limits.map((limit) => <article className="limit-tile" key={limit.key}><div className="limit-tile-head"><span>{limit.label}</span><Gauge size={16} /></div><strong>{limit.used}</strong><span className="limit-of">of {limit.limit}</span><RiskBar limit={limit} /><small>{limit.state} · {limit.policy}</small></article>)}</div><section className="panel policy-panel"><div className="panel-heading"><div><div className="section-kicker"><SlidersHorizontal size={14} /> POLICY MATERIAL</div><h2>Non-negotiable controls</h2></div><span className="locked-label">READ ONLY</span></div><div className="policy-list"><PolicyRow label="Stale data" value="reject" /><PolicyRow label="AI limit changes" value="disabled" /><PolicyRow label="Kill switch" value="independent" /><PolicyRow label="Live capital" value="sealed" /></div></section></div>
}

function PolicyRow({ label, value }: { label: string; value: string }) { return <div className="policy-row"><span>{label}</span><strong><Check size={14} /> {value}</strong></div> }

function DataWorkspace({ data }: { data: DashboardOverview }) {
  return <div className="workspace-stack"><div className="data-health-strip"><div><Database size={20} /><strong>Point-in-time data spine</strong><span>Snapshots carry first-available timestamps, revision lineage, parser versions, and source grades.</span></div><div className="health-number"><strong>{data.status.data_freshness_seconds}s</strong><span>freshest market tick</span></div></div><section className="panel wide-table"><div className="panel-heading"><div><div className="section-kicker"><Database size={14} /> SOURCE HEALTH</div><h2>Quality monitor</h2></div><span className="panel-heading-meta">AS OF {formatTime(data.as_of)} UTC</span></div><div className="data-table quality-table"><div className="data-table-row table-head"><span>DATASET</span><span>STATE</span><span>FRESHNESS</span><span>OBSERVATIONS</span><span>SOURCE FAMILIES</span><span>FINDING</span></div>{data.data_quality.map((row) => <div className="data-table-row" key={row.dataset}><span><strong>{row.dataset}</strong><small>immutable projection</small></span><span><StatusPill tone={row.tone} label={row.state} /></span><span className="mono">{row.freshness}</span><span className="mono">{row.observations}</span><span>{row.source_families}</span><span className="muted">{row.finding}</span></div>)}</div></section><div className="model-lanes"><ModelLane title="Forecast candidates" icon={<LineChart size={16} />} detail="Naive · LightGBM · Chronos-2" state="baseline included" /><ModelLane title="Evidence routes" icon={<BookOpenCheck size={16} />} detail="2 source families minimum" state="gate enforced" /><ModelLane title="Artifact lineage" icon={<Boxes size={16} />} detail="Bronze → Silver → Gold" state="hash bound" /></div></div>
}

function ModelLane({ title, detail, state, icon }: { title: string; detail: string; state: string; icon: React.ReactNode }) { return <div className="model-lane"><span className="lane-icon">{icon}</span><span><strong>{title}</strong><small>{detail}</small></span><em>{state}</em></div> }

function SystemWorkspace({ data }: { data: DashboardOverview }) {
  return <div className="workspace-stack"><div className="system-overview"><div className="system-stat"><span className="section-kicker"><Cpu size={14} /> RESOURCE HEADROOM</span><strong>{data.status.resource_headroom_gib.toFixed(1)} <small>GiB</small></strong><span>of required 1.5 GiB minimum</span></div><div className="system-stat"><span className="section-kicker"><Activity size={14} /> ALWAYS-ON SERVICES</span><strong>4 <small>/ 4</small></strong><span>one degraded source worker</span></div><div className="system-stat"><span className="section-kicker"><HardDrive size={14} /> LEDGER</span><strong>WAL <small>live</small></strong><span>last event {formatTime(data.status.last_ledger_event_at)} UTC</span></div></div><section className="panel wide-table"><div className="panel-heading"><div><div className="section-kicker"><ServerCog size={14} /> SERVICE REGISTRY</div><h2>Ownership and runtime state</h2></div><span className="panel-heading-meta">MODE {data.status.operating_mode.toUpperCase()}</span></div><div className="data-table service-table"><div className="data-table-row table-head"><span>SERVICE</span><span>KIND</span><span>STATE</span><span>OWNS</span><span>LATENCY</span><span>ADMISSION</span></div>{data.services.map((row) => <div className="data-table-row" key={row.name}><span><strong>{row.name}</strong><small>resource bounded</small></span><span>{row.kind}</span><span><StatusPill tone={row.tone} label={row.state} /></span><span className="muted">{row.owns}</span><span className="mono">{row.latency}</span><span className="mono">{row.tone === 'warning' ? 'review' : 'admitted'}</span></div>)}</div></section><section className="panel load-panel"><div className="panel-heading"><div><div className="section-kicker"><Zap size={14} /> LOAD SHEDDING ORDER</div><h2>Protect the spine first</h2></div></div><div className="load-order">{['archive', 'browser', 'Hermes / Skill Foundry', 'low-priority research', 'training / backtests', 'optional challengers', 'noncritical collectors'].map((item, index) => <div className={`load-step ${index === 6 ? 'last' : ''}`} key={item}><span>{String(index + 1).padStart(2, '0')}</span><strong>{item}</strong>{index < 3 && <small>shed first</small>}</div>)}</div></section></div>
}

function IncidentWorkspace({ data }: { data: DashboardOverview }) {
  return <div className="workspace-stack"><div className="incident-banner"><AlertTriangle size={21} /><div><strong>{data.incidents.filter((incident) => incident.status === 'open').length} open incident</strong><span>Containment and reconciliation stay linked to the immutable ledger.</span></div><button className="secondary-button"><BookOpenCheck size={15} /> OPEN RUNBOOK</button></div><section className="panel wide-table"><div className="panel-heading"><div><div className="section-kicker"><AlertTriangle size={14} /> INCIDENT LEDGER</div><h2>Containment and recovery</h2></div><span className="panel-heading-meta">AUDIT REQUIRED FOR CLOSE</span></div><div className="data-table"><div className="data-table-row table-head"><span>INCIDENT</span><span>SEVERITY</span><span>SUMMARY</span><span>OWNER</span><span>OPENED</span><span>STATE</span></div>{data.incidents.map((row) => <div className="data-table-row" key={row.incident_id}><span><strong>{row.incident_id}</strong><small>runbook linked</small></span><span className={toneClass(row.tone)}>{row.severity}</span><span>{row.summary}</span><span className="mono">{row.owner}</span><span className="mono">{row.opened}</span><span><StatusPill tone={row.tone} label={row.status} /></span></div>)}</div></section><div className="recovery-grid"><div className="recovery-step complete"><span><Check size={14} /></span><strong>Contain</strong><small>Owner and safe state recorded</small></div><div className="recovery-step complete"><span><Check size={14} /></span><strong>Reconcile</strong><small>Account and venue projections aligned</small></div><div className="recovery-step"><span>03</span><strong>Correct</strong><small>Test and rollback link required</small></div></div></div>
}

function AuditWorkspace({ data }: { data: DashboardOverview }) {
  return <div className="workspace-stack"><div className="audit-intro"><div className="audit-seal"><ClipboardList size={24} /></div><div><strong>Append-only evidence trail.</strong><span>Hashes, actors, timestamps, and owning services make the decision chain inspectable.</span></div><div className="audit-identity"><span>CONTRACT</span><strong>v3.0</strong></div></div><section className="panel wide-table"><div className="panel-heading"><div><div className="section-kicker"><FileClock size={14} /> IMMUTABLE EVENTS</div><h2>Recent audit activity</h2></div><div className="search-field"><Search size={14} /><input placeholder="Filter event type or actor" aria-label="Filter audit events" /></div></div><div className="data-table audit-table"><div className="data-table-row table-head"><span>EVENT</span><span>TIME</span><span>ACTOR</span><span>TYPE</span><span>SUMMARY</span><span>HASH</span></div>{data.audit.map((row) => <div className="data-table-row" key={row.event_id}><span><strong>{row.event_id}</strong><small>ledger / WAL</small></span><span className="mono">{formatDate(row.at)}</span><span>{row.actor}</span><span className="mono">{row.event_type}</span><span>{row.summary}</span><span className="hash-cell">#{row.hash.slice(0, 12)}</span></div>)}</div></section></div>
}

function SettingsWorkspace({ data, onCommand }: { data: DashboardOverview; onCommand: (command: CommandKind) => void }) {
  return <div className="workspace-stack"><div className="settings-warning"><ShieldAlert size={20} /><div><strong>Settings are proposals, never hidden mutations.</strong><span>Every revision is validated, diffed, approved, hashed, and applied by its owning service.</span></div></div><div className="settings-grid"><section className="panel settings-panel"><div className="panel-heading"><div><div className="section-kicker"><Settings2 size={14} /> OPERATING MODE</div><h2>Resource envelope</h2></div><StatusPill tone="info" label={data.status.operating_mode} /></div><div className="mode-list">{['trade_fast', 'standard', 'deep', 'builder', 'recovery'].map((mode) => <button className={`mode-row ${data.status.operating_mode === mode ? 'selected' : ''}`} key={mode} onClick={() => onCommand('set_mode')}><span>{mode.replace('_', ' / ').toUpperCase()}</span><small>{mode === 'trade_fast' ? 'zero remote LLM · hot path' : mode === 'standard' ? '2 remote calls · one GPU worker' : mode === 'deep' ? 'expanded council · challengers' : mode === 'builder' ? 'isolated Hermes work' : 'deterministic recovery first'}</small>{data.status.operating_mode === mode && <Check size={15} />}</button>)}</div></section><section className="panel settings-panel"><div className="panel-heading"><div><div className="section-kicker"><KeyRound size={14} /> SECURITY POSTURE</div><h2>Local / LAN boundary</h2></div><span className="locked-label">ENFORCED</span></div><div className="security-list"><PolicyRow label="Password storage" value="Argon2id" /><PolicyRow label="MFA" value="TOTP required" /><PolicyRow label="Session" value="15 min TTL" /><PolicyRow label="CSRF" value="bound" /><PolicyRow label="Live orders" value="denied" /></div><button className="secondary-button wide-button" onClick={() => onCommand('propose_config')}><SlidersHorizontal size={15} /> PROPOSE CONFIG REVISION</button></section></div><section className="panel sealed-panel"><LockKeyhole size={22} /><div><div className="section-kicker">LIVE ACTIVATION</div><h2>Sealed by design</h2><p>{data.live_readiness.approval}. The live route is not present in the V1 command contract.</p></div><div className="sealed-blockers">{data.live_readiness.blockers.map((blocker) => <span key={blocker}><AlertTriangle size={13} />{blocker}</span>)}</div></section></div>
}

function CommandModal({ command, currentMode, protectedMode, onClose, onConfirm }: {
  command: CommandKind
  currentMode: string
  protectedMode: boolean
  onClose: () => void
  onConfirm: (
    command: CommandKind,
    reason: string,
    requestedMode?: string,
    configPatch?: Record<string, string>,
    stepUpCredentials?: StepUpCredentials,
  ) => Promise<void>
}) {
  const [reason, setReason] = useState(command === 'halt_paper' ? 'Operator initiated paper safety halt' : 'Operator control-room change')
  const [requestedMode, setRequestedMode] = useState(currentMode)
  const [configPatchText, setConfigPatchText] = useState('{\n  "review": "operator proposal"\n}')
  const [stepUpPassword, setStepUpPassword] = useState('')
  const [stepUpCode, setStepUpCode] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const destructive = command === 'halt_paper'
  const isMode = command === 'set_mode'
  const isProposal = command === 'propose_config'
  const sensitive = requiresStepUp(command)
  const copy: Record<CommandKind, { title: string; body: string; confirm: string }> = {
    halt_paper: { title: 'Halt paper activity?', body: 'The paper control plane will enter a safe halted state. Reconciliation remains available; resume requires step-up authentication.', confirm: 'CONFIRM HALT' },
    resume_paper: { title: 'Resume paper activity?', body: 'Resume is allowed only when reconciliation is clean and a fresh step-up check is present.', confirm: 'RESUME PAPER' },
    set_mode: { title: 'Change operating mode?', body: 'The resource governor will admit the selected mode. This does not change risk limits or execution authority.', confirm: 'APPLY MODE' },
    propose_config: { title: 'Stage configuration proposal?', body: 'This creates a reviewable revision. It does not apply policy directly from the browser.', confirm: 'STAGE PROPOSAL' },
    rollback_config: { title: 'Request configuration rollback?', body: 'The owning service will validate and record the rollback before applying it.', confirm: 'REQUEST ROLLBACK' },
    refresh_data: { title: 'Refresh authoritative state?', body: 'Source workers retain acquisition authority. This request is recorded for traceability.', confirm: 'REFRESH STATE' },
  }
  const content = copy[command]
  const confirm = () => {
    setFormError(null)
    if (reason.trim().length < 3) {
      setFormError('Add a short reason so the command can be audited.')
      return
    }
    let configPatch: Record<string, string> | undefined
    if (isProposal) {
      try {
        const parsed: unknown = JSON.parse(configPatchText)
        if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error()
        const entries = Object.entries(parsed as Record<string, unknown>)
        if (!entries.length || entries.some(([key, value]) => !key.trim() || typeof value !== 'string')) throw new Error()
        configPatch = Object.fromEntries(entries) as Record<string, string>
      } catch {
        setFormError('Configuration patch must be a JSON object with string values.')
        return
      }
    }
    if (protectedMode && sensitive && (!stepUpPassword.trim() || !/^\d{6}$/.test(stepUpCode))) {
      setFormError('Enter the owner password and current six-digit authenticator code for step-up.')
      return
    }
    void onConfirm(
      command,
      reason,
      isMode ? requestedMode : undefined,
      configPatch,
      protectedMode && sensitive ? { password: stepUpPassword, totpCode: stepUpCode } : undefined,
    )
  }
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}><div className={`command-modal ${destructive ? 'destructive' : ''}`} role="dialog" aria-modal="true" aria-labelledby="command-title"><div className="modal-top"><span className="modal-type">{destructive ? <ShieldAlert size={15} /> : <SlidersHorizontal size={15} />} GUARDED COMMAND</span><button className="icon-button" onClick={onClose} aria-label="Close dialog"><X size={17} /></button></div><h2 id="command-title">{content.title}</h2><p>{content.body}</p>{isMode && <label className="modal-field">Requested mode<select value={requestedMode} onChange={(event) => setRequestedMode(event.target.value)}>{['trade_fast', 'standard', 'deep', 'builder', 'recovery'].map((mode) => <option value={mode} key={mode}>{mode.replace('_', ' / ').toUpperCase()}</option>)}</select></label>}{isProposal && <label className="modal-field">Configuration patch (JSON)<textarea value={configPatchText} onChange={(event) => setConfigPatchText(event.target.value)} rows={4} spellCheck={false} /></label>}<label className="modal-field">Reason<textarea value={reason} onChange={(event) => setReason(event.target.value)} rows={2} /></label>{protectedMode && sensitive && <div className="step-up-fields"><label className="modal-field">Owner password<input type="password" value={stepUpPassword} onChange={(event) => setStepUpPassword(event.target.value)} autoComplete="current-password" /></label><label className="modal-field">Authenticator code<input inputMode="numeric" pattern="[0-9]{6}" maxLength={6} value={stepUpCode} onChange={(event) => setStepUpCode(event.target.value.replace(/\D/g, ''))} autoComplete="one-time-code" /></label><span className="step-up-note"><LockKeyhole size={13} /> A fresh token is issued for this command and consumed once.</span></div>}{formError && <div className="form-error" role="alert"><ShieldAlert size={15} />{formError}</div>}{(destructive || command === 'resume_paper' || isMode || isProposal || command === 'rollback_config') && <div className="modal-check"><Check size={15} /><span>I understand this is recorded against the operator audit trail.</span></div>}<div className="modal-actions"><button className="secondary-button" onClick={onClose}>CANCEL</button><button className={destructive ? 'danger-button' : 'primary-button'} onClick={confirm}>{content.confirm}<ChevronRight size={15} /></button></div></div></div>
}

export default App
