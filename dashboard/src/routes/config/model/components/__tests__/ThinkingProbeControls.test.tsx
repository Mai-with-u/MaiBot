import type { ThinkingProbeMode, ThinkingProbeResult } from '@/lib/config-api'

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { probeModelThinking } from '@/lib/config-api'
import { ThinkingProbeControls } from '../ThinkingProbeControls'

vi.mock('@/lib/config-api', () => ({ probeModelThinking: vi.fn() }))

const probeMock = vi.mocked(probeModelThinking)
const modes: ThinkingProbeMode[] = ['thinking_type', 'enable_thinking', 'reasoning_effort_nested']

function matched(mode: ThinkingProbeMode = 'thinking_type'): ThinkingProbeResult {
  return {
    status: 'matched',
    matches: [mode],
    attempts: [{ mode, status: 'matched', message: '开关响应证据已匹配' }],
    message: '已找到支持的思考参数格式。',
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((complete) => { resolve = complete })
  return { promise, resolve }
}

function makeProps(extraParams: Record<string, unknown> = {}) {
  return {
    apiProvider: 'relay',
    modelIdentifier: 'test-model',
    forceStreamMode: false,
    extraParams,
    onChange: vi.fn(),
    onBusyChange: vi.fn(),
  }
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('ThinkingProbeControls', () => {
  it.each(modes)('%s 匹配后应用草稿，重新打开恢复开关而不伪造验证状态', async (mode) => {
    const pending = deferred<ThinkingProbeResult>()
    probeMock.mockReturnValue(pending.promise)
    const props = makeProps({ headers: { 'X-Route': 'keep' } })
    const view = render(<ThinkingProbeControls {...props} />)

    fireEvent.click(screen.getByRole('button', { name: '自动测试' }))
    expect(screen.getByRole('button', { name: '正在测试…' })).toBeDisabled()
    expect(props.onBusyChange).toHaveBeenLastCalledWith(true)
    expect(probeMock).toHaveBeenCalledWith({
      api_provider: 'relay', model_identifier: 'test-model',
      extra_params: props.extraParams, force_stream_mode: false,
    }, expect.any(AbortSignal))

    await act(async () => { pending.resolve(matched(mode)) })
    expect(props.onChange).toHaveBeenCalledTimes(1)
    const applied = props.onChange.mock.calls[0][0] as Record<string, unknown>
    expect(applied.headers).toEqual({ 'X-Route': 'keep' })
    expect(props.onBusyChange).toHaveBeenLastCalledWith(false)
    view.rerender(<ThinkingProbeControls {...props} extraParams={applied} />)
    expect(screen.getByRole('status')).toHaveTextContent('已找到支持的思考参数格式。')
    expect(screen.getByRole('switch', { name: '启用思考' })).toBeChecked()

    fireEvent.click(screen.getByRole('switch', { name: '启用思考' }))
    const disabledParams = props.onChange.mock.calls[1][0] as Record<string, unknown>
    view.unmount()
    render(<ThinkingProbeControls {...props} extraParams={disabledParams} />)
    expect(screen.getByRole('switch', { name: '启用思考' })).not.toBeChecked()
    expect(screen.getByText(/已配置参数格式：/)).toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(probeMock).toHaveBeenCalledTimes(1)
  })

  it('无法确认时展示原因且不改变草稿', async () => {
    const pending = deferred<ThinkingProbeResult>()
    probeMock.mockReturnValue(pending.promise)
    const props = makeProps({ top_p: 0.8 })
    render(<ThinkingProbeControls {...props} />)
    fireEvent.click(screen.getByRole('button', { name: '自动测试' }))
    await act(async () => {
      pending.resolve({ status: 'inconclusive', matches: [], attempts: [], message: '缺少足以确认的思考证据。' })
    })
    expect(props.onChange).not.toHaveBeenCalled()
    expect(screen.getByRole('status')).toHaveTextContent('缺少足以确认的思考证据。')
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '自动测试' })).toBeEnabled()
  })

  it('关闭后丢弃旧结果，即使重新打开的是同一个模型', async () => {
    const pending = deferred<ThinkingProbeResult>()
    probeMock.mockReturnValue(pending.promise)
    const props = makeProps()
    const view = render(<ThinkingProbeControls {...props} />)
    fireEvent.click(screen.getByRole('button', { name: '自动测试' }))
    const signal = probeMock.mock.calls[0][1]!
    view.unmount()
    expect(signal.aborted).toBe(true)
    render(<ThinkingProbeControls {...props} />)
    await act(async () => { pending.resolve(matched()) })
    expect(props.onChange).not.toHaveBeenCalled()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('按目标重新挂载后旧请求不能应用参数或结束新请求的忙碌状态', async () => {
    const oldRequest = deferred<ThinkingProbeResult>()
    const newRequest = deferred<ThinkingProbeResult>()
    probeMock.mockReturnValueOnce(oldRequest.promise).mockReturnValueOnce(newRequest.promise)
    const props = makeProps()
    const view = render(<ThinkingProbeControls key="model-a" {...props} />)
    fireEvent.click(screen.getByRole('button', { name: '自动测试' }))
    const oldSignal = probeMock.mock.calls[0][1]!
    view.rerender(<ThinkingProbeControls key="model-b" {...props} modelIdentifier="other-model" />)
    expect(oldSignal.aborted).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '自动测试' }))
    await act(async () => { oldRequest.resolve(matched()) })
    expect(props.onChange).not.toHaveBeenCalled()
    expect(props.onBusyChange).toHaveBeenLastCalledWith(true)
    expect(screen.getByRole('button', { name: '正在测试…' })).toBeDisabled()
    await act(async () => { newRequest.resolve(matched('enable_thinking')) })
    expect(props.onChange).toHaveBeenCalledExactlyOnceWith({ enable_thinking: true })
  })

  it('请求期间额外参数变化后不覆盖新草稿', async () => {
    const pending = deferred<ThinkingProbeResult>()
    probeMock.mockReturnValue(pending.promise)
    const props = makeProps({ top_p: 0.8 })
    const view = render(<ThinkingProbeControls {...props} />)
    fireEvent.click(screen.getByRole('button', { name: '自动测试' }))
    view.rerender(<ThinkingProbeControls {...props} extraParams={{ top_p: 0.5 }} />)
    await act(async () => { pending.resolve(matched()) })
    expect(props.onChange).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('额外参数已变化，本次结果未应用')
    expect(props.onBusyChange).toHaveBeenLastCalledWith(false)
  })

  it('达到总时限取消请求，保留草稿并允许重新测试', async () => {
    vi.useFakeTimers()
    probeMock.mockImplementation((_draft, signal) => new Promise((_resolve, reject) => {
      signal!.addEventListener('abort', () => reject(new Error('请求已取消')), { once: true })
    }))
    const props = makeProps({ enable_thinking: false })
    render(<ThinkingProbeControls {...props} />)
    fireEvent.click(screen.getByRole('button', { name: '自动测试' }))
    await act(async () => { await vi.advanceTimersByTimeAsync(90_000) })
    expect(probeMock.mock.calls[0][1]!.aborted).toBe(true)
    expect(screen.getByRole('alert')).toHaveTextContent('测试超时')
    expect(props.onChange).not.toHaveBeenCalled()
    expect(props.onBusyChange).toHaveBeenLastCalledWith(false)
    expect(screen.getByRole('button', { name: '自动测试' })).toBeEnabled()
    expect(screen.getByRole('switch', { name: '启用思考' })).not.toBeChecked()
  })
})
