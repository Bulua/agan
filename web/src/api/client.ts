import axios from 'axios'

export const apiClient = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

export function extractApiError(err: unknown, fallback = 'Request failed'): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail) && detail.length > 0) {
      return detail.map((d: any) => d?.msg || JSON.stringify(d)).join('; ')
    }
    return err.message || fallback
  }
  if (err instanceof Error) return err.message
  return fallback
}
