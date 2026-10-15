import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { WebUIExtension, DeclarativePage, WebUINode } from '@/lib/plugin-webui'
import { PluginWebUIPage } from '@/routes/plugin-webui'

const mocks = vi.hoisted(() => ({ extensions: [] as WebUIExtension[], invoke: vi.fn() }))
vi.mock('@tanstack/react-router', () => ({
  useParams: () => ({ pluginId: 'test.plugin', pageId: 'overview' }),
  Link: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
}))
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))
vi.mock('@/lib/plugin-webui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/plugin-webui')>()),
  usePluginWebUI: () => ({
    extensions: mocks.extensions,
    loading: false,
    error: null,
    preferences: { hidden: [], order: [] },
  }),
  invokePluginWebUI: mocks.invoke,
}))

function node(overrides: Partial<WebUINode>): WebUINode {
  return { type: 'text', value: null, ...overrides } as WebUINode
}
function page(overrides: Partial<DeclarativePage> = {}): DeclarativePage {
  return {
    id: 'overview',
    mode: 'declarative',
    title: 'Overview',
    description: '',
    placement: 'workspace',
    icon: 'puzzle',
    queries: {
      summary: { api: 'summary', version: '1', parameters: {}, confirmation: null },
    },
    actions: {},
    content: [node({ type: 'stat', label: 'Total', value: { source: 'summary', field: 'count' } })],
    ...overrides,
  }
}
function install(value: DeclarativePage) {
  mocks.extensions = [
    { schema_version: 2, plugin_id: 'test.plugin', workspace_title: 'Statistics', pages: [value] },
  ]
}

describe('plugin page lifecycle', () => {
  beforeEach(() => {
    mocks.invoke.mockResolvedValue({ count: 3 })
    install(page())
  })

  it('keeps inputs usable when a required query argument has not been filled', async () => {
    install(
      page({
        queries: {
          summary: {
            api: 'summary',
            version: '1',
            confirmation: null,
            parameters: {
              term: {
                type: 'string',
                required: true,
                max_length: 100,
                minimum: null,
                maximum: null,
                choices: [],
              },
            },
          },
        },
        content: [
          node({ type: 'input', label: 'Search', name: 'term' }),
          node({ type: 'stat', value: { source: 'summary', field: 'count' } }),
        ],
      })
    )
    mocks.invoke.mockImplementation((_plugin, _page, _kind, _name, args) =>
      args.term ? Promise.resolve({ count: 3 }) : Promise.reject(new Error('Missing term'))
    )
    render(<PluginWebUIPage />)
    expect(await screen.findByText('Error: Missing term')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Search'), { target: { value: 'hello' } })
    fireEvent.click(screen.getByRole('button', { name: 'pluginWebUI.refresh' }))
    expect(await screen.findByText('3')).toBeInTheDocument()
    expect(mocks.invoke).toHaveBeenLastCalledWith(
      'test.plugin',
      'overview',
      'queries',
      'summary',
      { term: 'hello' },
      false,
      expect.any(AbortSignal)
    )
  })

  it('requires host confirmation before dispatching a declared write action', async () => {
    install(
      page({
        actions: {
          reset: { api: 'reset', version: '1', parameters: {}, confirmation: 'Reset all counts?' },
        },
        content: [node({ type: 'button', label: 'Reset', action: 'reset', variant: 'danger' })],
      })
    )
    render(<PluginWebUIPage />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reset' })).not.toBeDisabled())
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }))
    expect(screen.getByText('Reset all counts?')).toBeInTheDocument()
    expect(mocks.invoke.mock.calls.filter((call) => call[2] === 'actions')).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'pluginWebUI.confirm' }))
    await waitFor(() =>
      expect(mocks.invoke).toHaveBeenCalledWith(
        'test.plugin',
        'overview',
        'actions',
        'reset',
        {},
        true,
        expect.any(AbortSignal)
      )
    )
    expect(await screen.findByText('pluginWebUI.completed')).toBeInTheDocument()
  })

  it('aborts the browser request on unmount without retrying the write', async () => {
    install(
      page({
        queries: {},
        actions: { run: { api: 'run', version: '1', parameters: {}, confirmation: null } },
        content: [node({ type: 'button', label: 'Run', action: 'run' })],
      })
    )
    mocks.invoke.mockImplementation(() => new Promise(() => {}))
    const view = render(<PluginWebUIPage />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Run' })).not.toBeDisabled())
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    const signal = mocks.invoke.mock.calls[0][6] as AbortSignal
    view.unmount()
    expect(signal.aborted).toBe(true)
    expect(mocks.invoke).toHaveBeenCalledOnce()
  })
})

describe('automatic query pagination', () => {
  it('loads page one, requests the next page, and resets pagination when the filter changes', async () => {
    const parameter = {
      required: false,
      max_length: 100,
      minimum: null,
      maximum: null,
      choices: [],
    }
    install(
      page({
        auto_refresh: true,
        queries: {
          summary: {
            api: 'summary',
            version: '1',
            confirmation: null,
            parameters: {
              page: { ...parameter, type: 'integer' },
              filter: { ...parameter, type: 'string' },
            },
          },
        },
        content: [
          node({ type: 'input', label: 'Filter', name: 'filter', value: 'all' }),
          node({
            type: 'pagination',
            name: 'page',
            value: { source: 'summary', field: 'pagination' },
          }),
        ],
      })
    )
    mocks.invoke.mockImplementation((_plugin, _page, _kind, _name, args) =>
      Promise.resolve({ pagination: { page: args.page, pages: 3, total: 30 } })
    )
    render(<PluginWebUIPage />)
    const next = await screen.findByRole('button', { name: 'pluginWebUI.next' })
    await waitFor(() => expect(next).not.toBeDisabled())
    expect(mocks.invoke.mock.calls.at(-1)?.[4]).toEqual({ page: 1, filter: 'all' })
    fireEvent.click(next)
    await waitFor(() =>
      expect(mocks.invoke.mock.calls.at(-1)?.[4]).toEqual({ page: 2, filter: 'all' })
    )
    await waitFor(() => expect(screen.getByLabelText('Filter')).not.toBeDisabled())
    fireEvent.change(screen.getByLabelText('Filter'), { target: { value: 'excellent' } })
    await waitFor(() =>
      expect(mocks.invoke.mock.calls.at(-1)?.[4]).toEqual({ page: 1, filter: 'excellent' })
    )
  })
})

describe('selected row action parameters', () => {
  it('keeps confirmation arguments frozen when the form changes and clears details after refresh', async () => {
    const row = { id: 7, name: 'Selected row' }
    const parameter = {
      type: 'integer' as const,
      required: true,
      max_length: 100,
      minimum: null,
      maximum: null,
      choices: [],
    }
    install(
      page({
        actions: {
          remove: {
            api: 'remove',
            version: '1',
            parameters: { id: parameter, count: parameter },
            arguments: { id: { scope: 'selection', source: 'record', field: 'id' } },
            confirmation: 'Remove this row?',
          },
        },
        content: [
          node({ type: 'input', name: 'count', label: 'Count', value: 1 }),
          node({
            type: 'table',
            selection: 'record',
            detail: 'details',
            value: { source: 'summary', field: 'rows' },
            columns: [{ field: 'name', label: 'Name' }],
          }),
          node({
            type: 'dialog',
            name: 'details',
            label: 'Details',
            children: [node({ type: 'button', label: 'Remove row', action: 'remove' })],
          }),
        ],
      })
    )
    mocks.invoke.mockImplementation((_plugin, _page, kind) =>
      Promise.resolve(kind === 'queries' ? { rows: [row] } : { removed: 7 })
    )
    render(<PluginWebUIPage />)
    const cell = await screen.findByText('Selected row')
    await waitFor(() => expect(screen.getByLabelText('Count')).not.toBeDisabled())
    fireEvent.click(cell.closest('tr')!)
    fireEvent.click(screen.getByRole('button', { name: 'Remove row' }))
    expect(screen.getByText('Remove this row?')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Count'), { target: { value: '2' } })
    fireEvent.click(screen.getByRole('button', { name: 'pluginWebUI.confirm' }))
    await waitFor(() =>
      expect(mocks.invoke).toHaveBeenCalledWith(
        'test.plugin',
        'overview',
        'actions',
        'remove',
        { id: 7, count: 1 },
        true,
        expect.any(AbortSignal)
      )
    )
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('rejects object projections instead of passing whole rows to an API', async () => {
    const parameter = {
      type: 'string' as const,
      required: true,
      max_length: 100,
      minimum: null,
      maximum: null,
      choices: [],
    }
    install(
      page({
        actions: {
          run: {
            api: 'run',
            version: '1',
            parameters: { payload: parameter },
            confirmation: null,
            arguments: { payload: { scope: 'query', source: 'summary', field: 'payload' } },
          },
        },
        content: [node({ type: 'button', label: 'Run projection', action: 'run' })],
      })
    )
    mocks.invoke.mockResolvedValue({ payload: { id: 1 } })
    render(<PluginWebUIPage />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Run projection' })).not.toBeDisabled()
    )
    fireEvent.click(screen.getByRole('button', { name: 'Run projection' }))
    expect(
      await screen.findByText('Error: Action argument must be scalar: payload')
    ).toBeInTheDocument()
    expect(mocks.invoke.mock.calls.filter((call) => call[2] === 'actions')).toHaveLength(0)
  })
})
