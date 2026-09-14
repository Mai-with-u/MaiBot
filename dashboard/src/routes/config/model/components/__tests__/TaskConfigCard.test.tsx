import type { ReactNode } from 'react'
import type { TaskConfig } from '../../types'

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { TaskConfigCard } from '../TaskConfigCard'

vi.mock('@/components/ui/multi-select', () => ({
  MultiSelect: ({
    selected,
    singleSelect,
  }: {
    selected: string[]
    singleSelect?: boolean
  }) => (
    <div
      data-testid="multi-select"
      data-selected={selected.join(',')}
      data-single-select={singleSelect ? 'true' : 'false'}
    />
  ),
}))

vi.mock('@/components/ui/slider', () => ({
  Slider: ({ value }: { value: number[] }) => (
    <button type="button" data-testid="temperature-slider">
      {value[0]}
    </button>
  ),
}))

vi.mock('@/components/ui/select', () => ({
  Select: ({ children, value }: { children: ReactNode; value: string }) => (
    <div data-testid="select-root" data-value={value}>
      {children}
    </div>
  ),
  SelectTrigger: ({ children, ...props }: { children: ReactNode }) => <div {...props}>{children}</div>,
  SelectValue: () => <span>当前选项</span>,
  SelectContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectItem: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))

const baseTask: TaskConfig = {
  model_list: ['alpha'],
  temperature: 0.7,
  max_tokens: 4096,
  selection_strategy: 'balance',
}

afterEach(() => cleanup())

describe('TaskConfigCard 缺口', () => {
  it('缺省字段回落到空模型列表、温度 0.7、Token 4096 与 balance', () => {
    render(
      <TaskConfigCard
        title="缺省任务"
        description="默认值"
        taskConfig={{} as TaskConfig}
        modelNames={[]}
        onChange={vi.fn()}
      />
    )

    expect(screen.getByTestId('multi-select')).toHaveAttribute('data-selected', '')
    expect(screen.getByLabelText('温度')).toHaveValue(0.7)
    expect(screen.getByTestId('temperature-slider')).toHaveTextContent('0.7')
    expect(screen.getByDisplayValue('4096')).toBeInTheDocument()
    expect(screen.getByTestId('select-root')).toHaveAttribute('data-value', 'balance')
  })

  it('温度非法值被忽略，越界值被钳制到 0 和 2', () => {
    const onChange = vi.fn()
    render(
      <TaskConfigCard
        title="温度任务"
        description="钳制"
        taskConfig={baseTask}
        modelNames={[]}
        onChange={onChange}
      />
    )

    const input = screen.getByLabelText('温度')
    fireEvent.change(input, { target: { value: 'abc' } })
    expect(onChange).not.toHaveBeenCalled()

    fireEvent.change(input, { target: { value: '-1' } })
    expect(onChange).toHaveBeenCalledWith('temperature', 0)

    fireEvent.change(input, { target: { value: '2.4' } })
    expect(onChange).toHaveBeenCalledWith('temperature', 2)
  })

  it('高级模式、单选、导览标记与超时默认值/非法值', () => {
    const onChange = vi.fn()
    const { container } = render(
      <TaskConfigCard
        title="嵌入任务"
        description="高级"
        taskConfig={baseTask}
        modelNames={['alpha']}
        onChange={onChange}
        advanced
        singleModel
        dataTour="embedding-models"
        showAdvancedSettings
      />
    )

    expect(container.firstChild).toHaveClass('bg-amber-50/30')
    expect(screen.getByTestId('multi-select')).toHaveAttribute('data-single-select', 'true')
    expect(document.querySelector('[data-tour="embedding-models"]')).not.toBeNull()

    expect(screen.getByDisplayValue('15')).toBeInTheDocument()
    expect(screen.getByDisplayValue('240')).toBeInTheDocument()

    fireEvent.change(screen.getByDisplayValue('15'), { target: { value: 'abc' } })
    fireEvent.change(screen.getByDisplayValue('240'), { target: { value: '' } })
    expect(onChange).not.toHaveBeenCalled()
  })
})
