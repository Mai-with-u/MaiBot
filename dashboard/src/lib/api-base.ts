/**
 * Centralized API base URL utility
 * Provides single source of truth for all URL construction across the application
 * Handles environment-specific configuration (Electron, Browser DEV, Browser PROD)
 */

import type { BackendConnection } from '@/types/electron'

import { isElectron } from './runtime'

/**
 * Get API base URL for HTTP/HTTPS requests
 * - Electron: User-configured backend URL from main process
 * - Browser DEV: Empty string (Vite proxy handles /api prefix)
 * - Browser PROD: Empty string (same-origin deployment)
 */
export async function getApiBaseUrl(): Promise<string> {
  if (isElectron()) {
    // Electron: Get configured backend URL from IPC
    const backendUrl = await window.electronAPI?.getActiveBackendUrl()
    return backendUrl ?? ''
  }

  // Browser (DEV & PROD): Return empty string
  // In DEV: Vite proxy forwards /api requests to backend
  // In PROD: API is deployed on same origin as frontend
  return ''
}

/**
 * Get WebSocket base URL
 * - Electron: Convert HTTP/HTTPS URL to WS/WSS
 * - Browser DEV: Use same-origin WS URL and let Vite proxy forward requests
 * - Browser PROD: Construct WS URL from window.location
 */
export async function getWsBaseUrl(): Promise<string> {
  if (isElectron()) {
    // Electron: Convert API URL protocol to WS protocol
    const apiUrl = await getApiBaseUrl()
    if (!apiUrl) {
      return ''
    }

    // Convert http -> ws, https -> wss
    return apiUrl.replace(/^https?/, (match) => {
      return match === 'https' ? 'wss' : 'ws'
    })
  }

  // Browser DEV: Use same-origin URL so Vite proxy can forward WebSocket requests
  if (import.meta.env.DEV) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${protocol}//${window.location.host}`
  }

  // Browser PROD: Construct WS URL from current location
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = window.location.host
  return `${protocol}//${host}`
}

/**
 * Resolve full API path by prepending base URL if needed
 * - Electron: Prepends configured backend URL
 * - Browser: Path remains unchanged (proxy/same-origin handling)
 */
export async function resolveApiPath(path: string): Promise<string> {
  if (isElectron()) {
    const baseUrl = await getApiBaseUrl()
    return baseUrl ? `${baseUrl}${path}` : path
  }

  // Browser: Path is used as-is
  return path
}

/**
 * Subscribe to backend URL changes
 * Electron: Listens to IPC backend change events
 * Browser: No-op (backend cannot change at runtime)
 *
 * @param callback Function called when backend URL changes
 * @returns Unsubscribe function
 */
export function onBackendUrlChanged(
  callback: (newUrl: string | null) => void
): () => void {
  if (!isElectron()) {
    // Browser: No-op, return empty unsubscribe function
    return () => {}
  }

  // Electron: Register IPC listener and return unsubscribe function
  if (!window.electronAPI?.onBackendChanged) {
    return () => {}
  }

  // Wrap callback to extract URL from BackendConnection
  const wrappedCallback = (backend: BackendConnection | null) => {
    const url = backend?.url ?? null
    callback(url)
  }

  // Get and return the unsubscribe function from preload
  return window.electronAPI.onBackendChanged(wrappedCallback)
}
