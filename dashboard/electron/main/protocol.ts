import { net, protocol } from 'electron'
import { readFile } from 'fs/promises'
import { dirname, extname, join } from 'path'
import { fileURLToPath } from 'url'

import { getActiveBackend } from './store'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

const MIME_TYPES: Record<string, string> = {
  '.html': 'text/html',
  '.js': 'application/javascript',
  '.mjs': 'application/javascript',
  '.cjs': 'application/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.txt': 'text/plain',
  '.webp': 'image/webp',
}

export function registerAppProtocol(): void {
  protocol.handle('app', async (request) => {
    const url = new URL(request.url)
    const pathname = url.pathname

    if (pathname.startsWith('/api/')) {
      const backend = getActiveBackend()
      const targetUrl = backend
        ? `${backend.url.replace(/\/$/, '')}${pathname}${url.search}`
        : null

      if (!targetUrl) {
        return new Response(JSON.stringify({ error: 'No backend configured' }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      const headers = new Headers(request.headers)
      headers.delete('host')

      return net.fetch(targetUrl, {
        method: request.method,
        headers,
        body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.body,
        duplex: 'half',
      })
    }

    // Dev mode: renderer is served by vite dev server, not app:// protocol
    if (process.env.ELECTRON_RENDERER_URL) {
      return new Response(null, { status: 204 })
    }

    const rendererDir = join(__dirname, '../renderer')
    const safePath = decodeURIComponent(pathname)
      .replace(/\.\./g, '')
      .replace(/^\/+/, '')

    const resolvedPath = safePath === '' ? 'index.html' : safePath
    const filePath = resolvedPath.endsWith('/')
      ? join(rendererDir, resolvedPath, 'index.html')
      : join(rendererDir, resolvedPath)

    const tryReadFile = async (path: string) => {
      const ext = extname(path)
      const mimeType = MIME_TYPES[ext] ?? 'application/octet-stream'
      const data = await readFile(path)
      return new Response(data, { headers: { 'Content-Type': mimeType } })
    }

    try {
      return await tryReadFile(filePath)
    } catch {
      const indexPath = join(rendererDir, 'index.html')
      return tryReadFile(indexPath)
    }
  })
}
