import { randomUUID } from 'crypto'

import Store, { type Schema } from 'electron-store'

/**
 * Backend connection data model
 */
export interface BackendConnection {
  id: string
  name: string
  url: string
  isDefault: boolean
  lastConnected?: number
}

/**
 * Application settings data model
 */
export interface AppSettings {
  backends: BackendConnection[]
  activeBackendId: string | null
  windowBounds: {
    x: number
    y: number
    width: number
    height: number
  }
  firstLaunchComplete: boolean
}

/**
 * JSON Schema for validating store contents
 */
const SCHEMA: Schema<AppSettings> = {
  backends: {
    type: 'array',
    items: {
      type: 'object',
      properties: {
        id: { type: 'string' },
        name: { type: 'string' },
        url: { type: 'string' },
        isDefault: { type: 'boolean' },
        lastConnected: { type: 'number' },
      },
      required: ['id', 'name', 'url', 'isDefault'],
    },
  },
  activeBackendId: { type: ['string', 'null'] },
  windowBounds: {
    type: 'object',
    properties: {
      x: { type: 'number' },
      y: { type: 'number' },
      width: { type: 'number' },
      height: { type: 'number' },
    },
    required: ['x', 'y', 'width', 'height'],
  },
  firstLaunchComplete: { type: 'boolean' },
}

/**
 * Default settings
 */
const DEFAULTS: AppSettings = {
  backends: [],
  activeBackendId: null,
  windowBounds: {
    x: 100,
    y: 100,
    width: 1280,
    height: 800,
  },
  firstLaunchComplete: false,
}

/**
 * Initialize electron-store with encryption and schema validation
 */
const store = new Store<AppSettings>({
  schema: SCHEMA,
  defaults: DEFAULTS,
  encryptionKey: process.env.MAIBOT_STORE_KEY,
})

/**
 * Get all backends
 */
export function getBackends(): BackendConnection[] {
  return store.get('backends', [])
}

/**
 * Add a new backend connection
 * Generates UUID for new backend
 */
export function addBackend(
  conn: Omit<BackendConnection, 'id'>,
): BackendConnection {
  const newBackend: BackendConnection = {
    ...conn,
    id: randomUUID(),
  }

  const backends = getBackends()
  backends.push(newBackend)
  store.set('backends', backends)

  return newBackend
}

/**
 * Update an existing backend connection
 */
export function updateBackend(
  id: string,
  patch: Partial<Omit<BackendConnection, 'id'>>,
): void {
  const backends = getBackends()
  const index = backends.findIndex((b) => b.id === id)

  if (index === -1) {
    throw new Error(`Backend with id ${id} not found`)
  }

  backends[index] = {
    ...backends[index],
    ...patch,
  }

  store.set('backends', backends)
}

/**
 * Remove a backend connection by id
 */
export function removeBackend(id: string): void {
  const backends = getBackends()
  const filtered = backends.filter((b) => b.id !== id)

  store.set('backends', filtered)

  // Clear active backend if it was the removed one
  if (store.get('activeBackendId') === id) {
    store.set('activeBackendId', null)
  }
}

/**
 * Set the active backend
 */
export function setActiveBackend(id: string): void {
  const backends = getBackends()

  if (!backends.find((b) => b.id === id)) {
    throw new Error(`Backend with id ${id} not found`)
  }

  store.set('activeBackendId', id)
}

/**
 * Get the currently active backend connection
 */
export function getActiveBackend(): BackendConnection | null {
  const activeId = store.get('activeBackendId')

  if (!activeId) {
    return null
  }

  const backends = getBackends()
  return backends.find((b) => b.id === activeId) || null
}

/**
 * Get window bounds
 */
export function getWindowBounds(): AppSettings['windowBounds'] {
  return store.get('windowBounds', DEFAULTS.windowBounds)
}

/**
 * Set window bounds
 */
export function setWindowBounds(bounds: AppSettings['windowBounds']): void {
  store.set('windowBounds', bounds)
}

/**
 * Check if this is the first launch
 */
export function isFirstLaunch(): boolean {
  return !store.get('firstLaunchComplete', false)
}

/**
 * Mark first launch as complete
 */
export function markFirstLaunchComplete(): void {
  store.set('firstLaunchComplete', true)
}

/**
 * Get complete app settings
 */
export function getSettings(): AppSettings {
  return {
    backends: getBackends(),
    activeBackendId: store.get('activeBackendId', null),
    windowBounds: getWindowBounds(),
    firstLaunchComplete: store.get('firstLaunchComplete', false),
  }
}
