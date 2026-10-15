import { describe, expect, it } from 'vitest'

import { customPageDocument, parseCustomPageRequest } from '@/lib/plugin-custom-page'
import type { CustomPage } from '@/lib/plugin-webui'

const page = {
  queries: { summary: { api: 'summary' } },
  actions: { save: { api: 'save' } },
  uploads: { library: { action: 'save' } },
} as unknown as CustomPage

describe('custom page boundary', () => {
  it('rejects forged upload IDs, missing bindings and non-image files', () => {
    const file = new File(['png'], 'image.png', { type: 'image/png' })
    const request = { id: 1, kind: 'uploads', name: 'library', file, args: {} }
    expect(parseCustomPageRequest(request, page).file).toBe(file)
    for (const changes of [
      { name: 'other' },
      { args: { upload_id: 'forged' } },
      { file: {} },
      { file: new File(['code'], 'page.html', { type: 'text/html' }) },
      { file: new File([], 'empty.png', { type: 'image/png' }) },
    ])
      expect(() => parseCustomPageRequest({ ...request, ...changes }, page)).toThrow()
  })
  it('accepts only declared query/action aliases with bounded JSON arguments', () => {
    expect(
      parseCustomPageRequest({ id: 1, kind: 'queries', name: 'summary', args: { count: 2 } }, page)
    ).toEqual({ id: 1, kind: 'queries', name: 'summary', args: { count: 2 } })
    for (const request of [
      { id: 1, kind: 'queries', name: 'save', args: {} },
      { id: 1, kind: 'queries', name: 'toString', args: {} },
      { id: 1, kind: 'fetch', name: '/api/config', args: {} },
      { id: 1, kind: 'queries', name: 'summary', args: { value: new Date() } },
      { id: 1, kind: 'queries', name: 'summary', args: { value: Infinity } },
      { id: 1, kind: 'queries', name: 'summary', args: { value: 'x'.repeat(65537) } },
      { id: 1, kind: 'queries', name: 'summary', args: { value: '界'.repeat(22000) } },
      { id: 0, kind: 'queries', name: 'summary', args: {} },
    ])
      expect(() => parseCustomPageRequest(request, page)).toThrow()
  })

  it('copies structured arguments and bounds navigation state', () => {
    const args = { filter: { ids: [1, 2], enabled: true } }
    const parsed = parseCustomPageRequest({ id: 1, kind: 'actions', name: 'save', args }, page)
    args.filter.ids.push(3)
    expect(parsed.args).toEqual({ filter: { ids: [1, 2], enabled: true } })
    expect(
      parseCustomPageRequest({ id: 2, kind: 'navigate', name: 'details', args: { id: 1 } }, page)
        .kind
    ).toBe('navigate')
    expect(() =>
      parseCustomPageRequest(
        { id: 2, kind: 'navigate', name: 'details', args: { text: 'x'.repeat(4097) } },
        page
      )
    ).toThrow()
    const cycle: Record<string, unknown> = {}
    cycle.self = cycle
    expect(() =>
      parseCustomPageRequest({ id: 1, kind: 'actions', name: 'save', args: cycle }, page)
    ).toThrow()
  })

  it('puts the network policy and SDK before any plugin markup', () => {
    const html = customPageDocument('<script>window.plugin = true</script>', 'instance-token')
    expect(html.indexOf('Content-Security-Policy')).toBeLessThan(html.indexOf('window.plugin'))
    expect(html.indexOf("Object.defineProperty(window, 'maibot'")).toBeLessThan(
      html.indexOf('window.plugin')
    )
    expect(html).toContain("connect-src 'none'")
    expect(html).toContain("form-action 'none'")
    expect(html).toContain("worker-src 'none'")
    expect(html).toContain('nonce: "instance-token"')
    expect(html).not.toContain('allow-same-origin')
  })
})
