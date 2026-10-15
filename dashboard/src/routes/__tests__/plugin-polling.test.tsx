import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PluginWebUIPage } from '@/routes/plugin-webui'

const mocks = vi.hoisted(() => ({ invoke: vi.fn() }))
vi.mock('@tanstack/react-router', () => ({
  useParams: () => ({ pluginId: 'test.plugin', pageId: 'jobs' }),
  Link: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
}))
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))
vi.mock('@/lib/plugin-webui', async (importOriginal) => {
  const registry = {
    loading: false,
    error: null,
    preferences: { hidden: [], order: [] },
    extensions: [
      {
        schema_version: 2,
        plugin_id: 'test.plugin',
        workspace_title: 'Test',
        pages: [
          {
            id: 'jobs',
            title: 'Jobs',
            mode: 'declarative',
            description: '',
            placement: 'workspace',
            icon: 'chart',
            poll_interval_seconds: 5,
            queries: {
              jobs: {
                api: 'jobs',
                version: '1',
                parameters: { job_id: { type: 'string', required: false } },
                confirmation: null,
              },
            },
            actions: { add: { api: 'add', version: '1', parameters: {}, confirmation: null } },
            content: [
              { type: 'input', name: 'job_id', label: 'Job', value: null },
              {
                type: 'button',
                label: 'Start',
                action: 'add',
                value: null,
                children: [],
                name: null,
              },
            ],
          },
        ],
      },
    ],
  }
  return {
    ...(await importOriginal<typeof import('@/lib/plugin-webui')>()),
    usePluginWebUI: () => registry,
    invokePluginWebUI: mocks.invoke,
  }
})

describe('background job page polling', () => {
  it('allows actions during a pending poll and aborts the obsolete query', async () => {
    vi.useFakeTimers()
    mocks.invoke.mockReset().mockResolvedValue({ rows: [] })
    let view: ReturnType<typeof render>
    try {
      await act(async () => {
        view = render(<PluginWebUIPage />)
      })
      let signal: AbortSignal
      mocks.invoke.mockImplementationOnce((...args) => {
        signal = args[6]
        return new Promise(() => {})
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000)
      })
      expect(screen.getByRole('button', { name: 'Start' })).not.toBeDisabled()
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: 'Start' }))
      })
      expect(signal!.aborted).toBe(true)
      expect(mocks.invoke.mock.calls.some((args) => args[2] === 'actions')).toBe(true)
    } finally {
      view!.unmount()
      vi.useRealTimers()
    }
  })

  it('does not interpret business results as navigation instructions', async () => {
    window.history.replaceState(
      null,
      '',
      '/extensions/test.plugin/jobs?state=%7B%22job_id%22%3A%22old%22%7D'
    )
    mocks.invoke
      .mockReset()
      .mockImplementation(async (_plugin, _page, kind) =>
        kind === 'actions'
          ? { navigate_page: 'jobs', navigate_params: { job_id: 'new', unknown: 'ignored' } }
          : { rows: [] }
      )
    const view = render(<PluginWebUIPage />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start' })).not.toBeDisabled())
    expect(mocks.invoke.mock.calls[0][4]).toEqual({ job_id: 'old' })
    fireEvent.click(screen.getByRole('button', { name: 'Start' }))
    await screen.findByText('pluginWebUI.completed')
    expect(new URLSearchParams(window.location.search).get('state')).toBe(
      JSON.stringify({ job_id: 'old' })
    )
    expect(mocks.invoke.mock.calls.at(-1)![4]).toEqual({ job_id: 'old' })
    view.unmount()
    window.history.replaceState(null, '', '/')
  })
  it('polls queries and stops when leaving the page', async () => {
    vi.useFakeTimers()
    mocks.invoke.mockResolvedValue({ rows: [] })
    try {
      let view: ReturnType<typeof render>
      await act(async () => {
        view = render(<PluginWebUIPage />)
      })
      const before = mocks.invoke.mock.calls.length
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000)
      })
      expect(mocks.invoke.mock.calls.length).toBeGreaterThan(before)
      view!.unmount()
      const stopped = mocks.invoke.mock.calls.length
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10000)
      })
      expect(mocks.invoke.mock.calls.length).toBe(stopped)
    } finally {
      vi.useRealTimers()
    }
  })
})
