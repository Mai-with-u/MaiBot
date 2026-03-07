/**
 * Electron API type definitions
 * Declares Window.electronAPI and globalThis.__RUNTIME__ for frontend use
 */

import type { RuntimeInfo } from '@/lib/runtime'

/**
 * Backend connection configuration
 */
export interface BackendConnection {
  /** Unique identifier */
  id: string
  /** Display name */
  name: string
  /** Connection URL */
  url: string
  /** Whether this is the default backend */
  isDefault: boolean
  /** Last connection timestamp */
  lastConnected?: number
}

/**
 * Electron IPC API exposed to renderer process
 * All methods communicate via IPC bridges to main process
 */
export interface ElectronAPI {
  // Window control
  /** Minimize the application window */
  minimizeWindow(): void
  /** Maximize the application window */
  maximizeWindow(): void
  /** Close the application window */
  closeWindow(): void
  /** Check if window is currently maximized */
  isMaximized(): Promise<boolean>

  // Window event listeners
  /** Register callback for window maximized event */
  onWindowMaximized(callback: () => void): () => void
  /** Register callback for window unmaximized event */
  onWindowUnmaximized(callback: () => void): () => void

  // Backend management
  /** Get list of all configured backends */
  getBackends(): Promise<BackendConnection[]>
  /** Add a new backend connection */
  addBackend(conn: Omit<BackendConnection, 'id'>): Promise<BackendConnection>
  /** Update an existing backend configuration */
  updateBackend(id: string, patch: Partial<BackendConnection>): Promise<void>
  /** Remove a backend by ID */
  removeBackend(id: string): Promise<void>
  /** Set the active backend */
  setActiveBackend(id: string): Promise<void>
  /** Get the currently active backend */
  getActiveBackend(): Promise<BackendConnection | null>
  /** Get the active backend's URL for API requests */
  getActiveBackendUrl(): Promise<string | null>

  // Application state
  /** Mark that first-launch setup has been completed */
  markFirstLaunchComplete(): Promise<void>
  /** Check if this is the first launch */
  isFirstLaunch(): Promise<boolean>
  /** Get application version */
  getAppVersion(): Promise<string>

  // Backend event listener
  /** Register callback for backend change events */
  onBackendChanged(callback: (backend: BackendConnection | null) => void): () => void

  // Platform detection
  /** Get platform identifier (darwin, win32, linux) */
  getPlatform(): string
}

// Extend Window interface to include electronAPI
declare global {
  interface Window {
    /** Electron API bridge for main process communication */
    electronAPI?: ElectronAPI
  }

  /**
   * Global runtime information
   * Set by Electron preload, undefined in browser
   */
  namespace globalThis {
    var __RUNTIME__: RuntimeInfo | undefined
  }
}

// Ensure this file is treated as a module
export {}
