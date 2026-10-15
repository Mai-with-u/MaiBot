import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { PluginWebUIRenderer } from '@/components/plugin-webui-renderer'
import { resolveNodeValue, validateRenderSize } from '@/lib/plugin-webui'
import type { WebUINode } from '@/lib/plugin-webui'

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))

const callbacks = () => ({ values: {}, busy: false, onChange: vi.fn(), onAction: vi.fn() })

describe('simple plugin components', () => {
  it('renders text as text, including log output', () => {
    const text = '<img src=x onerror=alert(1)>'
    const { container } = render(
      <PluginWebUIRenderer
        nodes={[
          { type: 'text', value: text },
          { type: 'text', label: 'Log', value: text, format: 'log' },
        ]}
        data={{}}
        {...callbacks()}
      />
    )
    expect(screen.getByText(text, { selector: 'p' })).toBeInTheDocument()
    expect(screen.getByLabelText('Log')).toHaveValue(text)
    expect(screen.getByLabelText('Log')).toHaveAttribute('readonly')
    expect(container.querySelector('img')).toBeNull()
  })

  it('resolves only own properties and distinguishes unselected rows from invalid queries', () => {
    const node: WebUINode = { type: 'text', value: { source: 'summary', field: 'totals.count' } }
    expect(resolveNodeValue(node, { summary: { totals: { count: 4 } } })).toBe(4)
    expect(() => resolveNodeValue(node, { summary: {} })).toThrow('Missing data field')
    expect(() =>
      resolveNodeValue({ type: 'text', value: { source: 'toString', field: '' } }, {})
    ).toThrow('Missing data source')
    expect(() =>
      resolveNodeValue(
        { type: 'text', value: { source: 'summary', field: 'toString' } },
        { summary: {} }
      )
    ).toThrow('Missing data field')
    expect(
      resolveNodeValue(
        { type: 'text', value: { scope: 'selection', source: 'row', field: 'id' } },
        {}
      )
    ).toBeNull()
  })

  it('disables declared actions while busy or when the condition matches', () => {
    const action = vi.fn()
    const nodes: WebUINode[] = [
      {
        type: 'button',
        label: 'Run',
        action: 'run',
        disabled_when: {
          reference: { source: 'status', field: 'ready' },
          operator: 'equals',
          expected: false,
        },
        disabled_reason: 'Not ready',
      },
    ]
    const props = { nodes, ...callbacks(), onAction: action }
    const view = render(<PluginWebUIRenderer {...props} data={{ status: { ready: true } }} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    expect(action).toHaveBeenCalledWith('run', { selection: {} })
    view.rerender(<PluginWebUIRenderer {...props} data={{ status: { ready: false } }} />)
    expect(screen.getByRole('button', { name: 'Run' })).toBeDisabled()
    expect(screen.getByText('Not ready')).toBeInTheDocument()
    view.rerender(<PluginWebUIRenderer {...props} busy data={{ status: { ready: true } }} />)
    expect(screen.getByRole('button', { name: 'Run' })).toBeDisabled()
  })

  it('paginates tables without mounting the whole response', () => {
    const rows = Array.from({ length: 60 }, (_, i) => ({ name: 'row-' + i }))
    render(
      <PluginWebUIRenderer
        nodes={[
          {
            type: 'table',
            value: { source: 'rows', field: '' },
            columns: [{ field: 'name', label: 'Name' }],
          },
        ]}
        data={{ rows }}
        {...callbacks()}
      />
    )
    expect(screen.getByText('row-0')).toBeInTheDocument()
    expect(screen.queryByText('row-50')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'pluginWebUI.next' }))
    expect(screen.getByText('row-50')).toBeInTheDocument()
    expect(screen.queryByText('row-0')).not.toBeInTheDocument()
  })

  it('invalidates selected records and details when the data snapshot changes', () => {
    const row = { name: 'Alice', id: 9 }
    const props = {
      ...callbacks(),
      nodes: [
        {
          type: 'table',
          selection: 'record',
          detail: 'details',
          value: { source: 'rows', field: '' },
          columns: [{ field: 'name', label: 'Name' }],
        },
        {
          type: 'dialog',
          name: 'details',
          label: 'Details',
          children: [
            { type: 'text', value: { scope: 'selection', source: 'record', field: 'name' } },
            { type: 'button', label: 'Remove', action: 'remove' },
          ],
        },
      ] as WebUINode[],
    }
    const view = render(
      <PluginWebUIRenderer {...props} data={{ rows: [row] }} selectionRevision={0} />
    )
    fireEvent.keyDown(screen.getByText('Alice').closest('tr')!, { key: 'Enter' })
    expect(screen.getByRole('dialog')).toHaveTextContent('Alice')
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    expect(props.onAction).toHaveBeenCalledWith('remove', { selection: { record: row } })
    view.rerender(<PluginWebUIRenderer {...props} data={{ rows: [] }} selectionRevision={1} />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByText('Alice')).not.toBeInTheDocument()
  })

  it('supports simple visibility and collapsible content', () => {
    const nodes: WebUINode[] = [
      {
        type: 'text',
        value: 'No records',
        when: { reference: { source: 'rows', field: '' }, operator: 'empty', expected: null },
      },
      {
        type: 'collapsible',
        label: 'Details',
        children: [{ type: 'text', value: 'Detail content' }],
        when: { reference: { source: 'rows', field: '' }, operator: 'not_empty', expected: null },
      },
    ]
    const props = { nodes, ...callbacks() }
    const view = render(<PluginWebUIRenderer {...props} data={{ rows: [] }} />)
    expect(screen.getByText('No records')).toBeInTheDocument()
    view.rerender(<PluginWebUIRenderer {...props} data={{ rows: [{}] }} />)
    expect(screen.queryByText('No records')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Details' }))
    expect(screen.getByText('Detail content')).toBeInTheDocument()
  })

  it('rejects invalid images instead of treating them as missing data', () => {
    const props = {
      nodes: [{ type: 'image', value: { source: 'image', field: '' } }] as WebUINode[],
      ...callbacks(),
    }
    const view = render(<PluginWebUIRenderer {...props} data={{ image: null }} />)
    expect(screen.getByText('pluginWebUI.empty')).toBeInTheDocument()
    view.unmount()
    expect(() =>
      render(<PluginWebUIRenderer {...props} data={{ image: 'https://example.test/image.jpg' }} />)
    ).toThrow('bounded JPEG')
  })

  it('rejects oversized trees and unknown components', () => {
    expect(() =>
      validateRenderSize(Array.from({ length: 201 }, () => ({ type: 'text', value: 'x' })))
    ).toThrow('exceeds limits')
    expect(() =>
      render(
        <PluginWebUIRenderer
          {...callbacks()}
          data={{}}
          nodes={[{ type: 'repeat' } as unknown as WebUINode]}
        />
      )
    ).toThrow('Unsupported component')
  })
})
