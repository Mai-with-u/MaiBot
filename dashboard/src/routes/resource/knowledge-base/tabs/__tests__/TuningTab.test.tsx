/**
 * TuningTab：用 mock useMemoryTuning 结果锁定调参表单、保存、预览、错误与禁用态。
 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Tabs } from '@/components/ui/tabs'
import i18n from '@/i18n'
import type { MemoryTaskPayload } from '@/lib/memory-api'

import type { UseMemoryTuningResult } from '../../hooks/useMemoryTuning'
import { formatImportTime } from '../../utils'
import { TuningTab } from '../TuningTab'

vi.mock('@/components/CodeEditor', () => ({
  CodeEditor: ({
    value,
    language,
    readOnly,
  }: {
    value: string
    language?: string
    readOnly?: boolean
  }) => (
    <pre data-testid="tuning-toml-editor" data-language={language} data-readonly={String(Boolean(readOnly))}>
      {value}
    </pre>
  ),
}))

afterEach(() => {
  cleanup()
})

beforeEach(async () => {
  await i18n.changeLanguage('zh')
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {}
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {}
  }
})

function makeTask(overrides: MemoryTaskPayload = {}): MemoryTaskPayload {
  return {
    task_id: 'tune-1',
    status: 'completed',
    progress: 100,
    rounds_done: 20,
    rounds_total: 20,
    created_at: 1_710_000_000,
    updated_at: 1_710_000_100,
    ...overrides,
  }
}

function makeRecommendedTask(overrides: MemoryTaskPayload = {}): MemoryTaskPayload {
  return makeTask({
    best_score: 0.58,
    recommended: true,
    validation_summary: {
      recommended: true,
      holdout_case_count: 6,
      deltas: {
        score: 0.08,
        precision_at_1: 0.1,
        recall_at_k: 0.12,
        empty_rate: -0.02,
        avg_elapsed_ms: 14,
      },
      online_like: {
        baseline: {
          score: 0.5,
          metrics: {
            precision_at_1: 0.6,
            recall_at_k: 0.5,
            empty_rate: 0.08,
            avg_elapsed_ms: 80,
          },
        },
        best: {
          score: 0.58,
          metrics: {
            precision_at_1: 0.7,
            recall_at_k: 0.62,
            empty_rate: 0.06,
            avg_elapsed_ms: 94,
          },
        },
      },
    },
    ...overrides,
  })
}

function makeTuning(overrides: Partial<UseMemoryTuningResult> = {}): UseMemoryTuningResult {
  return {
    tuningObjective: 'precision_priority',
    setTuningObjective: vi.fn(),
    tuningIntensity: 'standard',
    setTuningIntensity: vi.fn(),
    tuningSampleSize: '24',
    setTuningSampleSize: vi.fn(),
    tuningTopKEval: '20',
    setTuningTopKEval: vi.fn(),
    persistBestProfile: false,
    setPersistBestProfile: vi.fn(),
    submitTuningTask: vi.fn(async () => {}),
    creatingTuning: false,
    tuningProfile: { runtime: {}, persistable: {} },
    tuningProfileToml: '',
    tuningTasks: [],
    applyBestTask: vi.fn(async () => {}),
    tuningErrorText: '',
    ...overrides,
  }
}

function renderTuning(overrides: Partial<UseMemoryTuningResult> = {}) {
  const tuning = makeTuning(overrides)
  const view = render(
    <Tabs defaultValue="tuning">
      <TuningTab tuning={tuning} />
    </Tabs>,
  )
  return { ...view, tuning }
}

async function openParameterDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: '调优参数' }))
  return screen.findByRole('dialog', { name: '调优参数' })
}

describe('TuningTab', () => {
  it('空状态渲染调参入口、结果占位，开始调优可点', () => {
    renderTuning()

    expect(screen.getByText('记忆搜索调优')).toBeInTheDocument()
    expect(screen.getByText('评估并改善记忆搜索效果')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始调优' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '调优参数' })).toBeEnabled()
    expect(screen.queryByText('优化目标')).not.toBeInTheDocument()
    expect(screen.getByText('暂无调优结果。创建并完成任务后，这里会显示评估结果。')).toBeInTheDocument()
    expect(screen.getByText('暂无调优记录')).toBeInTheDocument()
    expect(screen.getByText('暂时没有正在使用的结果。')).not.toBeVisible()
  })

  it('调参弹层渲染默认字段，写入目标、强度、样本与持久化', async () => {
    const user = userEvent.setup()
    const { tuning } = renderTuning({ persistBestProfile: true })

    const dialog = await openParameterDialog(user)
    expect(within(dialog).getByText('这些设置只影响下一次调优任务，一般保持默认即可。')).toBeInTheDocument()
    expect(within(dialog).getByText('调优策略')).toBeInTheDocument()
    expect(within(dialog).getByText('评估范围')).toBeInTheDocument()
    expect(screen.getByLabelText('样本量')).toHaveValue(24)
    expect(screen.getByLabelText('每次评估查看数量')).toHaveValue(20)
    expect(screen.getByLabelText('同时保存为默认设置')).toBeChecked()

    await user.click(screen.getByRole('combobox', { name: '优化目标' }))
    await user.click(screen.getByRole('option', { name: '准确率与召回平衡' }))
    expect(tuning.setTuningObjective).toHaveBeenCalledWith('balanced')

    await user.click(screen.getByRole('combobox', { name: '评估强度' }))
    await user.click(screen.getByRole('option', { name: '深入' }))
    expect(tuning.setTuningIntensity).toHaveBeenCalledWith('deep')

    fireEvent.change(screen.getByLabelText('样本量'), { target: { value: '32' } })
    expect(tuning.setTuningSampleSize).toHaveBeenCalledWith('32')
    fireEvent.change(screen.getByLabelText('每次评估查看数量'), { target: { value: '16' } })
    expect(tuning.setTuningTopKEval).toHaveBeenCalledWith('16')

    await user.click(screen.getByLabelText('同时保存为默认设置'))
    expect(tuning.setPersistBestProfile).toHaveBeenCalledWith(false)
  })

  it('未勾选持久化时勾选会写入 true，关闭弹层后仍可保存任务', async () => {
    const user = userEvent.setup()
    const { tuning } = renderTuning()

    await openParameterDialog(user)
    await user.click(screen.getByRole('combobox', { name: '优化目标' }))
    await user.click(screen.getByRole('option', { name: '优先召回率' }))
    expect(tuning.setTuningObjective).toHaveBeenCalledWith('recall_priority')
    await user.click(screen.getByRole('combobox', { name: '评估强度' }))
    await user.click(screen.getByRole('option', { name: '快速' }))
    expect(tuning.setTuningIntensity).toHaveBeenCalledWith('quick')

    expect(screen.getByLabelText('同时保存为默认设置')).not.toBeChecked()
    await user.click(screen.getByLabelText('同时保存为默认设置'))
    expect(tuning.setPersistBestProfile).toHaveBeenCalledWith(true)

    await user.click(screen.getByRole('button', { name: '关闭' }))
    await user.click(screen.getByRole('button', { name: '开始调优' }))
    expect(tuning.submitTuningTask).toHaveBeenCalledOnce()
  })

  it('创建中禁用开始调优，点击不会提交', async () => {
    const user = userEvent.setup()
    const { tuning } = renderTuning({ creatingTuning: true })

    const createButton = screen.getByRole('button', { name: '开始调优' })
    expect(createButton).toBeDisabled()
    await user.click(createButton)
    expect(tuning.submitTuningTask).not.toHaveBeenCalled()
  })

  it('展开设置详情预览运行中/可保存参数、差异与技术项', async () => {
    const user = userEvent.setup()
    renderTuning({
      tuningProfile: {
        runtime: {
          retrieval: {
            top_k: 10,
            enable_ppr: true,
            sparse: { mode: 'auto', enabled: false },
            fusion: { method: 'weighted_rrf' },
            vector_pools: { mode: 'dual' },
            extra_debug: { nested: [1, 2] },
          },
          threshold: { percentile: Number.POSITIVE_INFINITY, min_results: null },
        },
        persistable: {
          retrieval: {
            top_k: 12,
            enable_ppr: false,
            sparse: { mode: 'always' },
            fusion: { method: 'rrf' },
            vector_pools: { mode: 'single' },
            notes: '',
          },
          orphan: { leaf: { value: 1 } },
        },
      },
    })

    expect(screen.getByText('调优结果已经用于当前记忆搜索。')).not.toBeVisible()
    expect(screen.getByText('这组结果可按需保存为默认设置。')).not.toBeVisible()
    expect(screen.getByText('当前使用的设置与可保存设置存在差异，展开详情可查看。')).not.toBeVisible()

    const hiddenTopK = screen.getAllByText('初步查找数量')
    hiddenTopK.forEach((label) => expect(label).not.toBeVisible())

    await user.click(screen.getByText('设置详情'))
    expect(hiddenTopK[0]).toBeVisible()
    expect(screen.getAllByText('10').length).toBeGreaterThan(0)
    expect(screen.getAllByText('12').length).toBeGreaterThan(0)
    expect(screen.getAllByText('启用').length).toBeGreaterThan(0)
    expect(screen.getAllByText('关闭').length).toBeGreaterThan(0)
    expect(screen.getAllByText('自动').length).toBeGreaterThan(0)
    expect(screen.getAllByText('始终启用').length).toBeGreaterThan(0)
    expect(screen.getAllByText('综合排序').length).toBeGreaterThan(0)
    expect(screen.getAllByText('排序整合').length).toBeGreaterThan(0)
    expect(screen.getAllByText('双路语义搜索').length).toBeGreaterThan(0)
    expect(screen.getAllByText('单路语义搜索').length).toBeGreaterThan(0)
    expect(screen.getAllByText('null').length).toBeGreaterThan(0)
    expect(screen.getAllByText('-').length).toBeGreaterThan(0)
    expect(screen.getByText('另有 1 个高级项目未在摘要中展示，可切到高级配置查看。')).toBeInTheDocument()
    expect(screen.getByText('另有 2 个高级项目未在摘要中展示，可切到高级配置查看。')).toBeInTheDocument()
    expect(screen.getByText('另有 3 个高级差异未在摘要中展示，可切到高级配置查看。')).toBeInTheDocument()
    expect(screen.getAllByText('当前使用').length).toBeGreaterThan(0)
    expect(screen.getAllByText('保存后').length).toBeGreaterThan(0)
  })

  it('空快照展开后显示占位，切到 TOML 预览再返回摘要', async () => {
    const user = userEvent.setup()
    renderTuning({
      tuningProfileToml: '[retrieval]\ntop_k = 10\n',
    })

    await user.click(screen.getByText('设置详情'))
    expect(screen.getAllByText('暂无数据').length).toBeGreaterThan(0)
    expect(screen.getByText('两份配置当前没有差异。')).toBeInTheDocument()
    expect(screen.getByText('暂时没有正在使用的结果。')).toBeVisible()
    expect(screen.getByText('暂时没有可保存结果。')).toBeVisible()
    expect(screen.getByText('当前使用的设置与可保存设置一致。')).toBeVisible()

    await user.click(screen.getByRole('button', { name: '查看高级配置' }))
    const editor = screen.getByTestId('tuning-toml-editor')
    expect(editor).toHaveTextContent('[retrieval] top_k = 10')
    expect(editor).toHaveAttribute('data-language', 'toml')
    expect(editor).toHaveAttribute('data-readonly', 'true')
    expect(screen.getByText('面向排查的高级配置内容。')).toBeInTheDocument()
    expect(screen.queryByText('设置详情')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '返回结果摘要' }))
    expect(screen.getByText('设置详情')).toBeInTheDocument()
    expect(screen.queryByTestId('tuning-toml-editor')).not.toBeInTheDocument()
  })

  it('仅有技术差异时摘要提示有差异，可读差异仍为空', async () => {
    const user = userEvent.setup()
    renderTuning({
      tuningProfile: {
        runtime: { internal: { debug: true } },
        persistable: { internal: { debug: false } },
      },
    })

    await user.click(screen.getByText('设置详情'))
    expect(screen.getByText('当前使用的设置与可保存设置存在差异，展开详情可查看。')).toBeVisible()
    expect(screen.getByText('两份配置当前没有差异。')).toBeInTheDocument()
    expect(screen.getByText('另有 1 个高级差异未在摘要中展示，可切到高级配置查看。')).toBeInTheDocument()
  })

  it('快照把数组、空串和其余枚举值格式化后展示', async () => {
    const user = userEvent.setup()
    renderTuning({
      tuningProfile: {
        runtime: {
          retrieval: {
            top_k: [8, 16],
            alpha: '',
            sparse: { mode: 'off' },
            fusion: { method: 'alpha_legacy' },
          },
        },
        persistable: {},
      },
    })

    await user.click(screen.getByText('设置详情'))
    expect(screen.getAllByText('[8,16]').length).toBeGreaterThan(0)
    expect(screen.getAllByText('""').length).toBeGreaterThan(0)
    expect(screen.getAllByText('关闭').length).toBeGreaterThan(0)
    expect(screen.getAllByText('旧版综合排序').length).toBeGreaterThan(0)
    expect(screen.getByText('暂时没有可保存结果。')).toBeVisible()
  })

  it('推荐完成任务展示评分、指标，应用按钮可保存最佳结果', async () => {
    const user = userEvent.setup()
    const task = makeRecommendedTask()
    const { tuning } = renderTuning({ tuningTasks: [task] })

    expect(screen.getByText('验证通过，建议应用。')).toBeInTheDocument()
    expect(screen.getByText('0.500 → 0.580 · Δ +0.080')).toBeInTheDocument()
    expect(screen.getByText('通过，6 个样本')).toBeInTheDocument()
    expect(screen.getByText('首条命中率')).toBeInTheDocument()
    expect(screen.getByText('目标找回率')).toBeInTheDocument()
    expect(screen.getByText('无结果比例')).toBeInTheDocument()
    expect(screen.getByText('平均响应时间')).toBeInTheDocument()
    expect(screen.getByText('首条命中率').closest('.grid')).toHaveTextContent('60.0%')
    expect(screen.getByText('首条命中率').closest('.grid')).toHaveTextContent('Δ +10.0%')
    expect(screen.getByText('平均响应时间').closest('.grid')).toHaveTextContent('80 ms')
    expect(screen.getByText('平均响应时间').closest('.grid')).toHaveTextContent('Δ +14 ms')
    expect(screen.getByText(formatImportTime(1_710_000_100))).toBeInTheDocument()
    expect(screen.getByText('tune-1')).toBeInTheDocument()

    const applyButton = screen.getByRole('button', { name: '应用推荐结果' })
    expect(applyButton).toBeEnabled()
    await user.click(applyButton)
    expect(tuning.applyBestTask).toHaveBeenCalledWith('tune-1')
  })

  it('运行中任务展示进度，不展示综合评分', () => {
    renderTuning({
      tuningTasks: [
        makeTask({
          task_id: 'tune-running',
          status: 'running',
          progress: 45.4,
          rounds_done: 9,
          rounds_total: 20,
          recommended: false,
        }),
      ],
    })

    expect(screen.getByText('任务状态')).toBeInTheDocument()
    expect(screen.getAllByText('运行中').length).toBeGreaterThan(0)
    expect(screen.getByText('任务进度')).toBeInTheDocument()
    expect(screen.getByText('45%')).toBeInTheDocument()
    expect(screen.getByText('已尝试次数')).toBeInTheDocument()
    expect(screen.getByText('9/20')).toBeInTheDocument()
    expect(screen.queryByText('综合评分')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '应用推荐结果' })).toBeDisabled()
  })

  it('失败任务把错误原因展示为可读文案', () => {
    renderTuning({
      tuningTasks: [
        makeTask({
          task_id: 'tune-failed',
          status: 'failed',
          progress: '20',
          rounds_done: 4,
          rounds_total: 20,
          recommended: false,
          error: 'mock failure',
        }),
      ],
    })

    expect(screen.getAllByText('失败').length).toBeGreaterThan(0)
    expect(screen.getByText('失败原因：mock failure')).toBeInTheDocument()
    expect(screen.getByText('20%')).toBeInTheDocument()
  })

  it('验证未通过时展示本地化原因并禁用应用', async () => {
    const user = userEvent.setup()
    const { tuning } = renderTuning({
      tuningTasks: [
        makeTask({
          task_id: 'tune-bad',
          recommended: false,
          best_score: 0.49,
          validation_summary: {
            recommended: false,
            reason: 'holdout_online_like_validation_failed',
            holdout_case_count: 5,
            deltas: {
              score: -0.01,
              precision_at_1: -0.02,
              recall_at_k: -0.01,
              empty_rate: 0.06,
              avg_elapsed_ms: 120,
            },
            online_like: {
              baseline: {
                score: 0.5,
                metrics: {
                  precision_at_1: 0.6,
                  recall_at_k: 0.5,
                  empty_rate: 0.08,
                  avg_elapsed_ms: 80,
                },
              },
              best: {
                score: 0.49,
                metrics: {
                  precision_at_1: 0.58,
                  recall_at_k: 0.49,
                  empty_rate: 0.14,
                  avg_elapsed_ms: 200,
                },
              },
            },
          },
        }),
      ],
    })

    expect(screen.getByText('验证未通过，不建议应用。')).toBeInTheDocument()
    expect(screen.getByText('0.500 → 0.490 · Δ -0.010')).toBeInTheDocument()
    expect(screen.getByText('未通过，5 个样本')).toBeInTheDocument()
    expect(screen.getByText('原因：独立样本和实际搜索效果验证未通过。')).toBeInTheDocument()
    expect(screen.getByText('无结果比例').closest('.grid')).toHaveTextContent('Δ +6.0%')

    const applyButton = screen.getByRole('button', { name: '应用推荐结果' })
    expect(applyButton).toBeDisabled()
    await user.click(applyButton)
    expect(tuning.applyBestTask).not.toHaveBeenCalled()
  })

  it('独立样本不足与未知失败原因分别本地化', () => {
    const { rerender } = renderTuning({
      tuningTasks: [
        makeTask({
          task_id: 'tune-empty',
          recommended: false,
          validation_summary: {
            recommended: false,
            reason: 'holdout_empty',
            online_like: {
              baseline: { score: 0.4 },
              best: { score: 0.4 },
            },
          },
        }),
      ],
    })

    expect(screen.getByText('未通过')).toBeInTheDocument()
    expect(screen.getByText('原因：独立样本不足，无法验证调优结果。')).toBeInTheDocument()

    rerender(
      <Tabs defaultValue="tuning">
        <TuningTab
          tuning={makeTuning({
            tuningTasks: [
              makeTask({
                task_id: 'tune-unknown',
                status: 'failed',
                error: '  backend timeout  ',
              }),
            ],
          })}
        />
      </Tabs>,
    )
    expect(screen.getByText('失败原因：backend timeout')).toBeInTheDocument()
  })

  it('空白失败原因不渲染错误条，缺字段进度展示短横线', () => {
    renderTuning({
      tuningTasks: [
        makeTask({
          task_id: 'tune-queued',
          status: 'queued',
          progress: 'not-a-number',
          rounds_done: undefined,
          rounds_total: 8,
          error: '   ',
        }),
      ],
    })

    expect(screen.getAllByText('排队中').length).toBeGreaterThan(0)
    expect(screen.getAllByText('-').length).toBeGreaterThan(0)
    expect(screen.queryByText(/失败原因/)).not.toBeInTheDocument()
  })

  it('无 task_id、未完成或不推荐时禁用应用；validation 推荐可覆盖', async () => {
    const user = userEvent.setup()
    const { tuning, rerender } = renderTuning({
      tuningTasks: [
        makeTask({ task_id: '', status: 'completed', recommended: true }),
        makeTask({ task_id: 'tune-cancelled', status: 'cancelled', recommended: true }),
        makeTask({ task_id: 'tune-plain', status: 'completed', recommended: false }),
      ],
    })

    const applyButtons = screen.getAllByRole('button', { name: '应用推荐结果' })
    expect(applyButtons).toHaveLength(3)
    applyButtons.forEach((button) => expect(button).toBeDisabled())
    expect(screen.getByText('-')).toBeInTheDocument()
    expect(screen.getAllByText('已取消').length).toBeGreaterThan(0)
    expect(screen.getAllByText('不建议应用').length).toBeGreaterThan(0)
    await user.click(applyButtons[0])
    expect(tuning.applyBestTask).not.toHaveBeenCalled()

    const applyBestTask = vi.fn(async () => {})
    rerender(
      <Tabs defaultValue="tuning">
        <TuningTab
          tuning={makeTuning({
            applyBestTask,
            tuningTasks: [
              makeTask({
                task_id: 'tune-3',
                recommended: false,
                validation_summary: { recommended: true },
              }),
            ],
          })}
        />
      </Tabs>,
    )
    const enabledApply = screen.getByRole('button', { name: '应用推荐结果' })
    expect(enabledApply).toBeEnabled()
    await user.click(enabledApply)
    expect(applyBestTask).toHaveBeenCalledWith('tune-3')
  })

  it('结果区优先展示已完成任务，评分可从 best_score 与 baseline 推算', () => {
    renderTuning({
      tuningTasks: [
        makeTask({
          task_id: 'tune-running',
          status: 'running',
          progress: 10,
          rounds_done: 1,
          rounds_total: 8,
        }),
        makeTask({
          task_id: 'tune-done',
          status: 'completed',
          recommended: true,
          best_score: 0.58,
          validation_summary: {
            recommended: true,
            online_like: {},
            stable: {
              baseline: { score: 0.5, metrics: { precision_at_1: 0.4 } },
              best: { metrics: { precision_at_1: 0.55 } },
            },
          },
        }),
      ],
    })

    expect(screen.getByText('验证通过，建议应用。')).toBeInTheDocument()
    expect(screen.getByText('0.500 → 0.580 · Δ +0.080')).toBeInTheDocument()
    expect(screen.getByText('通过')).toBeInTheDocument()
    expect(screen.getByText('首条命中率').closest('.grid')).toHaveTextContent('40.0%')
    expect(screen.getByText('首条命中率').closest('.grid')).toHaveTextContent('55.0%')
    expect(screen.getByText('首条命中率').closest('.grid')).toHaveTextContent('Δ +15.0%')
    expect(screen.getAllByText('运行中').length).toBeGreaterThan(0)
    expect(screen.getByText('tune-running')).toBeInTheDocument()
    expect(screen.getByText('tune-done')).toBeInTheDocument()
  })

  it('已完成但没有评估数据时仍走进度视图，未知状态回退原文', () => {
    renderTuning({
      tuningTasks: [
        makeTask({
          task_id: 'tune-bare',
          status: 'completed',
          progress: undefined,
          rounds_done: undefined,
          rounds_total: undefined,
          recommended: false,
        }),
        makeTask({
          task_id: 'tune-custom',
          status: 'custom-status',
          created_at: 0,
          updated_at: 0,
        }),
      ],
    })

    expect(screen.getAllByText('已完成').length).toBeGreaterThan(0)
    expect(screen.getByText('任务状态')).toBeInTheDocument()
    expect(screen.queryByText('综合评分')).not.toBeInTheDocument()
    expect(screen.getByText('custom-status')).toBeInTheDocument()
    expect(screen.getAllByText('-').length).toBeGreaterThan(0)
  })

  it('仅有 delta 的指标也会渲染，缺分数时综合评分显示短横线', () => {
    renderTuning({
      tuningTasks: [
        makeTask({
          task_id: 'tune-delta-only',
          recommended: true,
          validation_summary: {
            recommended: true,
            deltas: { precision_at_1: 0.05, avg_elapsed_ms: -8 },
          },
        }),
      ],
    })

    expect(screen.getByText('综合评分').parentElement).toHaveTextContent('-')
    expect(screen.getByText('首条命中率').closest('.grid')).toHaveTextContent('Δ +5.0%')
    expect(screen.getByText('平均响应时间').closest('.grid')).toHaveTextContent('Δ -8 ms')
  })
})
