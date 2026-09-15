import type { ComponentProps } from 'react'
import type { ModelTestResult } from '@/lib/config-api'
import type { ModelInfo } from '../../types'

import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ModelTable } from '../ModelTable'

vi.mock('@/components/ui/checkbox', () => ({
  Checkbox: ({ checked, onCheckedChange }: { checked?: boolean; onCheckedChange?: () => void }) => (
    <input type="checkbox" checked={Boolean(checked)} onChange={() => onCheckedChange?.()} />
  ),
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

function renderTable(
  overrides: Partial<ComponentProps<typeof ModelTable>> & {
    paginatedModels: ModelInfo[]
    allModels: ModelInfo[]
    filteredModels: ModelInfo[]
  }
) {
  const onToggleSelectAll = vi.fn()
  render(
    <ModelTable
      selectedModels={new Set()}
      onEdit={vi.fn()}
      onDelete={vi.fn()}
      onTest={vi.fn()}
      onToggleSelection={vi.fn()}
      onToggleSelectAll={onToggleSelectAll}
      isModelUsed={() => false}
      testingModels={new Set()}
      modelTestResults={new Map()}
      searchQuery=""
      {...overrides}
    />
  )
  return { onToggleSelectAll }
}

afterEach(() => cleanup())

describe('ModelTable 缺口', () => {
  it('展示未使用、未启用视觉与未测试状态，且不展示温度', () => {
    const plain = makeModel('plain', { temperature: 0.7, visual: false })
    renderTable({
      paginatedModels: [plain],
      allModels: [plain],
      filteredModels: [plain],
    })

    expect(screen.getByLabelText('未使用')).toBeInTheDocument()
    expect(screen.getByLabelText('未启用视觉')).toBeInTheDocument()
    expect(screen.queryByText('温度')).not.toBeInTheDocument()
    expect(screen.queryByText('0.7')).not.toBeInTheDocument()
    expect(screen.getByLabelText('未测试：尚未执行模型能力测试')).toBeInTheDocument()
  })

  it('测试通过文案按视觉与耗时分支变化，失败无错误时使用默认描述', () => {
    const textOnly = makeModel('text')
    const noLatency = makeModel('fast', { visual: true })
    const visualNoLatency = makeModel('see')
    const failed = makeModel('worse')
    renderTable({
      paginatedModels: [textOnly, noLatency, visualNoLatency, failed],
      allModels: [textOnly, noLatency, visualNoLatency, failed],
      filteredModels: [textOnly, noLatency, visualNoLatency, failed],
      modelTestResults: new Map([
        ['text', makeResult({ model_name: 'text', visual_tested: false, latency_ms: 800 })],
        ['fast', makeResult({ model_name: 'fast', visual_tested: false, latency_ms: null })],
        ['see', makeResult({ model_name: 'see', visual_tested: true, latency_ms: null })],
        ['worse', makeResult({ model_name: 'worse', success: false, error: null })],
      ]),
    })

    expect(screen.getByLabelText('测试通过：文本与工具调用正常，耗时 0.80s')).toHaveClass(
      'border-green-500'
    )
    expect(screen.getByLabelText('测试通过：文本与工具调用正常')).toHaveClass('border-green-500')
    expect(screen.getByLabelText('测试通过：文本、视觉与工具调用正常')).toHaveClass(
      'border-green-500'
    )
    expect(screen.getByLabelText('模型能力测试未通过')).toHaveClass('border-red-500')
  })

  it('全选复选框随过滤结果同步，并触发全选回调', async () => {
    const user = userEvent.setup()
    const alpha = makeModel('alpha')
    const beta = makeModel('beta')
    const onToggleSelectAll = vi.fn()
    const props = {
      paginatedModels: [alpha],
      allModels: [alpha, beta],
      filteredModels: [alpha, beta],
      onEdit: vi.fn(),
      onDelete: vi.fn(),
      onTest: vi.fn(),
      onToggleSelection: vi.fn(),
      onToggleSelectAll,
      isModelUsed: () => false,
      testingModels: new Set<string>(),
      modelTestResults: new Map<string, ModelTestResult>(),
      searchQuery: '',
    }
    const { rerender } = render(<ModelTable {...props} selectedModels={new Set([0])} />)

    expect(screen.getAllByRole('checkbox')[0]).not.toBeChecked()

    rerender(<ModelTable {...props} selectedModels={new Set([0, 1])} />)
    expect(screen.getAllByRole('checkbox')[0]).toBeChecked()

    await user.click(screen.getAllByRole('checkbox')[0])
    expect(onToggleSelectAll).toHaveBeenCalledOnce()
  })
})
