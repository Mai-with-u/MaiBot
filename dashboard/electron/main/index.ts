import { app, BrowserWindow, ipcMain, protocol, session } from 'electron'
import path from 'path'
import { fileURLToPath } from 'url'

import { registerAppProtocol } from './protocol'
import {
  addBackend,
  getActiveBackend,
  getBackends,
  getWindowBounds,
  isFirstLaunch,
  markFirstLaunchComplete,
  removeBackend,
  setActiveBackend,
  setWindowBounds,
  updateBackend,
} from './store'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

let mainWindow: BrowserWindow | null = null

/**
 * Register app:// custom protocol BEFORE app.whenReady()
 * This is critical for electron-vite to work correctly
 */
function registerAppScheme() {
  protocol.registerSchemesAsPrivileged([
    {
      scheme: 'app',
      privileges: {
        corsEnabled: true,
        secure: true,
        allowServiceWorkers: true,
        standard: true,
        supportFetchAPI: true,
        stream: true,
      },
    },
  ])
}

/**
 * Register all IPC handlers for window control and store CRUD
 */
function registerIpcHandlers() {
  // ── Window control ───────────────────────────────────────────────────────
  ipcMain.handle('electron:minimize-window', () => mainWindow?.minimize())
  ipcMain.handle('electron:maximize-window', () => {
    if (mainWindow?.isMaximized()) mainWindow.unmaximize()
    else mainWindow?.maximize()
  })
  ipcMain.handle('electron:close-window', () => mainWindow?.close())
  ipcMain.handle('electron:is-maximized', () => mainWindow?.isMaximized() ?? false)

  // ── Backend CRUD ─────────────────────────────────────────────────────────
  ipcMain.handle('electron:get-backends', () => getBackends())
  ipcMain.handle('electron:add-backend', (_e, conn) => addBackend(conn))
  ipcMain.handle('electron:update-backend', (_e, id, patch) => updateBackend(id, patch))
  ipcMain.handle('electron:remove-backend', (_e, id) => removeBackend(id))
  ipcMain.handle('electron:set-active-backend', (_e, id) => {
    setActiveBackend(id)
    const backend = getActiveBackend()
    mainWindow?.webContents.send('electron:backend-changed', backend)
  })
  ipcMain.handle('electron:get-active-backend', () => getActiveBackend())
  ipcMain.handle('electron:get-active-url', () => getActiveBackend()?.url ?? null)

  // ── App state ────────────────────────────────────────────────────────────
  ipcMain.handle('electron:is-first-launch', () => isFirstLaunch())
  ipcMain.handle('electron:mark-first-launch-complete', () => markFirstLaunchComplete())
  ipcMain.handle('electron:get-app-version', () => app.getVersion())
}

/**
 * Create the main application window
 */
function createWindow() {
  const isMac = process.platform === 'darwin'

  // Restore window bounds from store
  const bounds = getWindowBounds()

  mainWindow = new BrowserWindow({
    x: bounds.x,
    y: bounds.y,
    width: bounds.width,
    height: bounds.height,
    minWidth: 800,
    minHeight: 600,
    // macOS: hide native title bar but keep traffic light buttons
    ...(isMac
      ? {
          titleBarStyle: 'hidden' as const,
          trafficLightPosition: { x: 12, y: 8 },
        }
      : {}),
    // Windows/Linux: overlay title bar (custom title bar integrated)
    ...(!isMac
      ? {
          titleBarOverlay: {
            color: '#00000000',
            symbolColor: '#ffffff',
            height: 32,
          },
        }
      : {}),
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.js'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  })

  // Load the app using app:// protocol
  // electron-vite will handle serving the renderer from app://host/index.html
  if (process.env.ELECTRON_RENDERER_URL) {
    // Development: load from electron-vite dev server
    mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    // Production: load from bundled renderer
    mainWindow.loadURL('app://host/index.html')
  }

  // Persist window size/position on close
  mainWindow.on('close', () => {
    if (mainWindow) {
      const { x, y, width, height } = mainWindow.getBounds()
      setWindowBounds({ x, y, width, height })
    }
  })

  mainWindow.on('closed', () => {
    mainWindow = null
  })

  // Push maximize/unmaximize events to renderer
  mainWindow.on('maximize', () => {
    mainWindow?.webContents.send('electron:window-maximized')
  })
  mainWindow.on('unmaximize', () => {
    mainWindow?.webContents.send('electron:window-unmaximized')
  })

  // 窗口获得焦点时确保焦点传递到 webContents，支持屏幕阅读器正确工作
  mainWindow.on('focus', () => {
    mainWindow?.webContents.focus()
  })
}

/**
 * App event: when app is ready
 */
app.whenReady().then(() => {
  // 确保 Chromium a11y tree 始终激活（供屏幕阅读器使用）
  app.setAccessibilitySupportEnabled(true)

  registerAppProtocol()

  // Set Content Security Policy
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [
          "default-src 'self' app:; " +
          "script-src 'self' 'unsafe-inline' app:; " +
          "style-src 'self' 'unsafe-inline' app:; " +
          "img-src 'self' app: data: blob:; " +
          "font-src 'self' app: data:; " +
          "connect-src 'self' app: ws: wss: http: https:; " +
          "worker-src 'self' blob:;"
        ],
      },
    })
  })

  registerIpcHandlers()
  createWindow()
})

/**
 * App event: when all windows are closed (non-macOS behavior)
 */
app.on('window-all-closed', () => {
  // On macOS, applications typically stay open until the user quits
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

/**
 * App event: when app is activated (macOS)
 */
app.on('activate', () => {
  if (mainWindow === null) {
    createWindow()
  }
})

registerAppScheme()
