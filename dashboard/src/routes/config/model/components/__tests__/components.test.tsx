import type { ReactNode } from 'react'
import type { ModelTestResult } from '@/lib/config-api'
import type { ModelInfo, TaskConfig } from '../../types'

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ModelCardList } from '../ModelCardList'
import { ModelTable } from '../ModelTable'
import { Pagination } from '../Pagination'
import { TaskConfigCard } from '../TaskConfigCard'

vi.mock('@/components/ui/checkbox', () => ({
  Checkbox: ({ checked, onCheckedChange }: { checked?: boolean; onCheckedChange?: () => void }) => (
    <input type="checkbox" checked={Boolean(checked)} onChange={() => onCheckedChange?.()} />
  ),
}))

vi.mock('@/components/ui/multi-select', () => ({
  MultiSelect: ({
    options,
    selected,
    onChange,
  }: {
    options: Array<{ label: string; value: string }>
    selected: string[]
    onChange: (value: string[]) => void
  }) => (
    <div data-testid="multi-select" data-selected={selected.join(',')}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange([...selected, option.value])}
        >
          {option.label}
        </button>
      ))}
    </div>
  ),
}))

vi.mock('@/components/ui/slider', () => ({
  Slider: ({
    value,
    onValueChange,
  }: {
    value: number[]
    onValueChange: (value: number[]) => void
  }) => (
    <button type="button" data-testid="temperature-slider" onClick={() => onValueChange([1.25])}>
      {value[0]}
    </button>
  ),
}))

vi.mock('@/components/ui/select', () => ({
  Select: ({
    children,
    value,
    onValueChange,
  }: {
    children: ReactNode
    value: string
    onValueChange: (value: string) => void
  }) => (
    <div data-testid="select-root" data-value={value}>
      {children}
      <button type="button" onClick={() => onValueChange(value === '10' ? '20' : 'random')}>
        切换选项
      </button>
    </div>
  ),
  SelectTrigger: ({ children, ...props }: { children: ReactNode }) => (
    <div {...props}>{children}</div>
  ),
  SelectValue: () => <span>当前选项</span>,
  SelectContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectItem: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))

function makeModel(name: string, overrides: Partial<ModelInfo> = {}): ModelInfo {
  return {
    name,
    model_identifier: `${name}-id`,
    api_provider: 'provider-a',
    price_in: 1,
    price_out: 2,
    temperature: 0.7,
    visual: false,
    ...overrides,
  }
}

function makeResult(overrides: Partial<ModelTestResult> = {}): ModelTestResult {
  return {
    success: true,
    model_name: 'alpha',
    visual_tested: true,
    tool_call_ok: true,
    response: 'ok',
    reasoning: '',
    tool_calls: [],
    latency_ms: 1250,
    error: null,
    prompt_tokens: 1,
    completion_tokens: 2,
    total_tokens: 3,
    ...overrides,
  }
}

afterEach(() => cleanup())

describe('ModelTable', () => {
  it('渲染模型属性和测试状态，并使用 allModels 中的真实索引', async () => {
    const user = userEvent.setup()
    const hidden = makeModel('hidden')
    const alpha = makeModel('alpha', { visual: true })
    const onEdit = vi.fn()
    const onDelete = vi.fn()
    const onTest = vi.fn()
    const onToggleSelection = vi.fn()
    render(
      <ModelTable
        paginatedModels={[alpha]}
        allModels={[hidden, alpha]}
        filteredModels={[alpha]}
        selectedModels={new Set([1])}
        onEdit={onEdit}
        onDelete={onDelete}
        onTest={onTest}
        onToggleSelection={onToggleSelection}
        onToggleSelectAll={vi.fn()}
        isModelUsed={() => true}
        testingModels={new Set()}
        modelTestResults={new Map([['alpha', makeResult()]])}
        searchQuery=""
      />
    )

    expect(screen.getByText('alpha-id')).toBeInTheDocument()
    expect(screen.getByLabelText('已使用')).toBeInTheDocument()
    expect(screen.getByLabelText('已启用视觉')).toBeInTheDocument()
    const successName = screen.getByLabelText('测试通过：文本、视觉与工具调用正常，耗时 1.25s')
    expect(successName).toHaveTextContent('alpha')
    expect(successName).toHaveClass('border-green-500')
    expect(screen.queryByRole('columnheader', { name: '测试' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '测试模型 alpha' }))
    await user.click(screen.getByRole('button', { name: '编辑模型 alpha' }))
    await user.click(screen.getByRole('button', { name: '删除模型 alpha' }))
    await user.click(screen.getAllByRole('checkbox')[1])

    expect(onTest).toHaveBeenCalledWith('alpha')
    expect(onEdit).toHaveBeenCalledWith(alpha, 1)
    expect(onDelete).toHaveBeenCalledWith(1)
    expect(onToggleSelection).toHaveBeenCalledWith(1)
  })

  it('展示空态并区分无配置与搜索无结果', () => {
    const props = {
      paginatedModels: [],
      allModels: [],
      filteredModels: [],
      selectedModels: new Set<number>(),
      onEdit: vi.fn(),
      onDelete: vi.fn(),
      onTest: vi.fn(),
      onToggleSelection: vi.fn(),
      onToggleSelectAll: vi.fn(),
      isModelUsed: vi.fn(() => false),
      testingModels: new Set<string>(),
      modelTestResults: new Map<string, ModelTestResult>(),
    }
    const { rerender } = render(<ModelTable {...props} searchQuery="" />)
    expect(screen.getByText('暂无模型配置')).toBeInTheDocument()
    rerender(<ModelTable {...props} searchQuery="missing" />)
    expect(screen.getByText('未找到匹配的模型')).toBeInTheDocument()
  })

  it('正在测试时禁用测试按钮，失败结果暴露错误原因', () => {
    const alpha = makeModel('alpha')
    const props = {
      paginatedModels: [alpha],
      allModels: [alpha],
      filteredModels: [alpha],
      selectedModels: new Set<number>(),
      onEdit: vi.fn(),
      onDelete: vi.fn(),
      onTest: vi.fn(),
      onToggleSelection: vi.fn(),
      onToggleSelectAll: vi.fn(),
      isModelUsed: vi.fn(() => false),
      searchQuery: '',
    }
    const { rerender } = render(
      <ModelTable {...props} testingModels={new Set(['alpha'])} modelTestResults={new Map()} />
    )
    expect(screen.getByLabelText('正在测试模型能力')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '测试模型 alpha' })).toBeDisabled()

    rerender(
      <ModelTable
        {...props}
        testingModels={new Set()}
        modelTestResults={new Map([['alpha', makeResult({ success: false, error: '鉴权失败' })]])}
      />
    )
    expect(screen.getByLabelText('鉴权失败')).toHaveClass('border-red-500')
  })
})

describe('ModelCardList', () => {
  it('移动卡片展示模型并触发测试、编辑、删除', async () => {
    const user = userEvent.setup()
    const first = makeModel('first')
    const alpha = makeModel('alpha')
    const onEdit = vi.fn()
    const onDelete = vi.fn()
    const onTest = vi.fn()
    render(
      <ModelCardList
        paginatedModels={[alpha]}
        allModels={[first, alpha]}
        onEdit={onEdit}
        onDelete={onDelete}
        onTest={onTest}
        isModelUsed={() => false}
        testingModels={new Set()}
        modelTestResults={new Map()}
        searchQuery=""
      />
    )

    expect(screen.getByText('provider-a')).toBeInTheDocument()
    expect(screen.getByLabelText('未使用')).toBeInTheDocument()
    expect(screen.getByLabelText('未测试：尚未执行模型能力测试')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '测试模型 alpha' }))
    await user.click(screen.getByRole('button', { name: '编辑模型 alpha' }))
    await user.click(screen.getByRole('button', { name: '删除模型 alpha' }))
    expect(onTest).toHaveBeenCalledWith('alpha')
    expect(onEdit).toHaveBeenCalledWith(alpha, 1)
    expect(onDelete).toHaveBeenCalledWith(1)
  })

  it('空列表根据搜索词显示对应文案', () => {
    const props = {
      paginatedModels: [],
      allModels: [],
      onEdit: vi.fn(),
      onDelete: vi.fn(),
      onTest: vi.fn(),
      isModelUsed: vi.fn(() => false),
      testingModels: new Set<string>(),
      modelTestResults: new Map<string, ModelTestResult>(),
    }
    const { rerender } = render(<ModelCardList {...props} searchQuery="" />)
    expect(screen.getByText('暂无模型配置')).toBeInTheDocument()
    rerender(<ModelCardList {...props} searchQuery="x" />)
    expect(screen.getByText('未找到匹配的模型')).toBeInTheDocument()
  })
})

describe('TaskConfigCard', () => {
  const baseTask: TaskConfig = {
    model_list: ['alpha'],
    temperature: 0.7,
    max_tokens: 4096,
    selection_strategy: 'balance',
  }

  it('模型、温度、Token 和策略变更使用明确字段回调', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <TaskConfigCard
        title="回复模型"
        description="用于回复"
        taskConfig={baseTask}
        modelNames={['beta']}
        onChange={onChange}
      />
    )

    await user.click(screen.getByRole('button', { name: 'beta' }))
    await user.click(screen.getByTestId('temperature-slider'))
    fireEvent.change(screen.getByLabelText('温度'), { target: { value: '3.5' } })
    fireEvent.change(screen.getByDisplayValue('4096'), { target: { value: '8192' } })
    await user.click(screen.getByRole('button', { name: '切换选项' }))

    expect(onChange).toHaveBeenCalledWith('model_list', ['alpha', 'beta'])
    expect(onChange).toHaveBeenCalledWith('temperature', 1.25)
    expect(onChange).toHaveBeenCalledWith('temperature', 2)
    expect(onChange).toHaveBeenCalledWith('max_tokens', 8192)
    expect(onChange).toHaveBeenCalledWith('selection_strategy', 'random')
  })

  it('高级阈值拒绝无效值并接受大于等于一的整数', () => {
    const onChange = vi.fn()
    render(
      <TaskConfigCard
        title="高级任务"
        description="高级参数"
        taskConfig={{ ...baseTask, slow_threshold: 15 }}
        modelNames={[]}
        onChange={onChange}
        showAdvancedSettings
      />
    )
    const threshold = screen.getByDisplayValue('15')
    fireEvent.change(threshold, { target: { value: '0' } })
    expect(onChange).not.toHaveBeenCalledWith('slow_threshold', 0)
    fireEvent.change(threshold, { target: { value: '20' } })
    expect(onChange).toHaveBeenCalledWith('slow_threshold', 20)
  })

  it('隐藏项不会渲染温度和最大 Token 输入', () => {
    render(
      <TaskConfigCard
        title="嵌入任务"
        description="仅模型"
        taskConfig={baseTask}
        modelNames={[]}
        onChange={vi.fn()}
        hideTemperature
        hideMaxTokens
      />
    )
    expect(screen.queryByText('温度')).not.toBeInTheDocument()
    expect(screen.queryByText('最大 Token')).not.toBeInTheDocument()
  })
})

describe('Pagination', () => {
  it('无数据时不渲染分页模块', () => {
    const { container } = render(
      <Pagination
        page={1}
        pageSize={10}
        totalItems={0}
        jumpToPage=""
        onPageChange={vi.fn()}
        onPageSizeChange={vi.fn()}
        onJumpToPageChange={vi.fn()}
        onJumpToPage={vi.fn()}
      />
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('显示范围并执行翻页、输入和回车跳转', async () => {
    const user = userEvent.setup()
    const onPageChange = vi.fn()
    const onJumpToPageChange = vi.fn()
    const onJumpToPage = vi.fn()
    render(
      <Pagination
        page={2}
        pageSize={10}
        totalItems={25}
        jumpToPage="3"
        onPageChange={onPageChange}
        onPageSizeChange={vi.fn()}
        onJumpToPageChange={onJumpToPageChange}
        onJumpToPage={onJumpToPage}
      />
    )

    expect(screen.getByText('11 到 20 条，共 25 条')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '第一页' }))
    await user.click(screen.getByRole('button', { name: '上一页' }))
    await user.click(screen.getByRole('button', { name: '下一页' }))
    await user.click(screen.getByRole('button', { name: '最后一页' }))
    expect(onPageChange.mock.calls.map(([page]) => page)).toEqual([1, 1, 3, 3])

    const input = screen.getByRole('spinbutton')
    await user.clear(input)
    await user.type(input, '2{Enter}')
    expect(onJumpToPageChange).toHaveBeenCalled()
    expect(onJumpToPage).toHaveBeenCalledOnce()
  })

  it('修改每页数量时重置页码并清空选择', async () => {
    const user = userEvent.setup()
    const onPageChange = vi.fn()
    const onPageSizeChange = vi.fn()
    const onSelectionClear = vi.fn()
    render(
      <Pagination
        page={2}
        pageSize={10}
        totalItems={25}
        jumpToPage=""
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
        onJumpToPageChange={vi.fn()}
        onJumpToPage={vi.fn()}
        onSelectionClear={onSelectionClear}
      />
    )

    await user.click(screen.getByRole('button', { name: '切换选项' }))
    expect(onPageSizeChange).toHaveBeenCalledWith(20)
    expect(onPageChange).toHaveBeenCalledWith(1)
    expect(onSelectionClear).toHaveBeenCalledOnce()
  })
})
