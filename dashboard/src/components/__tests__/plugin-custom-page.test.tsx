import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PluginCustomPage } from '@/components/plugin-custom-page'
import type { CustomPage } from '@/lib/plugin-webui'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  invoke: vi.fn(),
  upload: vi.fn(),
  i18n: { language: 'en' },
}))
vi.mock('@/lib/http', () => ({ backendApi: { get: mocks.get } }))
vi.mock('@/lib/plugin-webui', () => ({
  invokePluginWebUI: mocks.invoke,
  uploadPluginWebUI: mocks.upload,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: mocks.i18n }),
}))

const page: CustomPage = {
  id: 'custom',
  title: 'Custom page',
  description: '',
  placement: 'sidebar',
  icon: 'puzzle',
  mode: 'custom',
  document_revision: 'revision',
  entry: 'index.html',
  layout: 'document',
  queries: { summary: { api: 'summary', version: '1', parameters: {}, confirmation: null } },
  actions: {
    reset: { api: 'reset', version: '1', parameters: {}, confirmation: 'Reset records?' },
    receive: { api: 'receive', version: '1', parameters: {}, confirmation: null },
  },
  uploads: { library: { action: 'receive' } },
}

class TestPort {
  onmessage: ((event: { data: unknown }) => void) | null = null
  postMessage = vi.fn()
  close = vi.fn()
}
const channels: Array<{ port1: TestPort; port2: TestPort }> = []

async function start(target = page) {
  const view = render(<PluginCustomPage pluginId="test.plugin" page={target} pages={[target]} />)
  const iframe = (await screen.findByTitle('Custom page')) as HTMLIFrameElement
  const nonce = JSON.parse(iframe.srcdoc.match(/nonce: ("[^"]+")/)![1])
  act(() =>
    window.dispatchEvent(
      new MessageEvent('message', {
        origin: 'null',
        source: iframe.contentWindow,
        data: { type: 'maibot:ready', nonce },
      })
    )
  )
  return { ...view, iframe, port: channels[0].port1 }
}

describe('custom page bridge lifecycle', () => {
  it('does not resend unchanged theme CSS on root mutations or resize', async () => {
    const { port } = await start()
    port.postMessage.mockClear()
    await act(async () => {
      document.documentElement.setAttribute('data-unrelated', 'changed')
      window.dispatchEvent(new Event('resize'))
      await new Promise((resolve) => setTimeout(resolve, 40))
    })
    expect(
      port.postMessage.mock.calls.filter(([message]) => message.type === 'context')
    ).toHaveLength(0)
    await act(async () => document.documentElement.classList.add('dark'))
    await waitFor(() =>
      expect(port.postMessage).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'context',
          context: expect.objectContaining({ theme: 'dark' }),
        })
      )
    )
    document.documentElement.classList.remove('dark')
    document.documentElement.removeAttribute('data-unrelated')
  })
  beforeEach(() => {
    channels.length = 0
    vi.stubGlobal(
      'MessageChannel',
      class {
        port1 = new TestPort()
        port2 = new TestPort()
        constructor() {
          channels.push(this)
        }
      }
    )
    mocks.get.mockResolvedValue({ document: '<h1>Plugin content</h1>', revision: 'revision' })
    mocks.invoke.mockResolvedValue({ count: 3 })
    mocks.invoke.mockClear()
    mocks.upload.mockReset()
  })
  afterEach(() => vi.unstubAllGlobals())

  it('reuses the declared upload endpoint and forwards progress', async () => {
    const { port } = await start()
    const file = new File(['png'], 'image.png', { type: 'image/png' })
    mocks.upload.mockImplementation(async (_plugin, _page, _action, _file, _args, progress) => {
      progress(70)
      return { added: true }
    })
    await act(async () =>
      port.onmessage?.({
        data: {
          id: 1,
          kind: 'uploads',
          name: 'library',
          file,
          args: { label: 'self' },
        },
      })
    )
    expect(mocks.upload).toHaveBeenCalledWith(
      'test.plugin',
      'custom',
      'library',
      file,
      { label: 'self' },
      expect.any(Function),
      expect.any(AbortSignal),
      false
    )
    expect(port.postMessage).toHaveBeenCalledWith({ id: 1, type: 'progress', percent: 70 })
    expect(port.postMessage).toHaveBeenCalledWith({ id: 1, result: { added: true } })
  })

  it('aborts a pending upload on cancellation or leaving the page', async () => {
    const { port, unmount } = await start()
    const signals: AbortSignal[] = []
    mocks.upload.mockImplementation((_plugin, _page, _action, _file, _args, _progress, signal) => {
      signals.push(signal)
      return new Promise((_resolve, reject) =>
        signal.addEventListener('abort', () => reject(new Error('cancelled')))
      )
    })
    const file = new File(['png'], 'image.png', { type: 'image/png' })
    act(() => {
      void port.onmessage?.({ data: { id: 1, kind: 'uploads', name: 'library', file, args: {} } })
    })
    await act(async () => port.onmessage?.({ data: { kind: 'cancel', id: 1 } }))
    expect(signals[0].aborted).toBe(true)
    act(() => {
      void port.onmessage?.({ data: { id: 2, kind: 'uploads', name: 'library', file, args: {} } })
    })
    unmount()
    expect(signals[1].aborted).toBe(true)
  })

  it('cancels uploads while host confirmation is open', async () => {
    const { port } = await start({
      ...page,
      actions: {
        ...page.actions,
        receive: { ...page.actions.receive, confirmation: 'Receive image?' },
      },
    })
    const file = new File(['png'], 'image.png', { type: 'image/png' })
    act(() => {
      void port.onmessage?.({ data: { id: 1, kind: 'uploads', name: 'library', file, args: {} } })
    })
    expect(await screen.findByText('Receive image?')).toBeInTheDocument()
    await act(async () => port.onmessage?.({ data: { kind: 'cancel', id: 1 } }))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(mocks.upload).not.toHaveBeenCalled()
    expect(port.postMessage).toHaveBeenCalledWith({ id: 1, error: 'Error: Action cancelled' })
  })

  it('isolates markup and forwards only the fixed plugin/page bindings', async () => {
    const { iframe, port } = await start()
    expect(iframe.getAttribute('sandbox')).toBe('allow-scripts')
    expect(screen.queryByText('Plugin content')).not.toBeInTheDocument()
    await act(async () =>
      port.onmessage?.({
        data: {
          id: 1,
          kind: 'queries',
          name: 'summary',
          args: {},
          pluginId: 'other.plugin',
        },
      })
    )
    expect(mocks.invoke).toHaveBeenCalledWith(
      'test.plugin',
      'custom',
      'queries',
      'summary',
      {},
      false,
      expect.any(AbortSignal)
    )
    expect(port.postMessage).toHaveBeenCalledWith({ id: 1, result: { count: 3 } })
    await act(async () =>
      port.onmessage?.({ data: { id: 2, kind: 'queries', name: 'other', args: {} } })
    )
    expect(mocks.invoke).toHaveBeenCalledTimes(1)
  })

  it('requires host confirmation and cancels without invoking the action', async () => {
    const { port } = await start()
    act(() => {
      void port.onmessage?.({ data: { id: 1, kind: 'actions', name: 'reset', args: {} } })
    })
    expect(await screen.findByText('Reset records?')).toBeInTheDocument()
    expect(mocks.invoke).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'pluginWebUI.cancel' }))
    await waitFor(() =>
      expect(port.postMessage).toHaveBeenCalledWith({ id: 1, error: 'Error: Action cancelled' })
    )
    expect(mocks.invoke).not.toHaveBeenCalled()
  })

  it('executes confirmed actions using the argument snapshot', async () => {
    const { port } = await start()
    const args = { filter: { ids: [2] } }
    act(() => {
      void port.onmessage?.({ data: { id: 1, kind: 'actions', name: 'reset', args } })
    })
    args.filter.ids[0] = 3
    expect(await screen.findByText('Reset records?')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'pluginWebUI.confirm' }))
    await waitFor(() =>
      expect(mocks.invoke).toHaveBeenCalledWith(
        'test.plugin',
        'custom',
        'actions',
        'reset',
        { filter: { ids: [2] } },
        true,
        expect.any(AbortSignal)
      )
    )
  })

  it('does not authorize a readiness message from a different window', async () => {
    render(<PluginCustomPage pluginId="test.plugin" page={page} pages={[page]} />)
    const iframe = (await screen.findByTitle('Custom page')) as HTMLIFrameElement
    const nonce = JSON.parse(iframe.srcdoc.match(/nonce: ("[^"]+")/)![1])
    act(() =>
      window.dispatchEvent(
        new MessageEvent('message', {
          origin: 'null',
          source: window,
          data: { type: 'maibot:ready', nonce },
        })
      )
    )
    expect(channels).toHaveLength(0)
  })

  it('closes the bridge and aborts requests when the page is removed', async () => {
    const { port, unmount } = await start()
    const signal = mocks.get.mock.calls.at(-1)![1].signal as AbortSignal
    unmount()
    expect(port.close).toHaveBeenCalled()
    expect(signal.aborted).toBe(true)
  })

  it('lets the user stop the frame and rejects undeclared navigation', async () => {
    const { iframe, port } = await start()
    await act(async () =>
      port.onmessage?.({ data: { id: 1, kind: 'navigate', name: 'missing', args: {} } })
    )
    expect(port.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ id: 1, error: expect.any(String) })
    )
    fireEvent.click(screen.getByRole('button', { name: 'pluginWebUI.customStop' }))
    expect(port.close).toHaveBeenCalled()
    expect(iframe.isConnected).toBe(false)
  })

  it('allows a batch of 100 sequential requests without stopping the page', async () => {
    const { port, iframe } = await start()
    for (let id = 1; id <= 100; id++) {
      await act(async () =>
        port.onmessage?.({ data: { id, kind: 'queries', name: 'summary', args: {} } })
      )
    }
    expect(mocks.invoke).toHaveBeenCalledTimes(100)
    expect(port.close).not.toHaveBeenCalled()
    expect(iframe.isConnected).toBe(true)
  })

  it('stops a page that floods messages', async () => {
    const { port, iframe } = await start()
    act(() => {
      for (let index = 0; index < 201; index++) void port.onmessage?.({ data: null })
    })
    expect(port.close).toHaveBeenCalled()
    expect(iframe.isConnected).toBe(false)
    expect(screen.getByText('pluginWebUI.customStopped')).toBeInTheDocument()
    expect(screen.getByText('pluginWebUI.customStopRequestRate')).toBeInTheDocument()
  })

  it('removes the frame when it navigates and never reconnects its bridge', async () => {
    const { iframe, port } = await start()
    fireEvent.load(iframe)
    fireEvent.load(iframe)
    expect(port.close).toHaveBeenCalled()
    expect(iframe.isConnected).toBe(false)
    expect(channels).toHaveLength(1)
    expect(screen.getByText('pluginWebUI.customStopNavigation')).toBeInTheDocument()
  })

  it('rejects excess concurrent requests without forwarding them', async () => {
    const { port } = await start()
    mocks.invoke.mockImplementation(() => new Promise(() => {}))
    act(() => {
      for (let id = 1; id <= 3; id++)
        void port.onmessage?.({ data: { id, kind: 'queries', name: 'summary', args: {} } })
    })
    expect(mocks.invoke).toHaveBeenCalledTimes(2)
    expect(port.postMessage).toHaveBeenCalledWith({
      id: 3,
      error: 'Error: Concurrency limit reached',
    })
  })
})
