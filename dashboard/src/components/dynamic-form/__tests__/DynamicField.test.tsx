import { beforeAll, describe, it, expect, vi } from 'vitest'
import { fireEvent, screen } from '@testing-library/dom'
import { render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { DynamicField } from '../DynamicField'
import type { FieldSchema, FieldType, XWidgetType } from '@/types/config-schema'

function makeField(overrides: Partial<FieldSchema> & Pick<FieldSchema, 'name' | 'type'>): FieldSchema {
  return {
    label: overrides.label ?? overrides.name,
    description: overrides.description ?? '',
    required: false,
    ...overrides,
  }
}

describe('DynamicField', () => {
  describe('x-widget priority', () => {
    it('renders Slider when x-widget is slider', () => {
      const schema: FieldSchema = {
        name: 'test_slider',
        type: 'number',
        label: 'Test Slider',
        description: 'A test slider',
        required: false,
        'x-widget': 'slider',
        minValue: 0,
        maxValue: 100,
        default: 50,
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value={50} onChange={onChange} />)

      expect(screen.getByText('Test Slider')).toBeInTheDocument()
      expect(screen.getByRole('slider')).toBeInTheDocument()
      expect(screen.getByText('50')).toBeInTheDocument()
    })

    it('renders Switch when x-widget is switch', () => {
      const schema: FieldSchema = {
        name: 'test_switch',
        type: 'boolean',
        label: 'Test Switch',
        description: 'A test switch',
        required: false,
        'x-widget': 'switch',
        default: false,
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value={false} onChange={onChange} />)

      expect(screen.getByText('Test Switch')).toBeInTheDocument()
      expect(screen.getByRole('switch')).toBeInTheDocument()
    })

    it('renders Textarea when x-widget is textarea', () => {
      const schema: FieldSchema = {
        name: 'test_textarea',
        type: 'string',
        label: 'Test Textarea',
        description: 'A test textarea',
        required: false,
        'x-widget': 'textarea',
        default: 'Hello',
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value="Hello" onChange={onChange} />)

      expect(screen.getByText('Test Textarea')).toBeInTheDocument()
      expect(screen.getByRole('textbox')).toBeInTheDocument()
      expect(screen.getByRole('textbox')).toHaveValue('Hello')
    })

    it('renders Select when x-widget is select', () => {
      const schema: FieldSchema = {
        name: 'test_select',
        type: 'string',
        label: 'Test Select',
        description: 'A test select',
        required: false,
        'x-widget': 'select',
        options: ['Option 1', 'Option 2', 'Option 3'],
        default: 'Option 1',
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value="Option 1" onChange={onChange} />)

      expect(screen.getByText('Test Select')).toBeInTheDocument()
      expect(screen.getByRole('combobox')).toBeInTheDocument()
    })

    it('renders placeholder for custom widget', () => {
      const schema: FieldSchema = {
        name: 'test_custom',
        type: 'string',
        label: 'Test Custom',
        description: 'A test custom field',
        required: false,
        'x-widget': 'custom',
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value="" onChange={onChange} />)

      expect(screen.getByText('Custom field requires Hook')).toBeInTheDocument()
    })

    it('renders number Input when x-widget is input but type is integer', () => {
      const schema: FieldSchema = {
        name: 'test_integer_input_widget',
        type: 'integer',
        label: 'Test Integer Input Widget',
        description: 'A numeric field rendered as input',
        required: false,
        'x-widget': 'input',
        default: 0,
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value={2} onChange={onChange} />)

      const input = screen.getByRole('spinbutton')
      expect(input).toBeInTheDocument()
      expect(input).toHaveValue(2)
    })

    it('parses string values for numeric input widgets', () => {
      const schema: FieldSchema = {
        name: 'test_string_number_input_widget',
        type: 'integer',
        label: 'Test String Number Input Widget',
        description: 'A numeric field with legacy string value',
        required: false,
        'x-widget': 'input',
        default: 0,
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value="2" onChange={onChange} />)

      expect(screen.getByRole('spinbutton')).toHaveValue(2)
    })
  })

  describe('type fallback', () => {
    it('renders Input for string type', () => {
      const schema: FieldSchema = {
        name: 'test_string',
        type: 'string',
        label: 'Test String',
        description: 'A test string',
        required: false,
        default: 'Hello',
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value="Hello" onChange={onChange} />)

      expect(screen.getByRole('textbox')).toBeInTheDocument()
      expect(screen.getByRole('textbox')).toHaveValue('Hello')
    })

    it('renders Switch for boolean type', () => {
      const schema: FieldSchema = {
        name: 'test_bool',
        type: 'boolean',
        label: 'Test Boolean',
        description: 'A test boolean',
        required: false,
        default: true,
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value={true} onChange={onChange} />)

      expect(screen.getByRole('switch')).toBeInTheDocument()
      expect(screen.getByRole('switch')).toBeChecked()
    })

    it('renders number Input for number type', () => {
      const schema: FieldSchema = {
        name: 'test_number',
        type: 'number',
        label: 'Test Number',
        description: 'A test number',
        required: false,
        default: 42,
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value={42} onChange={onChange} />)

      const input = screen.getByRole('spinbutton')
      expect(input).toBeInTheDocument()
      expect(input).toHaveValue(42)
    })

    it('renders number Input for integer type', () => {
      const schema: FieldSchema = {
        name: 'test_integer',
        type: 'integer',
        label: 'Test Integer',
        description: 'A test integer',
        required: false,
        default: 10,
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value={10} onChange={onChange} />)

      const input = screen.getByRole('spinbutton')
      expect(input).toBeInTheDocument()
      expect(input).toHaveValue(10)
    })

    it('renders Textarea for textarea type', () => {
      const schema: FieldSchema = {
        name: 'test_textarea_type',
        type: 'textarea',
        label: 'Test Textarea Type',
        description: 'A test textarea type',
        required: false,
        default: 'Long text',
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value="Long text" onChange={onChange} />)

      expect(screen.getByRole('textbox')).toBeInTheDocument()
      expect(screen.getByRole('textbox')).toHaveValue('Long text')
    })

    it('renders Select for select type', () => {
      const schema: FieldSchema = {
        name: 'test_select_type',
        type: 'select',
        label: 'Test Select Type',
        description: 'A test select type',
        required: false,
        options: ['A', 'B', 'C'],
        default: 'A',
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value="A" onChange={onChange} />)

      expect(screen.getByRole('combobox')).toBeInTheDocument()
    })

    it('renders textarea editor for primitive array type', () => {
      const schema: FieldSchema = {
        name: 'test_array',
        type: 'array',
        label: 'Test Array',
        description: 'A test array',
        required: false,
        items: {
          type: 'string',
        },
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value={['a', 'b']} onChange={onChange} />)

      expect(screen.getByRole('textbox')).toHaveValue('a\nb')
    })

    it('uses schema placeholder for string tag arrays', () => {
      const schema: FieldSchema = {
        name: 'test_tags',
        type: 'array',
        label: 'Test Tags',
        description: 'A test tag array',
        required: false,
        items: {
          type: 'string',
        },
        'x-widget': 'tags',
        'x-placeholder': '127.0.0.1',
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value={['::1']} onChange={onChange} />)

      expect(screen.getByPlaceholderText('127.0.0.1')).toBeInTheDocument()
      expect(screen.queryByPlaceholderText('qq:123456789')).not.toBeInTheDocument()
    })

    it('edits comma-separated string fields as list items', async () => {
      const schema: FieldSchema = {
        name: 'allowed_ips',
        type: 'string',
        label: 'Allowed IPs',
        description: 'A comma-separated string field',
        required: false,
        default: '',
        'x-widget': 'comma-list',
        'x-placeholder': '127.0.0.1',
      }
      let controlledValue: unknown = '127.0.0.1'
      const onChange = vi.fn((nextValue: unknown) => {
        controlledValue = nextValue
        view.rerender(<DynamicField schema={schema} value={controlledValue} onChange={onChange} />)
      })
      const user = userEvent.setup()

      const view = render(<DynamicField schema={schema} value={controlledValue} onChange={onChange} />)
      await user.type(screen.getByPlaceholderText('127.0.0.1'), '192.168.1.1{Enter}')

      expect(onChange).toHaveBeenLastCalledWith('127.0.0.1,192.168.1.1')
      expect(screen.getByText('192.168.1.1')).toBeInTheDocument()
    })

    it('keeps draft newlines while editing primitive arrays', async () => {
      const schema: FieldSchema = {
        name: 'test_array_draft',
        type: 'array',
        label: 'Test Array Draft',
        description: 'A test array with draft editing',
        required: false,
        items: {
          type: 'string',
        },
      }
      let controlledValue: unknown = ['a']
      const onChange = vi.fn((nextValue: unknown) => {
        controlledValue = nextValue
        view.rerender(<DynamicField schema={schema} value={controlledValue} onChange={onChange} />)
      })
      const user = userEvent.setup()

      const view = render(<DynamicField schema={schema} value={controlledValue} onChange={onChange} />)
      const textbox = screen.getByRole('textbox')
      await user.click(textbox)
      await user.keyboard('{End}{Enter}')

      expect(onChange).toHaveBeenLastCalledWith(['a'])
      expect(screen.getByRole('textbox')).toHaveValue('a\n')

      await user.keyboard('b')
      expect(onChange).toHaveBeenLastCalledWith(['a', 'b'])
      expect(screen.getByRole('textbox')).toHaveValue('a\nb')
    })

    it('renders key-value editor for object type', () => {
      const schema: FieldSchema = {
        name: 'test_object',
        type: 'object',
        label: 'Test Object',
        description: 'A test object',
        required: false,
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value={{ foo: 'bar' }} onChange={onChange} />)

      expect(screen.getByText('可视化编辑')).toBeInTheDocument()
      expect(screen.getByDisplayValue('foo')).toBeInTheDocument()
    })
  })

  describe('onChange events', () => {
    it('triggers onChange for Switch', async () => {
      const schema: FieldSchema = {
        name: 'test_switch',
        type: 'boolean',
        label: 'Test Switch',
        description: 'A test switch',
        required: false,
        default: false,
      }
      const onChange = vi.fn()
      const user = userEvent.setup()

      render(<DynamicField schema={schema} value={false} onChange={onChange} />)

      await user.click(screen.getByRole('switch'))
      expect(onChange).toHaveBeenCalledWith(true)
    })

    it('triggers onChange for Input', async () => {
      const schema: FieldSchema = {
        name: 'test_input',
        type: 'string',
        label: 'Test Input',
        description: 'A test input',
        required: false,
        default: '',
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value="" onChange={onChange} />)

      const input = screen.getByRole('textbox')
      input.focus()
      await userEvent.keyboard('Hello')
      
      expect(onChange).toHaveBeenCalledTimes(5)
      expect(onChange).toHaveBeenNthCalledWith(1, 'H')
      expect(onChange).toHaveBeenNthCalledWith(2, 'e')
      expect(onChange).toHaveBeenNthCalledWith(3, 'l')
      expect(onChange).toHaveBeenNthCalledWith(4, 'l')
      expect(onChange).toHaveBeenNthCalledWith(5, 'o')
    })

    it('triggers onChange for number Input', async () => {
      const schema: FieldSchema = {
        name: 'test_number',
        type: 'number',
        label: 'Test Number',
        description: 'A test number',
        required: false,
        default: 0,
      }
      const onChange = vi.fn()
      const user = userEvent.setup()

      render(<DynamicField schema={schema} value={0} onChange={onChange} />)

      const input = screen.getByRole('spinbutton')
      await user.clear(input)
      await user.type(input, '123')
      expect(onChange).toHaveBeenCalled()
    })

    it('keeps numeric input empty while replacing a value', async () => {
      const schema: FieldSchema = {
        name: 'test_number_replace',
        type: 'integer',
        label: 'Test Number Replace',
        description: 'A numeric field being edited',
        required: false,
        default: 0,
      }
      let controlledValue: unknown = 7
      const onChange = vi.fn((nextValue: unknown) => {
        controlledValue = nextValue
        view.rerender(<DynamicField schema={schema} value={controlledValue} onChange={onChange} />)
      })
      const user = userEvent.setup()

      const view = render(<DynamicField schema={schema} value={controlledValue} onChange={onChange} />)
      const input = screen.getByRole('spinbutton') as HTMLInputElement

      await user.clear(input)
      expect(input.value).toBe('')
      expect(onChange).not.toHaveBeenCalled()

      await user.type(input, '8')
      expect(onChange).toHaveBeenLastCalledWith(8)
      expect(input.value).toBe('8')
    })

    it('triggers numeric onChange for input widget with integer type', async () => {
      const schema: FieldSchema = {
        name: 'test_integer_input_widget_change',
        type: 'integer',
        label: 'Test Integer Input Widget Change',
        description: 'A numeric field rendered as input',
        required: false,
        'x-widget': 'input',
        default: 0,
      }
      const onChange = vi.fn()
      const user = userEvent.setup()

      render(<DynamicField schema={schema} value={0} onChange={onChange} />)

      const input = screen.getByRole('spinbutton')
      await user.clear(input)
      await user.type(input, '5')
      expect(onChange).toHaveBeenLastCalledWith(5)
    })
  })

  describe('visual features', () => {
    it('uses the advanced title color for advanced fields', () => {
      const schema: FieldSchema = {
        name: 'advanced_field',
        type: 'string',
        label: 'Advanced Field',
        description: 'An advanced field',
        required: false,
        advanced: true,
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value="" onChange={onChange} />)

      expect(screen.getByText('Advanced Field').closest('label')).toHaveClass('text-sky-700')
    })

    it('ignores legacy x-icon metadata', () => {
      const schema = {
        name: 'legacy_icon',
        type: 'string',
        label: 'Legacy Icon',
        description: 'A field carrying legacy icon metadata',
        required: false,
        'x-icon': 'Settings',
      } as FieldSchema

      render(<DynamicField schema={schema} value="" onChange={vi.fn()} />)

      const label = screen.getByText('Legacy Icon').closest('label')
      expect(label).toBeInTheDocument()
      expect(label?.querySelector('svg')).not.toBeInTheDocument()
    })

    it('renders required indicator', () => {
      const schema: FieldSchema = {
        name: 'test_required',
        type: 'string',
        label: 'Test Required',
        description: 'A required field',
        required: true,
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value="" onChange={onChange} />)

      expect(screen.getByText('*')).toBeInTheDocument()
    })

    it('renders description in the default label tooltip', async () => {
      const schema: FieldSchema = {
        name: 'test_desc',
        type: 'string',
        label: 'Test Description',
        description: 'This is a description',
        required: false,
      }
      const onChange = vi.fn()
      const user = userEvent.setup()

      render(<DynamicField schema={schema} value="" onChange={onChange} />)

      await user.hover(screen.getByText('Test Description'))
      expect(await screen.findByRole('tooltip')).toHaveTextContent('This is a description')
    })
  })

  describe('slider features', () => {
    it('renders slider with min/max/step', () => {
      const schema: FieldSchema = {
        name: 'test_slider_props',
        type: 'number',
        label: 'Test Slider Props',
        description: 'A slider with props',
        required: false,
        'x-widget': 'slider',
        minValue: 10,
        maxValue: 50,
        step: 5,
        default: 25,
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value={25} onChange={onChange} />)

      const slider = screen.getByRole('slider')
      expect(slider).toHaveAttribute('aria-valuemin', '10')
      expect(slider).toHaveAttribute('aria-valuemax', '50')
      expect(slider).toHaveAttribute('aria-valuenow', '25')

      const input = screen.getByRole('spinbutton', { name: 'Test Slider Props 数值' })
      expect(input).toHaveAttribute('min', '10')
      expect(input).toHaveAttribute('max', '50')
      expect(input).toHaveAttribute('step', '5')
      expect(input).toHaveValue(25)
    })

    it('parses string values for slider widgets', () => {
      const schema: FieldSchema = {
        name: 'test_slider_string_value',
        type: 'number',
        label: 'Test Slider String Value',
        description: 'A slider with legacy string value',
        required: false,
        'x-widget': 'slider',
        minValue: 0,
        maxValue: 10,
        default: 0,
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value="2.5" onChange={onChange} />)

      expect(screen.getByText('2.5')).toBeInTheDocument()
    })

    it('allows manual numeric input for slider widgets', async () => {
      const schema: FieldSchema = {
        name: 'test_slider_manual_value',
        type: 'number',
        label: 'Test Slider Manual Value',
        description: 'A slider with manual input',
        required: false,
        'x-widget': 'slider',
        minValue: 0,
        maxValue: 1,
        step: 0.001,
        default: 0,
      }
      let controlledValue: unknown = 0.5
      const onChange = vi.fn((nextValue: unknown) => {
        controlledValue = nextValue
        view.rerender(<DynamicField schema={schema} value={controlledValue} onChange={onChange} />)
      })
      const user = userEvent.setup()

      const view = render(<DynamicField schema={schema} value={controlledValue} onChange={onChange} />)
      const input = screen.getByRole('spinbutton', { name: 'Test Slider Manual Value 数值' })

      await user.clear(input)
      await user.type(input, '0.123')

      expect(onChange).toHaveBeenLastCalledWith(0.123)
      expect(input).toHaveValue(0.123)
    })
  })

  describe('select features', () => {
    beforeAll(() => {
      Object.defineProperty(HTMLElement.prototype, 'hasPointerCapture', {
        configurable: true,
        value: () => false,
      })
      Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', {
        configurable: true,
        value: () => {},
      })
      Object.defineProperty(HTMLElement.prototype, 'releasePointerCapture', {
        configurable: true,
        value: () => {},
      })
    })

    it('renders placeholder when no options', () => {
      const schema: FieldSchema = {
        name: 'test_select_no_options',
        type: 'string',
        label: 'Test Select No Options',
        description: 'A select with no options',
        required: false,
        'x-widget': 'select',
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value="" onChange={onChange} />)

      expect(screen.getByText('No options available for select')).toBeInTheDocument()
    })

    it('renders option labels and option-description tooltips', async () => {
      const schema = makeField({
        name: 'labeled_select',
        type: 'string',
        label: 'Labeled Select',
        'x-widget': 'select',
        options: ['alpha', 'beta'],
        'x-option-labels': { alpha: '选项甲', beta: '选项乙' },
        'x-option-descriptions': { alpha: '甲的说明' },
        default: 'alpha',
      })
      const onChange = vi.fn()
      const user = userEvent.setup()

      render(<DynamicField schema={schema} value="alpha" onChange={onChange} />)

      expect(screen.getByRole('combobox')).toHaveTextContent('选项甲')
      await user.click(screen.getByRole('combobox'))
      const alphaOption = await screen.findByRole('option', { name: '选项甲' })
      expect(screen.getByRole('option', { name: '选项乙' })).toBeInTheDocument()
      expect(alphaOption).toHaveAttribute('title', '甲的说明')
      await user.hover(alphaOption)
      expect(await screen.findByRole('tooltip')).toHaveTextContent('甲的说明')
    })
  })

  describe('password, talk-time and fallback widgets', () => {
    it('renders a password input', () => {
      render(
        <DynamicField
          schema={makeField({
            name: 'api_key',
            type: 'string',
            label: 'API Key',
            'x-widget': 'password',
          })}
          value="secret"
          onChange={vi.fn()}
        />,
      )

      const input = document.querySelector('input[type="password"]')
      expect(input).toBeInTheDocument()
      expect(input).toHaveValue('secret')
    })

    it('switches talk-time modes and disables the range input outside range mode', async () => {
      const schema = makeField({
        name: 'talk_time',
        type: 'string',
        label: '发言时间',
        'x-widget': 'talk-time',
        default: '',
      })
      let controlledValue: unknown = ''
      const onChange = vi.fn((nextValue: unknown) => {
        controlledValue = nextValue
        view.rerender(<DynamicField schema={schema} value={controlledValue} onChange={onChange} />)
      })
      const user = userEvent.setup()
      const view = render(<DynamicField schema={schema} value={controlledValue} onChange={onChange} />)

      const rangeInput = screen.getByPlaceholderText('HH:MM-HH:MM')
      expect(rangeInput).toBeDisabled()
      expect(rangeInput).toHaveValue('')

      await user.click(screen.getByRole('button', { name: '时间段' }))
      expect(onChange).toHaveBeenCalledWith('00:00-23:59')
      expect(screen.getByPlaceholderText('HH:MM-HH:MM')).not.toBeDisabled()

      fireEvent.change(screen.getByPlaceholderText('HH:MM-HH:MM'), {
        target: { value: '08:00-12:00' },
      })
      expect(onChange).toHaveBeenLastCalledWith('08:00-12:00')

      await user.click(screen.getByRole('button', { name: '时间段' }))
      expect(onChange).toHaveBeenLastCalledWith('08:00-12:00')

      await user.click(screen.getByRole('button', { name: '*' }))
      expect(onChange).toHaveBeenLastCalledWith('*')
      expect(screen.getByPlaceholderText('HH:MM-HH:MM')).toBeDisabled()

      await user.click(screen.getByRole('button', { name: '兜底' }))
      expect(onChange).toHaveBeenLastCalledWith('')
      expect(screen.getByPlaceholderText('HH:MM-HH:MM')).toBeDisabled()
    })

    it('uses the talk-time default when the value is empty', () => {
      render(
        <DynamicField
          schema={makeField({
            name: 'talk_time_default',
            type: 'string',
            label: '默认发言时间',
            'x-widget': 'talk-time',
            default: '*',
          })}
          value={undefined}
          onChange={vi.fn()}
        />,
      )

      expect(screen.getByPlaceholderText('HH:MM-HH:MM')).toBeDisabled()
      expect(screen.getByRole('button', { name: '*' })).toHaveClass('bg-primary')
    })

    it('falls unknown widgets back to type and shows validation placeholders', () => {
      const { rerender } = render(
        <DynamicField
          schema={makeField({
            name: 'mystery_widget',
            type: 'string',
            label: '未知控件',
            'x-widget': 'not-a-widget' as XWidgetType,
          })}
          value="hello"
          onChange={vi.fn()}
        />,
      )
      expect(screen.getByRole('textbox')).toHaveValue('hello')

      rerender(
        <DynamicField
          schema={makeField({
            name: 'mystery_type',
            type: 'mystery' as FieldType,
            label: '未知类型',
          })}
          value=""
          onChange={vi.fn()}
        />,
      )
      expect(screen.getByText('Unknown field type: mystery')).toBeInTheDocument()

      rerender(
        <DynamicField
          schema={makeField({
            name: 'custom_string',
            type: 'string',
            label: '自定义字符串',
            'x-widget': 'custom',
          })}
          value=""
          onChange={vi.fn()}
        />,
      )
      expect(screen.getByText('Custom field requires Hook')).toBeInTheDocument()

      rerender(
        <DynamicField
          schema={makeField({
            name: 'complex_array',
            type: 'array',
            label: '复杂数组',
            items: { type: 'object' },
          })}
          value={[]}
          onChange={vi.fn()}
        />,
      )
      expect(screen.getByText('Complex array requires Hook')).toBeInTheDocument()

      rerender(
        <DynamicField
          schema={makeField({
            name: 'untyped_array',
            type: 'array',
            label: '无 items 数组',
          })}
          value={[]}
          onChange={vi.fn()}
        />,
      )
      expect(screen.getByText('Complex array requires Hook')).toBeInTheDocument()
    })

    it('renders custom primitive arrays and objects, and coerces empty object values', () => {
      const { rerender } = render(
        <DynamicField
          schema={makeField({
            name: 'custom_tags',
            type: 'array',
            label: '自定义数组',
            items: { type: 'string' },
            'x-widget': 'custom',
          })}
          value={['alpha']}
          onChange={vi.fn()}
        />,
      )
      expect(screen.getByRole('textbox')).toHaveValue('alpha')

      rerender(
        <DynamicField
          schema={makeField({
            name: 'custom_object',
            type: 'object',
            label: '自定义对象',
            'x-widget': 'custom',
          })}
          value={{ foo: 'bar' }}
          onChange={vi.fn()}
        />,
      )
      expect(screen.getByDisplayValue('foo')).toBeInTheDocument()

      rerender(
        <DynamicField
          schema={makeField({
            name: 'object_from_array',
            type: 'object',
            label: '空对象回退',
          })}
          value={['not-an-object']}
          onChange={vi.fn()}
        />,
      )
      expect(screen.queryByDisplayValue('not-an-object')).not.toBeInTheDocument()
      expect(screen.getByText('可视化编辑')).toBeInTheDocument()
    })

    it('shows JSON validation messages in the object editor', async () => {
      const onChange = vi.fn()
      const user = userEvent.setup()
      render(
        <DynamicField
          schema={makeField({
            name: 'extra',
            type: 'object',
            label: '额外参数',
          })}
          value={{ foo: 1 }}
          onChange={onChange}
        />,
      )

      await user.click(screen.getByRole('tab', { name: 'JSON 编辑' }))
      const jsonEditor = screen.getByPlaceholderText((content) => content.includes('"key": "value"'))

      fireEvent.change(jsonEditor, { target: { value: '[1, 2]' } })
      expect(screen.getAllByText('必须是一个 JSON 对象 {}').length).toBeGreaterThan(0)

      fireEvent.change(jsonEditor, { target: { value: '{bad' } })
      expect(screen.getAllByText('JSON 格式错误').length).toBeGreaterThan(0)
    })
  })

  describe('token list and primitive array editors', () => {
    it('adds, dedupes and removes tag tokens', async () => {
      const schema = makeField({
        name: 'tags',
        type: 'array',
        label: '标签',
        items: { type: 'string' },
        'x-widget': 'tags',
      })
      let controlledValue: unknown = ['keep']
      const onChange = vi.fn((nextValue: unknown) => {
        controlledValue = nextValue
        view.rerender(<DynamicField schema={schema} value={controlledValue} onChange={onChange} />)
      })
      const user = userEvent.setup()
      const view = render(<DynamicField schema={schema} value={controlledValue} onChange={onChange} />)

      expect(screen.getByPlaceholderText('输入后按回车添加')).toBeInTheDocument()

      await user.click(screen.getByRole('button', { name: '添加标签' }))
      expect(onChange).not.toHaveBeenCalled()

      await user.type(screen.getByPlaceholderText('输入后按回车添加'), 'new,keep;extra')
      await user.click(screen.getByRole('button', { name: '添加标签' }))
      expect(onChange).toHaveBeenLastCalledWith(['keep', 'new', 'extra'])

      await user.click(screen.getByRole('button', { name: '删除keep' }))
      expect(onChange).toHaveBeenLastCalledWith(['new', 'extra'])
    })

    it('removes comma-list tokens and ignores empty drafts', async () => {
      const schema = makeField({
        name: 'allowed_ips',
        type: 'string',
        label: '白名单',
        'x-widget': 'comma-list',
        default: '127.0.0.1',
      })
      let controlledValue: unknown = undefined
      const onChange = vi.fn((nextValue: unknown) => {
        controlledValue = nextValue
        view.rerender(<DynamicField schema={schema} value={controlledValue} onChange={onChange} />)
      })
      const user = userEvent.setup()
      const view = render(<DynamicField schema={schema} value={controlledValue} onChange={onChange} />)

      expect(screen.getByText('127.0.0.1')).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: '添加白名单' }))
      expect(onChange).not.toHaveBeenCalled()

      await user.click(screen.getByRole('button', { name: '删除127.0.0.1' }))
      expect(onChange).toHaveBeenLastCalledWith('')
    })

    it('canonicalizes primitive array drafts on blur and parses item types', async () => {
      const intSchema = makeField({
        name: 'int_array',
        type: 'array',
        label: '整数数组',
        items: { type: 'integer' },
      })
      let controlledValue: unknown = ['  8  ', 'xx']
      const onChange = vi.fn((nextValue: unknown) => {
        controlledValue = nextValue
        view.rerender(<DynamicField schema={intSchema} value={controlledValue} onChange={onChange} />)
      })
      const view = render(
        <DynamicField schema={intSchema} value={controlledValue} onChange={onChange} />,
      )

      const textbox = screen.getByRole('textbox')
      expect(textbox).toHaveValue('  8  \nxx')
      fireEvent.focus(textbox)
      fireEvent.change(textbox, { target: { value: '  8  \n\n  xx  ' } })
      expect(onChange).toHaveBeenLastCalledWith([8, 0])
      fireEvent.blur(textbox)
      expect(screen.getByRole('textbox')).toHaveValue('8\n0')

      const numberSchema = makeField({
        name: 'num_array',
        type: 'array',
        label: '数字数组',
        items: { type: 'number' },
        default: [1.5],
      })
      view.rerender(
        <DynamicField schema={numberSchema} value={undefined} onChange={onChange} />,
      )
      expect(screen.getByRole('textbox')).toHaveValue('1.5')
      fireEvent.change(screen.getByRole('textbox'), { target: { value: '2.5\nnot-a-number' } })
      expect(onChange).toHaveBeenLastCalledWith([2.5, 0])

      const boolSchema = makeField({
        name: 'bool_array',
        type: 'array',
        label: '布尔数组',
        items: { type: 'boolean' },
      })
      view.rerender(<DynamicField schema={boolSchema} value={[]} onChange={onChange} />)
      fireEvent.change(screen.getByRole('textbox'), { target: { value: 'true\nfalse\nyes' } })
      expect(onChange).toHaveBeenLastCalledWith([true, false, false])
    })

    it('uses a textarea for non-string tag arrays and a text input for non-string comma-lists', () => {
      const { rerender } = render(
        <DynamicField
          schema={makeField({
            name: 'int_tags',
            type: 'array',
            label: '整型标签',
            items: { type: 'integer' },
            'x-widget': 'tags',
          })}
          value={[1, 2]}
          onChange={vi.fn()}
        />,
      )
      expect(screen.getByRole('textbox')).toHaveValue('1\n2')
      expect(screen.queryByPlaceholderText('输入后按回车添加')).not.toBeInTheDocument()

      rerender(
        <DynamicField
          schema={makeField({
            name: 'comma_number',
            type: 'number',
            label: '逗号数字',
            'x-widget': 'comma-list',
          })}
          value={3}
          onChange={vi.fn()}
        />,
      )
      expect(screen.getByRole('textbox')).toHaveValue('3')
    })

    it('falls primitive arrays back to an empty list when value and default are not arrays', () => {
      render(
        <DynamicField
          schema={makeField({
            name: 'empty_array',
            type: 'array',
            label: '空数组',
            items: { type: 'string' },
            default: 'not-an-array',
          })}
          value="also-not-an-array"
          onChange={vi.fn()}
        />,
      )

      expect(screen.getByRole('textbox')).toHaveValue('')
    })
  })

  describe('description display, layout and numeric coercion', () => {
    it('renders icon and inline descriptions, and hides inline text when options have descriptions', async () => {
      const user = userEvent.setup()
      const { rerender } = render(
        <DynamicField
          schema={makeField({
            name: 'icon_field',
            type: 'string',
            label: '图标字段',
            description: '图标说明文案',
            'x-description-display': 'icon',
          })}
          value=""
          onChange={vi.fn()}
        />,
      )

      await user.hover(screen.getByRole('button', { name: '图标字段 说明' }))
      expect(await screen.findByRole('tooltip')).toHaveTextContent('图标说明文案')

      rerender(
        <DynamicField
          schema={makeField({
            name: 'inline_field',
            type: 'string',
            label: '行内字段',
            description: '行内说明文案',
            'x-description-display': 'inline',
          })}
          value=""
          onChange={vi.fn()}
        />,
      )
      expect(screen.getByText('行内说明文案')).toBeInTheDocument()

      rerender(
        <DynamicField
          schema={makeField({
            name: 'inline_select',
            type: 'string',
            label: '行内选择',
            description: '不该行内展示',
            'x-widget': 'select',
            options: ['a'],
            'x-description-display': 'inline',
            'x-option-descriptions': { a: '选项说明' },
          })}
          value="a"
          onChange={vi.fn()}
        />,
      )
      expect(screen.queryByText('不该行内展示')).not.toBeInTheDocument()
    })

    it('applies inline-right widths and textarea rows', () => {
      const { rerender } = render(
        <DynamicField
          schema={makeField({
            name: 'inline_text',
            type: 'string',
            label: '右对齐文本',
            'x-layout': 'inline-right',
          })}
          value=""
          onChange={vi.fn()}
        />,
      )
      expect(screen.getByText('右对齐文本').closest('[data-dynamic-field]')).toHaveStyle({
        '--field-input-width': '12rem',
      })

      rerender(
        <DynamicField
          schema={makeField({
            name: 'inline_number',
            type: 'integer',
            label: '右对齐数字',
            'x-layout': 'inline-right',
            'x-input-width': '12rem',
          })}
          value={1}
          onChange={vi.fn()}
        />,
      )
      expect(screen.getByText('右对齐数字').closest('[data-dynamic-field]')).toHaveStyle({
        '--field-input-width': '7.5rem',
      })

      rerender(
        <DynamicField
          schema={makeField({
            name: 'inline_custom_width',
            type: 'integer',
            label: '自定义宽度',
            'x-layout': 'inline-right',
            'x-input-width': '10rem',
          })}
          value={1}
          onChange={vi.fn()}
        />,
      )
      expect(screen.getByText('自定义宽度').closest('[data-dynamic-field]')).toHaveStyle({
        '--field-input-width': '10rem',
      })

      rerender(
        <DynamicField
          schema={makeField({
            name: 'tall_textarea',
            type: 'string',
            label: '多行文本',
            'x-widget': 'textarea',
            'x-textarea-rows': 8,
            'x-textarea-min-height': 120,
          })}
          value="line"
          onChange={vi.fn()}
        />,
      )
      expect(screen.getByRole('textbox')).toHaveAttribute('rows', '8')
    })

    it('coerces non-string text values and uses numeric defaults', () => {
      const { rerender } = render(
        <DynamicField
          schema={makeField({
            name: 'from_null',
            type: 'string',
            label: '空值文本',
            default: 'fallback-text',
          })}
          value={null}
          onChange={vi.fn()}
        />,
      )
      expect(screen.getByRole('textbox')).toHaveValue('fallback-text')

      rerender(
        <DynamicField
          schema={makeField({
            name: 'from_number',
            type: 'string',
            label: '数字文本',
          })}
          value={123}
          onChange={vi.fn()}
        />,
      )
      expect(screen.getByRole('textbox')).toHaveValue('123')

      rerender(
        <DynamicField
          schema={makeField({
            name: 'textarea_default',
            type: 'textarea',
            label: '文本域默认',
            default: 'textarea-default',
          })}
          value={undefined}
          onChange={vi.fn()}
        />,
      )
      expect(screen.getByRole('textbox')).toHaveValue('textarea-default')

      rerender(
        <DynamicField
          schema={makeField({
            name: 'slider_default',
            type: 'integer',
            label: '滑块默认',
            'x-widget': 'slider',
            default: 9,
          })}
          value="not-a-number"
          onChange={vi.fn()}
        />,
      )
      expect(screen.getByRole('slider')).toHaveAttribute('aria-valuenow', '9')
    })

    it('canonicalizes slider drafts, clamps to min/max and truncates integers', async () => {
      const schema = makeField({
        name: 'clamped_slider',
        type: 'integer',
        label: '受限滑块',
        'x-widget': 'slider',
        minValue: 1,
        maxValue: 10,
        default: 4,
      })
      let controlledValue: unknown = 4
      const onChange = vi.fn((nextValue: unknown) => {
        controlledValue = nextValue
        view.rerender(<DynamicField schema={schema} value={controlledValue} onChange={onChange} />)
      })
      const user = userEvent.setup()
      const view = render(<DynamicField schema={schema} value={controlledValue} onChange={onChange} />)
      const input = screen.getByRole('spinbutton', { name: '受限滑块 数值' })

      await user.clear(input)
      await user.type(input, '99')
      expect(onChange).toHaveBeenLastCalledWith(10)

      const sliderInput = () => screen.getByRole('spinbutton', { name: '受限滑块 数值' })
      await user.clear(sliderInput())
      await user.type(sliderInput(), '3.8')
      expect(onChange).toHaveBeenLastCalledWith(3)
      await user.tab()
      expect(sliderInput()).toHaveValue(3)
    })
  })
})
