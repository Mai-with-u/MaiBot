import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/dom'
import { render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { DynamicField } from '../DynamicField'
import type { FieldSchema } from '@/types/config-schema'

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
  })
})
