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

    it('renders placeholder for array type', () => {
      const schema: FieldSchema = {
        name: 'test_array',
        type: 'array',
        label: 'Test Array',
        description: 'A test array',
        required: false,
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value={[]} onChange={onChange} />)

      expect(screen.getByText('Array fields not yet supported')).toBeInTheDocument()
    })

    it('renders placeholder for object type', () => {
      const schema: FieldSchema = {
        name: 'test_object',
        type: 'object',
        label: 'Test Object',
        description: 'A test object',
        required: false,
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value={{}} onChange={onChange} />)

      expect(screen.getByText('Object fields not yet supported')).toBeInTheDocument()
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
  })

  describe('visual features', () => {
    it('renders label with icon', () => {
      const schema: FieldSchema = {
        name: 'test_icon',
        type: 'string',
        label: 'Test Icon',
        description: 'A test with icon',
        required: false,
        'x-icon': 'Settings',
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value="" onChange={onChange} />)

      expect(screen.getByText('Test Icon')).toBeInTheDocument()
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

    it('renders description', () => {
      const schema: FieldSchema = {
        name: 'test_desc',
        type: 'string',
        label: 'Test Description',
        description: 'This is a description',
        required: false,
      }
      const onChange = vi.fn()

      render(<DynamicField schema={schema} value="" onChange={onChange} />)

      expect(screen.getByText('This is a description')).toBeInTheDocument()
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

      expect(screen.getByText('10')).toBeInTheDocument()
      expect(screen.getByText('50')).toBeInTheDocument()
      expect(screen.getByText('25')).toBeInTheDocument()
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
