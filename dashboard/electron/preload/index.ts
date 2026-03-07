import { contextBridge, ipcRenderer } from 'electron'

// Write __RUNTIME__ tag into the isolated world so renderer can detect Electron
contextBridge.exposeInMainWorld('__RUNTIME__', {
  kind: 'electron' as const,
  versions: process.versions as unknown as Record<string, string>,
  source: 'tag' as const,
})

// Expose the full ElectronAPI surface to the renderer process
contextBridge.exposeInMainWorld('electronAPI', {
  // ── Platform detection ──────────────────────────────────────────────────
  getPlatform: () => process.platform,

  // ── Window control ──────────────────────────────────────────────────────
  minimizeWindow: () => ipcRenderer.invoke('electron:minimize-window'),
  maximizeWindow: () => ipcRenderer.invoke('electron:maximize-window'),
  closeWindow: () => ipcRenderer.invoke('electron:close-window'),
  isMaximized: () => ipcRenderer.invoke('electron:is-maximized'),

  // ── Window event listeners ───────────────────────────────────────────────
  onWindowMaximized: (callback: () => void) => {
    const listener = () => callback()
    ipcRenderer.on('electron:window-maximized', listener)
    return () => ipcRenderer.removeListener('electron:window-maximized', listener)
  },
  onWindowUnmaximized: (callback: () => void) => {
    const listener = () => callback()
    ipcRenderer.on('electron:window-unmaximized', listener)
    return () => ipcRenderer.removeListener('electron:window-unmaximized', listener)
  },

  // ── Backend CRUD ─────────────────────────────────────────────────────────
  getBackends: () => ipcRenderer.invoke('electron:get-backends'),
  addBackend: (conn: object) => ipcRenderer.invoke('electron:add-backend', conn),
  updateBackend: (id: string, patch: object) =>
    ipcRenderer.invoke('electron:update-backend', id, patch),
  removeBackend: (id: string) => ipcRenderer.invoke('electron:remove-backend', id),
  setActiveBackend: (id: string) =>
    ipcRenderer.invoke('electron:set-active-backend', id),
  getActiveBackend: () => ipcRenderer.invoke('electron:get-active-backend'),
  getActiveBackendUrl: () => ipcRenderer.invoke('electron:get-active-url'),

  // ── App state ───────────────────────────────────────────────────────────
  isFirstLaunch: () => ipcRenderer.invoke('electron:is-first-launch'),
  markFirstLaunchComplete: () =>
    ipcRenderer.invoke('electron:mark-first-launch-complete'),
  getAppVersion: () => ipcRenderer.invoke('electron:get-app-version'),

  // ── Backend event listener ──────────────────────────────────────────────
  onBackendChanged: (callback: (backend: { id: string; name: string; url: string; isDefault: boolean; lastConnected?: number } | null) => void) => {
    const listener = (_event: unknown, backend: { id: string; name: string; url: string; isDefault: boolean; lastConnected?: number } | null) => callback(backend)
    ipcRenderer.on('electron:backend-changed', listener)
    return () => ipcRenderer.removeListener('electron:backend-changed', listener)
  },
})
