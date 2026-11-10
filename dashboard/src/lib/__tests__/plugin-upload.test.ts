import { describe, expect, it, vi } from 'vitest'
import { uploadPluginWebUI } from '@/lib/plugin-webui'

vi.mock('@/lib/api-base', () => ({
  resolveApiPath: async (path: string) => path,
  onBackendUrlChanged: () => () => {},
  getApiBaseUrl: async () => '',
}))

describe('multipart transport', () => {
  it('sends actual file bytes in FormData with authenticated cookie transport', async () => {
    let body: FormData | null = null
    class FakeXHR {
      upload: { onprogress?: (event: unknown) => void } = {}
      withCredentials = false
      status = 200
      responseText = JSON.stringify({ result: { id: 'image' } })
      onload?: () => void
      open(method: string, url: string) {
        expect(method).toBe('POST')
        expect(url).toBe('/api/webui/plugins/runtime/webui/test.plugin/images/uploads/add')
      }
      send(form: FormData) {
        expect(this.withCredentials).toBe(true)
        body = form
        this.upload.onprogress?.({ lengthComputable: true, loaded: 5, total: 10 })
        this.onload?.()
      }
    }
    vi.stubGlobal('XMLHttpRequest', FakeXHR)
    try {
      const progress = vi.fn()
      const result = await uploadPluginWebUI('test.plugin', 'images', 'add', new File(['bytes'], 'x.png'), {}, progress)
      expect(result).toEqual({ id: 'image' })
      expect((body as unknown as FormData).get('file')).toBeInstanceOf(File)
      expect((body as unknown as FormData).get('args')).toBe('{}')
      expect(progress).toHaveBeenCalledWith(50)
    } finally { vi.unstubAllGlobals() }
  })
})
