/**
 * Runtime environment detection and information
 * Provides unified interface for checking execution environment (Electron vs Browser)
 */

/**
 * Type of runtime environment
 */
export type RuntimeKind = 'electron' | 'browser'

/**
 * Runtime information object
 */
export interface RuntimeInfo {
  /** Type of runtime (electron or browser) */
  kind: RuntimeKind
  /** Version information (electron versions, etc) */
  versions?: Record<string, string>
  /** User agent string */
  userAgent?: string
  /** Source of runtime detection (tag means set by preload, fallback means default for browser) */
  source: 'tag' | 'fallback'
}

/**
 * Build default browser runtime info
 * Used as fallback when not in Electron environment
 */
function buildBrowserRuntime(): RuntimeInfo {
  return {
    kind: 'browser',
    userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : undefined,
    source: 'fallback',
  }
}

/**
 * Get current runtime information
 * Reads from globalThis.__RUNTIME__ if available (set by Electron preload)
 * Falls back to browser runtime if not running in Electron
 */
export function getRuntime(): RuntimeInfo {
  // Check if running in Electron (preload sets __RUNTIME__)
  if (typeof globalThis !== 'undefined' && globalThis.__RUNTIME__) {
    return globalThis.__RUNTIME__
  }

  // Fallback to browser runtime
  return buildBrowserRuntime()
}

/**
 * Check if running in Electron environment
 * Safe to use across browser and Electron - always returns boolean
 */
export function isElectron(): boolean {
  return getRuntime().kind === 'electron'
}

/**
 * Get platform information
 * In Electron: calls window.electronAPI.getPlatform() for actual platform
 * In browser: returns 'browser' as identifier
 */
export function getPlatform(): string {
  if (!isElectron()) {
    return 'browser'
  }

  // Safe to access electronAPI because isElectron() confirms it's available
  if (typeof window !== 'undefined' && window.electronAPI?.getPlatform) {
    return window.electronAPI.getPlatform()
  }

  // Fallback if electronAPI unavailable
  return 'unknown'
}
