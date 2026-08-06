import type {
  AuthStatus,
  CommandReceipt,
  CommandRequest,
  DashboardOverview,
} from './types'

const apiBase = (import.meta.env.VITE_API_BASE as string | undefined) ?? ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    credentials: 'include',
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...(init?.headers ?? {}),
    },
    ...init,
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
  login: (password: string, totpCode: string) =>
    request<{ csrf_token: string; subject: string }>('/api/v1/auth/login', {
      method: 'POST',
      body: JSON.stringify({ password, totp_code: totpCode }),
    }),
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
