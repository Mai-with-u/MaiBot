import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { MemoryConfigEditor } from '../MemoryConfigEditor'
import type { PluginConfigSchema } from '@/lib/plugin-api'

describe('MemoryConfigEditor', () => {
  it('does not render hidden fields', () => {
    const schema: PluginConfigSchema = {
      plugin_id: 'a_memorix',
      plugin_info: {
        name: 'A_Memorix',
        version: '2.0.0',
        description: '',
        author: 'A_Dawn',
      },
      layout: {
        type: 'tabs',
        tabs: [
          {
            id: 'basic',
            title: '基础',
            sections: ['plugin'],
            order: 1,
          },
        ],
      },
      sections: {
        plugin: {
          name: 'plugin',
          title: '子系统状态',
          collapsed: false,
          order: 1,
          fields: {
            enabled: {
              name: 'enabled',
              type: 'boolean',
              default: true,
              description: '',
              label: '启用 A_Memorix',
              ui_type: 'switch',
              required: false,
              hidden: false,
              disabled: false,
              order: 1,
            },
            restricted: {
              name: 'restricted',
              type: 'string',
              default: 'hidden',
              description: '',
              label: '隐藏字段',
              ui_type: 'text',
              required: false,
              hidden: true,
              disabled: false,
              order: 2,
            },
          },
        },
      },
    }

    render(<MemoryConfigEditor schema={schema} config={{ plugin: {} }} onChange={vi.fn()} />)

    expect(screen.getByText('启用 A_Memorix')).toBeInTheDocument()
    expect(screen.queryByText('隐藏字段')).not.toBeInTheDocument()
  })
})
