import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { PluginWebUIRenderer } from '@/components/plugin-webui-renderer'
import { resolveNodeValue, validateRenderSize } from '@/lib/plugin-webui'
import type { WebUINode } from '@/lib/plugin-webui'

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))

function node(overrides: Partial<WebUINode>): WebUINode {
  return {
    type: 'text',
    label: null,
    value: null,
    children: [],
    columns: null,
    name: null,
    options: [],
    action: null,
    variant: 'primary',
    chart_type: 'line',
    x: null,
    y: null,
    ...overrides,
  }
}

describe('plugin WebUI renderer', () => {
  it('keeps unavailable actions visible with an explanation', () => {
    render(<PluginWebUIRenderer nodes={[node({type: 'button', label: 'Train', action: 'train',
      disabled_when: {reference: {source: 'runtime', field: 'ready'}, operator: 'equals', expected: false}, disabled_reason: 'Add negative examples first'})]}
      data={{runtime: {ready: false}}} values={{}} busy={false} onChange={vi.fn()} onAction={vi.fn()} />)
    expect(screen.getByRole('button', {name: 'Train'})).toBeDisabled()
    expect(screen.getByText('Add negative examples first')).toBeInTheDocument()
  })

  it('renders progress and button choices with dynamic action labels', () => {
    const change = vi.fn()
    render(<PluginWebUIRenderer nodes={[
      node({type: 'progress', label: 'Training progress', value: 42}),
      node({type: 'select', presentation: 'buttons', name: 'identity', label: 'Identity', value: 'self', options: [{label: 'Self',value: 'self'},{label: 'Other',value: 'other'}]}),
      node({type: 'button', label: 'Manage', action: 'manage', value: {source: 'runtime', field: 'label'}}),
    ]} data={{runtime: {label: 'Uninstall CPU'}}} values={{identity: 'self'}} busy={false} onChange={change} onAction={vi.fn()} />)
    expect(screen.getByRole('progressbar', {name: 'Training progress'})).toHaveAttribute('value', '42')
    expect(screen.getByRole('radio', {name: 'Self'})).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(screen.getByRole('radio', {name: 'Other'}))
    expect(change).toHaveBeenCalledWith('identity', 'other')
    expect(screen.getByRole('button', {name: 'Uninstall CPU'})).toBeInTheDocument()
  })
  it('opens an ordinary dialog containing a button selection', () => {
    const change = vi.fn()
    render(<PluginWebUIRenderer nodes={[
      node({type: 'button', label: 'Choose identity', detail: 'identity'}),
      node({type: 'dialog', name: 'identity', label: 'Identity', children: [node({type: 'select', presentation: 'buttons', name: 'label', value: 'self', options: [{label: 'Bot', value: 'self'}, {label: 'Other', value: 'other'}]})]})
    ]} data={{}} values={{label: 'self'}} busy={false} onChange={change} onAction={vi.fn()} />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', {name: 'Choose identity'}))
    fireEvent.click(screen.getByRole('radio', {name: 'Other'}))
    expect(change).toHaveBeenCalledWith('label', 'other')
  })

  it('keeps multi-selection across pages and clears after a successful batch', () => {
    const action = vi.fn()
    const nodes = [node({type: 'multi_select', selection: 'batch', value: {source: 'pictures', field: 'rows'}}),
      node({type: 'repeat', name: 'picture', value: {source: 'pictures', field: 'rows'}, children: [node({type: 'checkbox', selection: 'batch', value: {scope: 'item', source: 'picture', field: 'id'}})]}),
      node({type: 'button', action: 'apply', label: 'Apply'})]
    const props = {nodes, values: {}, busy: false, onChange: vi.fn(), onAction: action}
    const view = render(<PluginWebUIRenderer {...props} data={{pictures: {rows: [{id: 'one'}]}}} selectionRevision={0} />)
    fireEvent.click(screen.getByRole('checkbox'))
    view.rerender(<PluginWebUIRenderer {...props} data={{pictures: {rows: [{id: 'two'}]}}} selectionRevision={1} />)
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', {name: 'Apply'}))
    expect(action.mock.calls[0][1].selection.batch).toEqual({ids: 'one,two', count: 2})
    view.rerender(<PluginWebUIRenderer {...props} data={{pictures: {rows: [{id: 'two'}]}}} selectionRevision={2} clearSelection="batch" />)
    expect(screen.getByRole('checkbox')).not.toBeChecked()
  })

  it('renders plugin text as text without interpreting HTML', () => {
    const content = '<img src=x onerror=alert(1)>'
    const { container } = render(
      <PluginWebUIRenderer
        nodes={[node({ value: content })]}
        data={{}}
        values={{}}
        busy={false}
        onChange={vi.fn()}
        onAction={vi.fn()}
      />
    )
    expect(screen.getByText(content)).toBeInTheDocument()
    expect(container.querySelector('img')).toBeNull()
  })

  it('resolves only own data fields and exposes invalid bindings', () => {
    const reference = node({ value: { source: 'summary', field: 'totals.count' } })
    expect(resolveNodeValue(reference, { summary: { totals: { count: 4 } } })).toBe(4)
    expect(() => resolveNodeValue(reference, { summary: {} })).toThrow('Missing data field')
    expect(() =>
      resolveNodeValue(node({ value: { source: 'summary', field: 'toString' } }), { summary: {} })
    ).toThrow('Missing data field')
  })

  it('dispatches declared actions and disables buttons during requests', () => {
    const onAction = vi.fn()
    const nodes = [node({ type: 'button', label: 'Recalculate', action: 'recalculate' })]
    const { rerender } = render(
      <PluginWebUIRenderer
        nodes={nodes}
        data={{}}
        values={{}}
        busy={false}
        onChange={vi.fn()}
        onAction={onAction}
      />
    )
    fireEvent.click(screen.getByRole('button', { name: 'Recalculate' }))
    expect(onAction).toHaveBeenCalledWith('recalculate', { selection: {}, item: undefined })
    rerender(
      <PluginWebUIRenderer
        nodes={nodes}
        data={{}}
        values={{}}
        busy
        onChange={vi.fn()}
        onAction={onAction}
      />
    )
    expect(screen.getByRole('button', { name: 'Recalculate' })).toBeDisabled()
  })

  it('paginates tables instead of rendering unbounded result rows', () => {
    const rows = Array.from({ length: 60 }, (_, i) => ({ name: `row-${i}` }))
    render(
      <PluginWebUIRenderer
        nodes={[
          node({
            type: 'table',
            value: { source: 'rows', field: '' },
            columns: [{ field: 'name', label: 'Name' }],
          }),
        ]}
        data={{ rows }}
        values={{}}
        busy={false}
        onChange={vi.fn()}
        onAction={vi.fn()}
      />
    )
    expect(screen.getByText('row-0')).toBeInTheDocument()
    expect(screen.queryByText('row-50')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'pluginWebUI.next' }))
    expect(screen.getByText('row-50')).toBeInTheDocument()
    expect(screen.queryByText('row-0')).not.toBeInTheDocument()
  })
})

describe('plugin gallery', () => {
  it('renders bounded raster previews and secure artwork links as plain text', () => {
    const { container } = render(
      <PluginWebUIRenderer
        nodes={[node({ type: 'gallery', columns: 3, value: { source: 'images', field: '' } })]}
        data={{
          images: [
            {
              title: '<script>alert(1)</script>',
              thumbnail: 'data:image/jpeg;base64,aGVsbG8=',
              url: 'https://www.pixiv.net/artworks/1',
              badge: '模型优秀',
            },
            {
              title: 'SVG',
              thumbnail: 'data:image/svg+xml;base64,aGVsbG8=',
              url: 'javascript:alert(1)',
            },
            {
              title: 'Oversized',
              thumbnail: 'data:image/jpeg;base64,' + 'a'.repeat(25000),
              url: 'https://user:secret@example.com/',
            },
            { title: 'Remote', thumbnail: 'https://example.com/tracking.jpg' },
          ],
        }}
        values={{}}
        busy={false}
        onChange={vi.fn()}
        onAction={vi.fn()}
      />
    )
    expect(container.querySelectorAll('img')).toHaveLength(1)
    expect(container.querySelectorAll('a')).toHaveLength(1)
    expect(container.querySelector('a')).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.getByText('<script>alert(1)</script>')).toBeInTheDocument()
    expect(container.querySelector('script')).toBeNull()
  })
})

describe('details and dynamic content', () => {
  it('opens selected row details with keyboard and dispatches its context', () => {
    const onAction = vi.fn()
    const row = { name: 'Alice', message: '<script>full message</script>', id: 9 }
    render(
      <PluginWebUIRenderer
        nodes={[
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
            label: 'Record detail',
            children: [
              node({ value: { scope: 'selection', source: 'record', field: 'message' } }),
              node({ type: 'button', label: 'Remove', action: 'remove' }),
            ],
          }),
        ]}
        data={{ summary: { rows: [row] } }}
        values={{}}
        busy={false}
        onChange={vi.fn()}
        onAction={onAction}
      />
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    fireEvent.keyDown(screen.getByText('Alice').closest('tr')!, { key: 'Enter' })
    expect(screen.getByRole('dialog')).toHaveTextContent('<script>full message</script>')
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    expect(onAction).toHaveBeenCalledWith('remove', { selection: { record: row }, item: undefined })
    expect(document.querySelector('script')).toBeNull()
  })

  it('renders bounded array cards, controls empty state and expands sections', () => {
    const nodes = [
      node({
        type: 'text',
        value: 'No rows',
        when: {
          reference: { source: 'summary', field: 'rows' },
          operator: 'empty',
          expected: null,
        },
      }),
      node({
        type: 'collapsible',
        label: 'Expand cards',
        when: {
          reference: { source: 'summary', field: 'rows' },
          operator: 'not_empty',
          expected: null,
        },
        children: [
          node({
            type: 'repeat',
            name: 'entry',
            max_items: 2,
            value: { source: 'summary', field: 'rows' },
            children: [
              node({
                type: 'card',
                children: [node({ value: { scope: 'item', source: 'entry', field: 'name' } })],
              }),
            ],
          }),
        ],
      }),
    ]
    const props = { nodes, values: {}, busy: false, onChange: vi.fn(), onAction: vi.fn() }
    const view = render(<PluginWebUIRenderer {...props} data={{ summary: { rows: [] } }} />)
    expect(screen.getByText('No rows')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Expand cards' })).not.toBeInTheDocument()
    view.rerender(
      <PluginWebUIRenderer
        {...props}
        data={{ summary: { rows: [{ name: 'First' }, { name: 'Second' }, { name: 'Third' }] } }}
      />
    )
    expect(screen.queryByText('No rows')).not.toBeInTheDocument()
    expect(screen.queryByText('First')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Expand cards' }))
    expect(screen.getByText('First')).toBeInTheDocument()
    expect(screen.getByText('Second')).toBeInTheDocument()
    expect(screen.queryByText('Third')).not.toBeInTheDocument()
  })

  it('rejects nested repeat amplification and inherited source properties', () => {
    const inner = node({
      type: 'repeat',
      name: 'inner',
      max_items: 100,
      value: { source: 'rows', field: '' },
      children: [node({ value: 'item' })],
    })
    const outer = node({
      type: 'repeat',
      name: 'outer',
      max_items: 100,
      value: { source: 'rows', field: '' },
      children: [inner],
    })
    expect(() =>
      validateRenderSize([outer], { rows: Array.from({ length: 100 }, () => ({})) })
    ).toThrow('exceeds limits')
    expect(() => resolveNodeValue(node({ value: { source: 'toString', field: '' } }), {})).toThrow(
      'Missing data source'
    )
    expect(
      resolveNodeValue(node({ value: { scope: 'selection', source: 'record', field: 'name' } }), {})
    ).toBeNull()
  })
})
