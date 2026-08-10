import type {
  AuthStatus,
  CommandReceipt,
  CommandRequest,
  DashboardOverview,
  SourceHealthView,
} from './types'

const apiBase = (import.meta.env.VITE_API_BASE as string | undefined) ?? ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const { headers: initHeaders, ...requestInit } = init ?? {}
  const headers = new Headers(initHeaders)
  headers.set('Accept', 'application/json')
  if (requestInit.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(`${apiBase}${path}`, {
    ...requestInit,
    credentials: 'include',
    headers,
  })
  if (!response.ok) {
    const detail = await response.json().catch(() => ({})) as { detail?: string }
    const error = new Error(detail.detail ?? `Dashboard API returned ${response.status}`)
    ;(error as Error & { status?: number }).status = response.status
    throw error
  }
  return response.json() as Promise<T>
}

export const dashboardApi = {
  status: () => request<AuthStatus>('/api/v1/auth/status'),
  overview: () => request<DashboardOverview>('/api/v1/dashboard/overview'),
  sourceHealth: () => request<SourceHealthView[]>('/api/v1/dashboard/source-health'),
  login: (password: string, totpCode: string) =>
    request<{ csrf_token: string; subject: string }>('/api/v1/auth/login', {
      method: 'POST',
      body: JSON.stringify({ password, totp_code: totpCode }),
    }),
  stepUp: (password: string, totpCode: string) => {
    const csrf = sessionStorage.getItem('advisorai_csrf')
    return request<{ step_up_token: string; expires_at: string }>('/api/v1/auth/step-up', {
      method: 'POST',
      headers: csrf ? { 'X-CSRF-Token': csrf } : {},
      body: JSON.stringify({ password, totp_code: totpCode }),
    })
  },
  logout: () => request<{ status: string }>('/api/v1/auth/logout', { method: 'POST' }),
  command: (payload: CommandRequest) => {
    const csrf = sessionStorage.getItem('advisorai_csrf')
    return request<CommandReceipt>('/api/v1/control/command', {
      method: 'POST',
      headers: csrf ? { 'X-CSRF-Token': csrf } : {},
      body: JSON.stringify(payload),
    })
  },
}
