import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/dom'
import { render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { AdvancedSettingsButton, DynamicConfigForm } from '../DynamicConfigForm'
import { FieldHookRegistry } from '@/lib/field-hooks'
import type { ConfigSchema, FieldSchema } from '@/types/config-schema'
import type { FieldHookComponentProps } from '@/lib/field-hooks'

vi.mock('@tanstack/react-router', () => ({
  Link: (props: { children?: React.ReactNode; className?: string; to: string }) => (
    <a className={props.className} href={props.to}>
      {props.children}
    </a>
  ),
}))

function makeField(name: string, overrides: Partial<FieldSchema> = {}): FieldSchema {
  return {
    name,
    type: 'string',
    label: name,
    description: `${name} desc`,
    required: false,
    ...overrides,
  }
}

describe('DynamicConfigForm', () => {
  describe('basic rendering', () => {
    it('renders simple fields', () => {
      const schema: ConfigSchema = {
        className: 'TestConfig',
        classDoc: 'Test configuration',
        fields: [
          {
            name: 'field1',
            type: 'string',
            label: 'Field 1',
            description: 'First field',
            required: false,
            default: 'value1',
          },
          {
            name: 'field2',
            type: 'boolean',
            label: 'Field 2',
            description: 'Second field',
            required: false,
            default: false,
          },
        ],
      }
      const values = { field1: 'value1', field2: false }
      const onChange = vi.fn()

      render(<DynamicConfigForm schema={schema} values={values} onChange={onChange} />)

      expect(screen.getByText('Field 1')).toBeInTheDocument()
      expect(screen.getByText('Field 2')).toBeInTheDocument()
    })

    it('renders nested schema', () => {
      const schema: ConfigSchema = {
        className: 'MainConfig',
        classDoc: 'Main configuration',
        fields: [
          {
            name: 'top_field',
            type: 'string',
            label: 'Top Field',
            description: 'Top level field',
            required: false,
          },
        ],
        nested: {
          sub_config: {
            className: 'SubConfig',
            classDoc: 'Sub configuration',
            fields: [
              {
                name: 'nested_field',
                type: 'number',
                label: 'Nested Field',
                description: 'Nested field',
                required: false,
                default: 42,
              },
            ],
          },
        },
      }
      const values = {
        top_field: 'top',
        sub_config: {
          nested_field: 42,
        },
      }
      const onChange = vi.fn()

      render(<DynamicConfigForm schema={schema} values={values} onChange={onChange} />)

      expect(screen.getByText('Top Field')).toBeInTheDocument()
      expect(screen.getByText('Sub configuration')).toBeInTheDocument()
      expect(screen.getByText('Nested Field')).toBeInTheDocument()
      expect(document.querySelector('[data-dynamic-field="sub_config.nested_field"]')).toBeInTheDocument()
    })

    it('does not add an extra collapse button for the A_Memorix root section', () => {
      const schema: ConfigSchema = {
        className: 'RootConfig',
        classDoc: 'Root configuration',
        fields: [],
        nested: {
          a_memorix: {
            className: 'AMemorixConfig',
            classDoc: '记忆',
            fields: [
              {
                name: 'enabled',
                type: 'boolean',
                label: '启用记忆',
                description: '是否启用记忆',
                required: false,
              },
            ],
            nested: {
              memory: {
                className: 'AMemorixMemoryConfig',
                classDoc: '记忆演化',
                fields: [
                  {
                    name: 'enabled',
                    type: 'boolean',
                    label: '启用记忆演化',
                    description: '是否启用记忆演化',
                    required: false,
                  },
                ],
              },
            },
          },
        },
      }
      const values = {
        a_memorix: {
          enabled: true,
          memory: {
            enabled: true,
          },
        },
      }

      render(<DynamicConfigForm schema={schema} values={values} onChange={vi.fn()} />)

      expect(screen.getByText('记忆')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: '收起' })).not.toBeInTheDocument()
    })
  })

  describe('Hook system', () => {
    it('renders Hook component in replace mode', () => {
      const TestHookComponent: React.FC<FieldHookComponentProps> = ({ fieldPath, value }) => {
        return <div data-testid="hook-component">Hook: {fieldPath} = {String(value)}</div>
      }

      const hooks = new FieldHookRegistry()
      hooks.register('hooked_field', TestHookComponent, 'replace')

      const schema: ConfigSchema = {
        className: 'TestConfig',
        classDoc: 'Test configuration',
        fields: [
          {
            name: 'hooked_field',
            type: 'string',
            label: 'Hooked Field',
            description: 'A field with hook',
            required: false,
          },
          {
            name: 'normal_field',
            type: 'string',
            label: 'Normal Field',
            description: 'A normal field',
            required: false,
          },
        ],
      }
      const values = { hooked_field: 'test', normal_field: 'normal' }
      const onChange = vi.fn()

      render(<DynamicConfigForm schema={schema} values={values} onChange={onChange} hooks={hooks} />)

      expect(screen.getByTestId('hook-component')).toBeInTheDocument()
      expect(screen.getByText('Hook: hooked_field = test')).toBeInTheDocument()
      expect(screen.queryByText('Hooked Field')).not.toBeInTheDocument()
      expect(screen.getByText('Normal Field')).toBeInTheDocument()
    })

    it('renders Hook component in wrapper mode', () => {
      const WrapperHookComponent: React.FC<FieldHookComponentProps> = ({ fieldPath, children }) => {
        return (
          <div data-testid="wrapper-hook">
            <div>Wrapper for: {fieldPath}</div>
            {children}
          </div>
        )
      }

      const hooks = new FieldHookRegistry()
      hooks.register('wrapped_field', WrapperHookComponent, 'wrapper')

      const schema: ConfigSchema = {
        className: 'TestConfig',
        classDoc: 'Test configuration',
        fields: [
          {
            name: 'wrapped_field',
            type: 'string',
            label: 'Wrapped Field',
            description: 'A wrapped field',
            required: false,
          },
        ],
      }
      const values = { wrapped_field: 'test' }
      const onChange = vi.fn()

      render(<DynamicConfigForm schema={schema} values={values} onChange={onChange} hooks={hooks} />)

      expect(screen.getByTestId('wrapper-hook')).toBeInTheDocument()
      expect(screen.getByText('Wrapper for: wrapped_field')).toBeInTheDocument()
      expect(screen.getByText('Wrapped Field')).toBeInTheDocument()
    })

    it('passes correct props to Hook component', () => {
      const TestHookComponent: React.FC<FieldHookComponentProps> = ({ fieldPath, value, onChange }) => {
        return (
          <div>
            <div data-testid="field-path">{fieldPath}</div>
            <div data-testid="field-value">{String(value)}</div>
            <button onClick={() => onChange?.('new_value')}>Change</button>
          </div>
        )
      }

      const hooks = new FieldHookRegistry()
      hooks.register('test_field', TestHookComponent, 'replace')

      const schema: ConfigSchema = {
        className: 'TestConfig',
        classDoc: 'Test configuration',
        fields: [
          {
            name: 'test_field',
            type: 'string',
            label: 'Test Field',
            description: 'A test field',
            required: false,
          },
        ],
      }
      const values = { test_field: 'original' }
      const onChange = vi.fn()

      render(<DynamicConfigForm schema={schema} values={values} onChange={onChange} hooks={hooks} />)

      expect(screen.getByTestId('field-path')).toHaveTextContent('test_field')
      expect(screen.getByTestId('field-value')).toHaveTextContent('original')
    })
  })

  describe('onChange propagation', () => {
    it('propagates onChange from simple field', async () => {
      const schema: ConfigSchema = {
        className: 'TestConfig',
        classDoc: 'Test configuration',
        fields: [
          {
            name: 'test_field',
            type: 'string',
            label: 'Test Field',
            description: 'A test field',
            required: false,
          },
        ],
      }
      const values = { test_field: '' }
      const onChange = vi.fn()

      render(<DynamicConfigForm schema={schema} values={values} onChange={onChange} />)

      const input = screen.getByRole('textbox')
      input.focus()
      await userEvent.keyboard('Hello')

      expect(onChange).toHaveBeenCalledTimes(5)
      expect(onChange.mock.calls.every(call => call[0] === 'test_field')).toBe(true)
      expect(onChange).toHaveBeenNthCalledWith(1, 'test_field', 'H')
      expect(onChange).toHaveBeenNthCalledWith(5, 'test_field', 'o')
    })

    it('propagates onChange from nested field with correct path', async () => {
      const schema: ConfigSchema = {
        className: 'MainConfig',
        classDoc: 'Main configuration',
        fields: [],
        nested: {
          sub_config: {
            className: 'SubConfig',
            classDoc: 'Sub configuration',
            fields: [
              {
                name: 'nested_field',
                type: 'string',
                label: 'Nested Field',
                description: 'Nested field',
                required: false,
              },
            ],
          },
        },
      }
      const values = {
        sub_config: {
          nested_field: '',
        },
      }
      const onChange = vi.fn()

      render(<DynamicConfigForm schema={schema} values={values} onChange={onChange} />)

      const input = screen.getByRole('textbox')
      input.focus()
      await userEvent.keyboard('Test')

      expect(onChange).toHaveBeenCalledTimes(4)
      expect(onChange.mock.calls.every(call => call[0] === 'sub_config.nested_field')).toBe(true)
      expect(onChange).toHaveBeenNthCalledWith(1, 'sub_config.nested_field', 'T')
      expect(onChange).toHaveBeenNthCalledWith(4, 'sub_config.nested_field', 't')
    })

    it('propagates onChange from Hook component', async () => {
      const TestHookComponent: React.FC<FieldHookComponentProps> = ({ onChange }) => {
        return <button onClick={() => onChange?.('hook_value')}>Set Value</button>
      }

      const hooks = new FieldHookRegistry()
      hooks.register('hooked_field', TestHookComponent, 'replace')

      const schema: ConfigSchema = {
        className: 'TestConfig',
        classDoc: 'Test configuration',
        fields: [
          {
            name: 'hooked_field',
            type: 'string',
            label: 'Hooked Field',
            description: 'A hooked field',
            required: false,
          },
        ],
      }
      const values = { hooked_field: '' }
      const onChange = vi.fn()
      const user = userEvent.setup()

      render(<DynamicConfigForm schema={schema} values={values} onChange={onChange} hooks={hooks} />)

      await user.click(screen.getByRole('button'))

      expect(onChange).toHaveBeenCalledWith('hooked_field', 'hook_value')
    })

    it('renders nested Hook component with full field path', async () => {
      const NestedHookComponent: React.FC<FieldHookComponentProps> = ({ fieldPath, onChange }) => {
        return (
          <button onClick={() => onChange?.([{ enabled: true }])}>
            {fieldPath}
          </button>
        )
      }

      const hooks = new FieldHookRegistry()
      hooks.register('mcp.servers', NestedHookComponent, 'replace')

      const schema: ConfigSchema = {
        className: 'RootConfig',
        classDoc: 'Root configuration',
        fields: [],
        nested: {
          mcp: {
            className: 'MCPConfig',
            classDoc: 'MCP 配置',
            fields: [
              {
                name: 'enable',
                type: 'boolean',
                label: '启用 MCP',
                description: '是否启用 MCP',
                required: false,
              },
              {
                name: 'servers',
                type: 'array',
                label: '服务器列表',
                description: '复杂对象数组',
                required: false,
                items: {
                  type: 'object',
                },
              },
            ],
            nested: {
              servers: {
                className: 'MCPServerItemConfig',
                classDoc: 'MCP 服务器项',
                fields: [],
              },
            },
          },
        },
      }
      const values = {
        mcp: {
          enable: true,
          servers: [],
        },
      }
      const onChange = vi.fn()
      const user = userEvent.setup()

      render(<DynamicConfigForm schema={schema} values={values} onChange={onChange} hooks={hooks} />)

      await user.click(screen.getByRole('button', { name: 'mcp.servers' }))

      expect(onChange).toHaveBeenCalledWith('mcp.servers', [{ enabled: true }])
    })
  })

  describe('edge cases', () => {
    it('renders with empty nested values', () => {
      const schema: ConfigSchema = {
        className: 'MainConfig',
        classDoc: 'Main configuration',
        fields: [],
        nested: {
          sub_config: {
            className: 'SubConfig',
            classDoc: 'Sub configuration',
            fields: [
              {
                name: 'nested_field',
                type: 'string',
                label: 'Nested Field',
                description: 'Nested field',
                required: false,
              },
            ],
          },
        },
      }
      const values = {}
      const onChange = vi.fn()

      render(<DynamicConfigForm schema={schema} values={values} onChange={onChange} />)

      expect(screen.getByText('Sub configuration')).toBeInTheDocument()
      expect(screen.getByText('Nested Field')).toBeInTheDocument()
    })

    it('uses default hook registry when not provided', () => {
      const schema: ConfigSchema = {
        className: 'TestConfig',
        classDoc: 'Test configuration',
        fields: [
          {
            name: 'test_field',
            type: 'string',
            label: 'Test Field',
            description: 'A test field',
            required: false,
          },
        ],
      }
      const values = { test_field: 'test' }
      const onChange = vi.fn()

      render(<DynamicConfigForm schema={schema} values={values} onChange={onChange} />)

      expect(screen.getByText('Test Field')).toBeInTheDocument()
    })
  })

  describe('AdvancedSettingsButton', () => {
    it('renders 高级设置 and toggles the active variant', async () => {
      const onClick = vi.fn()
      const user = userEvent.setup()
      const view = render(<AdvancedSettingsButton active={false} onClick={onClick} />)

      const button = screen.getByRole('button', { name: '高级设置' })
      expect(button).toHaveClass('border')
      expect(button).not.toHaveClass('bg-primary')

      await user.click(button)
      expect(onClick).toHaveBeenCalledTimes(1)

      view.rerender(<AdvancedSettingsButton active onClick={onClick} />)
      expect(screen.getByRole('button', { name: '高级设置' })).toHaveClass('bg-primary')
    })
  })

  describe('personality section', () => {
    it('renders the prompt generator entry card under the personality nested section', () => {
      const schema: ConfigSchema = {
        className: 'RootConfig',
        classDoc: 'Root',
        fields: [],
        nested: {
          personality: {
            className: 'PersonalityConfig',
            classDoc: '人格',
            fields: [
              makeField('personality', {
                type: 'textarea',
                label: '人格描述',
                'x-widget': 'textarea',
              }),
            ],
          },
        },
      }

      render(
        <DynamicConfigForm
          schema={schema}
          values={{ personality: { personality: '温柔' } }}
          onChange={vi.fn()}
        />,
      )

      expect(screen.getByText('人设生成器（测试版）')).toBeInTheDocument()
      expect(screen.getByText('根据人格设定生成或调整麦麦的人设描述。')).toBeInTheDocument()
      expect(screen.getByRole('link', { name: /人设生成器/ })).toHaveAttribute(
        'href',
        '/config/prompt-generator',
      )
    })
  })

  describe('advanced visibility and hidden hooks', () => {
    it('hides advanced inline fields until advancedVisible is true', () => {
      const schema: ConfigSchema = {
        className: 'TestConfig',
        classDoc: 'Test',
        fields: [
          makeField('visible_field', { label: '普通字段' }),
          makeField('secret_field', { label: '高级字段', advanced: true }),
        ],
      }
      const values = { visible_field: 'a', secret_field: 'b' }
      const view = render(
        <DynamicConfigForm schema={schema} values={values} onChange={vi.fn()} />,
      )

      expect(screen.getByText('普通字段')).toBeInTheDocument()
      expect(screen.queryByText('高级字段')).not.toBeInTheDocument()

      view.rerender(
        <DynamicConfigForm
          schema={schema}
          values={values}
          onChange={vi.fn()}
          advancedVisible
        />,
      )
      expect(screen.getByText('高级字段')).toBeInTheDocument()
    })

    it('hides empty nested schemas and nested trees that only contain advanced fields', () => {
      const schema: ConfigSchema = {
        className: 'RootConfig',
        classDoc: 'Root',
        fields: [
          makeField('advanced_box', {
            type: 'object',
            label: '父级高级字段',
            advanced: true,
          }),
        ],
        nested: {
          // 无 hook 时父字段 advanced 不会挡住分组；是否展示取决于子 schema 是否有可见内容
          advanced_box: {
            className: 'AdvancedBox',
            classDoc: '可见子分组',
            fields: [makeField('inner', { label: '分组内字段' })],
          },
          empty_box: {
            className: 'EmptyBox',
            classDoc: '空分组',
            fields: [],
          },
          deep_advanced: {
            className: 'DeepAdvanced',
            classDoc: '深层高级',
            fields: [makeField('only_advanced', { label: '仅高级子字段', advanced: true })],
          },
        },
      }

      const view = render(
        <DynamicConfigForm schema={schema} values={{}} onChange={vi.fn()} />,
      )

      expect(screen.getByText('可见子分组')).toBeInTheDocument()
      expect(screen.getByText('分组内字段')).toBeInTheDocument()
      expect(screen.queryByText('空分组')).not.toBeInTheDocument()
      expect(screen.queryByText('深层高级')).not.toBeInTheDocument()

      view.rerender(
        <DynamicConfigForm schema={schema} values={{}} onChange={vi.fn()} advancedVisible />,
      )
      expect(screen.getByText('深层高级')).toBeInTheDocument()
      expect(screen.getByText('仅高级子字段')).toBeInTheDocument()
      expect(screen.queryByText('空分组')).not.toBeInTheDocument()
    })

    it('hides hook-backed nested sections when the parent field is advanced', () => {
      const ProbeHook: React.FC<FieldHookComponentProps> = () => <div>高级 hook 内容</div>
      const hooks = new FieldHookRegistry()
      hooks.register('advanced_hook', ProbeHook, 'replace')

      const schema: ConfigSchema = {
        className: 'RootConfig',
        classDoc: 'Root',
        fields: [
          makeField('advanced_hook', {
            type: 'object',
            label: '高级 hook 分组',
            advanced: true,
          }),
        ],
        nested: {
          advanced_hook: {
            className: 'AdvancedHook',
            classDoc: '高级 hook 分组',
            fields: [],
          },
        },
      }

      const view = render(
        <DynamicConfigForm schema={schema} values={{}} onChange={vi.fn()} hooks={hooks} />,
      )
      expect(screen.queryByText('高级 hook 内容')).not.toBeInTheDocument()

      view.rerender(
        <DynamicConfigForm
          schema={schema}
          values={{}}
          onChange={vi.fn()}
          hooks={hooks}
          advancedVisible
        />,
      )
      expect(screen.getByText('高级 hook 内容')).toBeInTheDocument()
    })

    it('does not render hidden hook fields or nested sections', () => {
      const HiddenHook: React.FC<FieldHookComponentProps> = () => {
        throw new Error('hidden hook should not render')
      }
      const hooks = new FieldHookRegistry()
      hooks.register('secret_field', HiddenHook, 'hidden')
      hooks.register('secret_section', HiddenHook, 'hidden')

      const schema: ConfigSchema = {
        className: 'TestConfig',
        classDoc: 'Test',
        fields: [
          makeField('secret_field', { label: '不该出现的字段' }),
          makeField('kept_field', { label: '保留字段' }),
        ],
        nested: {
          secret_section: {
            className: 'SecretSection',
            classDoc: '不该出现的分组',
            fields: [makeField('inner', { label: '隐藏分组字段' })],
          },
        },
      }

      render(
        <DynamicConfigForm
          schema={schema}
          values={{ secret_field: 'x', kept_field: 'y' }}
          onChange={vi.fn()}
          hooks={hooks}
        />,
      )

      expect(screen.getByText('保留字段')).toBeInTheDocument()
      expect(screen.queryByText('不该出现的字段')).not.toBeInTheDocument()
      expect(screen.queryByText('不该出现的分组')).not.toBeInTheDocument()
    })

    it('passes advancedVisible through replace hooks', () => {
      const ProbeHook: React.FC<FieldHookComponentProps> = ({ advancedVisible }) => (
        <div>hook-advanced:{String(advancedVisible)}</div>
      )
      const hooks = new FieldHookRegistry()
      hooks.register('probed', ProbeHook, 'replace')

      const schema: ConfigSchema = {
        className: 'TestConfig',
        classDoc: 'Test',
        fields: [makeField('probed', { label: '探针' })],
      }

      const view = render(
        <DynamicConfigForm schema={schema} values={{ probed: 1 }} onChange={vi.fn()} hooks={hooks} />,
      )
      expect(screen.getByText('hook-advanced:false')).toBeInTheDocument()

      view.rerender(
        <DynamicConfigForm
          schema={schema}
          values={{ probed: 1 }}
          onChange={vi.fn()}
          hooks={hooks}
          advancedVisible
        />,
      )
      expect(screen.getByText('hook-advanced:true')).toBeInTheDocument()
    })
  })

  describe('chat.reply_timing talk-rule split', () => {
    const replyTimingSchema: ConfigSchema = {
      className: 'ReplyTiming',
      classDoc: '回复时机',
      fields: [
        makeField('timeout', { type: 'integer', label: '超时' }),
        makeField('enable_talk_value_rules', { type: 'boolean', label: '启用规则' }),
        makeField('interval', { type: 'integer', label: '间隔' }),
        makeField('talk_value_rules', {
          type: 'array',
          label: '规则列表',
          items: { type: 'object' },
        }),
      ],
    }

    it('renders common fields above talk-rule fields when both groups exist', () => {
      render(
        <DynamicConfigForm
          schema={replyTimingSchema}
          values={{
            timeout: 1,
            enable_talk_value_rules: true,
            interval: 2,
            talk_value_rules: [],
          }}
          onChange={vi.fn()}
          basePath="chat.reply_timing"
        />,
      )

      const text = document.body.textContent ?? ''
      expect(text.indexOf('超时')).toBeLessThan(text.indexOf('间隔'))
      expect(text.indexOf('间隔')).toBeLessThan(text.indexOf('启用规则'))
      expect(text.indexOf('启用规则')).toBeLessThan(text.indexOf('规则列表'))
      expect(document.querySelector('.my-2')).toBeInTheDocument()
    })

    it('skips the extra talk-rule separator when only one group is visible', () => {
      const talkOnly: ConfigSchema = {
        className: 'ReplyTiming',
        classDoc: '回复时机',
        fields: [
          makeField('enable_talk_value_rules', { type: 'boolean', label: '启用规则' }),
          makeField('talk_value_rules', {
            type: 'array',
            label: '规则列表',
            items: { type: 'object' },
          }),
        ],
      }

      const view = render(
        <DynamicConfigForm
          schema={talkOnly}
          values={{ enable_talk_value_rules: true, talk_value_rules: [] }}
          onChange={vi.fn()}
          basePath="chat.reply_timing"
        />,
      )
      expect(screen.getByText('启用规则')).toBeInTheDocument()
      expect(screen.getByText('规则列表')).toBeInTheDocument()
      expect(document.querySelector('.my-2')).not.toBeInTheDocument()

      const commonOnly: ConfigSchema = {
        className: 'ReplyTiming',
        classDoc: '回复时机',
        fields: [makeField('timeout', { type: 'integer', label: '超时' })],
      }
      view.rerender(
        <DynamicConfigForm
          schema={commonOnly}
          values={{ timeout: 1 }}
          onChange={vi.fn()}
          basePath="chat.reply_timing"
        />,
      )
      expect(screen.getByText('超时')).toBeInTheDocument()
      expect(screen.queryByText('启用规则')).not.toBeInTheDocument()
    })
  })

  describe('field ordering and rows', () => {
    it('puts nickname and platform first for bot schemas', () => {
      const schema: ConfigSchema = {
        className: 'bot',
        classDoc: 'Bot',
        fields: [
          makeField('zzz', { label: '其它甲' }),
          makeField('platform', { label: '平台' }),
          makeField('nickname', { label: '昵称' }),
          makeField('aaa', { label: '其它乙' }),
        ],
      }

      render(
        <DynamicConfigForm
          schema={schema}
          values={{ zzz: '', platform: 'qq', nickname: '麦麦', aaa: '' }}
          onChange={vi.fn()}
        />,
      )

      const fieldNames = [...document.querySelectorAll('[data-dynamic-field]')].map((node) =>
        node.getAttribute('data-dynamic-field'),
      )
      expect(fieldNames).toEqual(['nickname', 'platform', 'zzz', 'aaa'])
    })

    it('groups x-row fields and uses the visual-image-compression grid', () => {
      const schema: ConfigSchema = {
        className: 'Visual',
        classDoc: '视觉',
        fields: [
          makeField('alone', { label: '单独字段' }),
          makeField('left', { label: '左列', 'x-row': 'pair' }),
          makeField('right', { label: '右列', 'x-row': 'pair' }),
          makeField('quality', { type: 'integer', label: '质量', 'x-row': 'visual-image-compression' }),
          makeField('format', { label: '格式', 'x-row': 'visual-image-compression' }),
          makeField('max_size', { type: 'integer', label: '上限', 'x-row': 'visual-image-compression' }),
        ],
      }

      render(
        <DynamicConfigForm
          schema={schema}
          values={{ alone: '', left: '', right: '', quality: 80, format: 'webp', max_size: 1 }}
          onChange={vi.fn()}
        />,
      )

      const pairRow = document.querySelector('[data-config-row="pair"]')
      expect(pairRow).toBeInTheDocument()
      expect(pairRow).toHaveTextContent('左列')
      expect(pairRow).toHaveTextContent('右列')
      expect(pairRow?.className).toContain('md:grid-cols-2')

      const compressionRow = document.querySelector('[data-config-row="visual-image-compression"]')
      expect(compressionRow).toBeInTheDocument()
      expect(compressionRow?.className).toContain(
        'grid-cols-[minmax(0,0.8fr)_minmax(0,1fr)_minmax(0,1.1fr)]',
      )
    })
  })

  describe('nested sections and display-as-section', () => {
    it('uses uiLabel for nested section titles and className when docs are empty', () => {
      const schema: ConfigSchema = {
        className: 'RootConfig',
        classDoc: 'Root',
        fields: [],
        nested: {
          labeled: {
            className: 'LabeledConfig',
            classDoc: '文档标题',
            uiLabel: '界面标题',
            fields: [makeField('inner', { label: '带标签字段' })],
          },
          unnamed: {
            className: 'FallbackName',
            classDoc: '',
            fields: [makeField('inner', { label: '回退字段' })],
          },
        },
      }

      render(<DynamicConfigForm schema={schema} values={{}} onChange={vi.fn()} />)

      expect(screen.getByText('界面标题')).toBeInTheDocument()
      expect(screen.queryByText('文档标题')).not.toBeInTheDocument()
      expect(screen.getByText('FallbackName')).toBeInTheDocument()
    })

    it('renders grandchild sections with the nested card style when level > 0', () => {
      const schema: ConfigSchema = {
        className: 'RootConfig',
        classDoc: 'Root',
        fields: [],
        nested: {
          parent: {
            className: 'ParentConfig',
            classDoc: '父分组',
            fields: [makeField('parent_field', { label: '父字段' })],
            nested: {
              child: {
                className: 'ChildConfig',
                classDoc: '子分组',
                fields: [makeField('child_field', { label: '子字段' })],
              },
            },
          },
        },
      }

      render(
        <DynamicConfigForm
          schema={schema}
          values={{ parent: { parent_field: '', child: { child_field: '' } } }}
          onChange={vi.fn()}
        />,
      )

      expect(screen.getByText('父分组')).toBeInTheDocument()
      expect(screen.getByText('子分组')).toBeInTheDocument()
      expect(document.querySelector('[data-config-field-path="parent.child"]')).toHaveClass(
        'bg-muted/20',
      )
    })

    it('lays out multiple top-level sections into two columns', () => {
      const schema: ConfigSchema = {
        className: 'RootConfig',
        classDoc: 'Root',
        fields: [],
        nested: {
          left: {
            className: 'LeftConfig',
            classDoc: '左栏',
            fields: [makeField('l', { label: '左字段' })],
          },
          right: {
            className: 'RightConfig',
            classDoc: '右栏',
            fields: [makeField('r', { label: '右字段' })],
          },
        },
      }

      render(
        <DynamicConfigForm
          schema={schema}
          values={{}}
          onChange={vi.fn()}
          sectionColumns={2}
        />,
      )

      expect(document.querySelector('.grid.min-w-0.gap-3.md\\:grid-cols-2')).toBeInTheDocument()
      expect(screen.getByText('左栏')).toBeInTheDocument()
      expect(screen.getByText('右栏')).toBeInTheDocument()
    })

    it('wraps replace hooks marked x-display-as-section and omits fields without nested schema', () => {
      const SectionHook: React.FC<FieldHookComponentProps> = ({ fieldPath }) => (
        <div>section-hook:{fieldPath}</div>
      )
      const hooks = new FieldHookRegistry()
      hooks.register('rules', SectionHook, 'replace')

      const schema: ConfigSchema = {
        className: 'RootConfig',
        classDoc: 'Root',
        fields: [
          makeField('rules', {
            type: 'array',
            label: '规则区块',
            'x-display-as-section': true,
            'x-collapsed-by-default': true,
            items: { type: 'object' },
          }),
          makeField('orphan', {
            type: 'object',
            label: '孤儿区块',
            'x-display-as-section': true,
          }),
        ],
        nested: {
          rules: {
            className: 'RulesConfig',
            classDoc: '规则配置',
            fields: [],
          },
        },
      }

      render(
        <DynamicConfigForm
          schema={schema}
          values={{ rules: [], orphan: {} }}
          onChange={vi.fn()}
          hooks={hooks}
        />,
      )

      expect(screen.getByText('规则区块')).toBeInTheDocument()
      expect(screen.getByText('section-hook:rules')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: '展开' })).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: '收起' })).not.toBeInTheDocument()
      expect(screen.queryByText('孤儿区块')).not.toBeInTheDocument()
    })

    it('lets nested replace hooks render without a section card and wrappers prefix onChange', async () => {
      const ReplaceHook: React.FC<FieldHookComponentProps> = ({ fieldPath }) => (
        <div>replaced:{fieldPath}</div>
      )
      const WrapperHook: React.FC<FieldHookComponentProps> = ({ fieldPath, children }) => (
        <div data-testid="nested-wrapper">
          <div>wrapped:{fieldPath}</div>
          {children}
        </div>
      )
      const hooks = new FieldHookRegistry()
      hooks.register('replaced', ReplaceHook, 'replace')
      hooks.register('wrapped', WrapperHook, 'wrapper')

      const schema: ConfigSchema = {
        className: 'RootConfig',
        classDoc: 'Root',
        fields: [],
        nested: {
          replaced: {
            className: 'ReplacedConfig',
            classDoc: '替换分组',
            fields: [],
          },
          wrapped: {
            className: 'WrappedConfig',
            classDoc: '包装分组',
            fields: [makeField('inner', { label: '包装内字段' })],
          },
          empty_wrapped: {
            className: 'EmptyWrapped',
            classDoc: '空包装',
            fields: [],
          },
        },
      }
      const onChange = vi.fn()
      const user = userEvent.setup()

      render(
        <DynamicConfigForm
          schema={schema}
          values={{ wrapped: { inner: '' } }}
          onChange={onChange}
          hooks={hooks}
        />,
      )

      expect(screen.getByText('replaced:replaced')).toBeInTheDocument()
      expect(screen.queryByText('替换分组')).not.toBeInTheDocument()
      expect(screen.getByTestId('nested-wrapper')).toBeInTheDocument()
      expect(screen.getByText('wrapped:wrapped')).toBeInTheDocument()
      expect(screen.getByText('包装内字段')).toBeInTheDocument()
      expect(screen.queryByText('空包装')).not.toBeInTheDocument()

      await user.type(screen.getByRole('textbox'), 'Hi')
      expect(onChange).toHaveBeenNthCalledWith(1, 'wrapped.inner', 'H')
      expect(onChange).toHaveBeenNthCalledWith(2, 'wrapped.inner', 'i')
    })

    it('uses NestedDynamicConfigSection when the form itself is already nested', () => {
      const schema: ConfigSchema = {
        className: 'InnerConfig',
        classDoc: 'Inner',
        fields: [],
        nested: {
          child: {
            className: 'ChildConfig',
            classDoc: '直接子分组',
            fields: [makeField('leaf', { label: '叶子字段' })],
          },
        },
      }

      render(
        <DynamicConfigForm
          schema={schema}
          values={{ child: { leaf: '' } }}
          onChange={vi.fn()}
          level={1}
          basePath="parent"
        />,
      )

      expect(screen.getByText('直接子分组')).toBeInTheDocument()
      expect(document.querySelector('[data-config-field-path="parent.child"]')).toHaveClass(
        'bg-muted/20',
      )
      expect(document.querySelector('.md\\:grid-cols-2')).not.toBeInTheDocument()
    })
  })
})
