export type ThMediaRuntimeConfig = {
  backendBaseUrl?: string
  authToken?: string
}

declare global {
  interface Window {
    __TH_MEDIA_RUNTIME__?: ThMediaRuntimeConfig
  }
}

export function getRuntimeConfig(): ThMediaRuntimeConfig {
  return window.__TH_MEDIA_RUNTIME__ || {}
}

export function resolveApiUrl(path: string): string {
  const base = (getRuntimeConfig().backendBaseUrl || '').replace(/\/+$/, '')
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return base ? `${base}${normalizedPath}` : normalizedPath
}
