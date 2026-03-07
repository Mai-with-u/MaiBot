import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/dom'
import { render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { DynamicConfigForm } from '../DynamicConfigForm'
import { FieldHookRegistry } from '@/lib/field-hooks'
import type { ConfigSchema } from '@/types/config-schema'
import type { FieldHookComponentProps } from '@/lib/field-hooks'

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
      expect(screen.getByText('First field')).toBeInTheDocument()
      expect(screen.getByText('Second field')).toBeInTheDocument()
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
      expect(screen.getByText('SubConfig')).toBeInTheDocument()
      expect(screen.getByText('Sub configuration')).toBeInTheDocument()
      expect(screen.getByText('Nested Field')).toBeInTheDocument()
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

      expect(screen.getByText('SubConfig')).toBeInTheDocument()
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
})
