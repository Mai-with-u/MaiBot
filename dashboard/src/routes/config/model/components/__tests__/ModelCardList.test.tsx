import type { ModelTestResult } from '@/lib/config-api'
import type { ModelInfo } from '../../types'

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ModelCardList } from '../ModelCardList'

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

describe('ModelCardList 缺口', () => {
  it('覆盖使用态、视觉点、测试中与各类测试结果文案', () => {
    const unused = makeModel('plain')
    const usedVisual = makeModel('vision', { visual: true })
    const testing = makeModel('busy')
    const successFull = makeModel('ok')
    const textOnly = makeModel('text')
    const noLatency = makeModel('fast')
    const visualNoLatency = makeModel('see', { visual: true })
    const failErr = makeModel('bad')
    const failNoErr = makeModel('worse')
    const models = [
      unused,
      usedVisual,
      testing,
      successFull,
      textOnly,
      noLatency,
      visualNoLatency,
      failErr,
      failNoErr,
    ]

    render(
      <ModelCardList
        paginatedModels={models}
        allModels={models}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onTest={vi.fn()}
        isModelUsed={(name) => name === 'vision'}
        testingModels={new Set(['busy'])}
        modelTestResults={new Map([
          ['ok', makeResult({ model_name: 'ok', visual_tested: true, latency_ms: 1250 })],
          ['text', makeResult({ model_name: 'text', visual_tested: false, latency_ms: 800 })],
          ['fast', makeResult({ model_name: 'fast', visual_tested: false, latency_ms: null })],
          ['see', makeResult({ model_name: 'see', visual_tested: true, latency_ms: null })],
          ['bad', makeResult({ model_name: 'bad', success: false, error: '超时' })],
          ['worse', makeResult({ model_name: 'worse', success: false, error: null })],
        ])}
        searchQuery=""
      />
    )

    expect(screen.getAllByLabelText('未使用')).toHaveLength(8)
    expect(screen.getByLabelText('已使用')).toBeInTheDocument()
    expect(screen.getAllByLabelText('未启用视觉')).toHaveLength(7)
    expect(screen.getAllByLabelText('已启用视觉')).toHaveLength(2)
    expect(screen.getAllByLabelText('未测试：尚未执行模型能力测试').map((el) => el.textContent)).toEqual(
      ['plain', 'vision']
    )

    const testingName = screen.getByLabelText('正在测试模型能力')
    expect(testingName).toHaveTextContent('busy')
    expect(testingName).toHaveClass('border-amber-500')
    expect(screen.getByRole('button', { name: '测试模型 busy' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '测试模型 plain' })).toBeEnabled()

    expect(screen.getByLabelText('测试通过：文本、视觉与工具调用正常，耗时 1.25s')).toHaveClass(
      'border-green-500'
    )
    expect(screen.getByLabelText('测试通过：文本与工具调用正常，耗时 0.80s')).toHaveClass(
      'border-green-500'
    )
    expect(screen.getByLabelText('测试通过：文本与工具调用正常')).toHaveClass('border-green-500')
    expect(screen.getByLabelText('测试通过：文本、视觉与工具调用正常')).toHaveClass(
      'border-green-500'
    )
    expect(screen.getByLabelText('超时')).toHaveClass('border-red-500')
    expect(screen.getByLabelText('模型能力测试未通过')).toHaveClass('border-red-500')

    expect(screen.getAllByText('¥1/M')).toHaveLength(9)
    expect(screen.getAllByText('¥2/M')).toHaveLength(9)
  })
})
