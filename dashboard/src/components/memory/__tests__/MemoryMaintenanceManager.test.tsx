import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MemoryMaintenanceManager } from '../MemoryMaintenanceManager'
import * as memoryApi from '@/lib/memory-api'
import type { MemoryMaintenanceItemPayload } from '@/lib/memory-api'

// toast 桩：用 hoisted 保证 vi.mock 工厂内能引用同一个实例
const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

// 组件消费的记忆维护 API 全部打桩，避免真实请求
vi.mock('@/lib/memory-api', () => ({
  freezeMemory: vi.fn(),
  getMemoryRecycleBin: vi.fn(),
  protectMemory: vi.fn(),
  reinforceMemory: vi.fn(),
  restoreMaintainedMemory: vi.fn(),
}))

/** 构造一条回收站关系数据 */
function makeItem(overrides: Partial<MemoryMaintenanceItemPayload> = {}): MemoryMaintenanceItemPayload {
  return {
    hash: 'hash-a',
    subject: '张三',
    predicate: '喜欢',
    object: '猫',
    deleted_at: 1_700_000_000,
    source: '聊天记录',
    ...overrides,
  }
}

beforeEach(() => {
  // Radix Select 在 jsdom 下依赖的 pointer-capture API
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {}
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {}
  }

  vi.mocked(memoryApi.getMemoryRecycleBin).mockResolvedValue({
    success: true,
    items: [makeItem(), makeItem({ hash: 'hash-b', subject: '李四', text: '李四 讨厌 狗', source: undefined })],
  })
  vi.mocked(memoryApi.reinforceMemory).mockResolvedValue({ success: true, detail: '已强化 1 条关系' })
  vi.mocked(memoryApi.freezeMemory).mockResolvedValue({ success: true, detail: '已冻结' })
  vi.mocked(memoryApi.protectMemory).mockResolvedValue({ success: true, detail: '已保护' })
  vi.mocked(memoryApi.restoreMaintainedMemory).mockResolvedValue({ success: true, detail: '已恢复' })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

/** 渲染组件并等待首次回收站加载完成 */
async function renderManager(initialTarget?: string) {
  render(<MemoryMaintenanceManager initialTarget={initialTarget} />)
  await waitFor(() => {
    expect(memoryApi.getMemoryRecycleBin).toHaveBeenCalled()
  })
}

describe('MemoryMaintenanceManager 回收站列表', () => {
  it('挂载时以默认数量 50 加载回收站并渲染关系行', async () => {
    await renderManager()
    expect(memoryApi.getMemoryRecycleBin).toHaveBeenCalledWith(50)
    // 无 text 字段时由主谓宾拼接关系文本
    expect(await screen.findByText('张三 喜欢 猫')).toBeInTheDocument()
    // 有 text 字段时直接展示
    expect(screen.getByText('李四 讨厌 狗')).toBeInTheDocument()
    expect(screen.getByText('hash-a')).toBeInTheDocument()
    expect(screen.getByText('聊天记录')).toBeInTheDocument()
    expect(screen.getByText('已加载 2 条')).toBeInTheDocument()
    expect(screen.getByText('当前命中 2 条')).toBeInTheDocument()
  })

  it('回收站为空时显示占位文案', async () => {
    vi.mocked(memoryApi.getMemoryRecycleBin).mockResolvedValue({ success: true, items: [] })
    await renderManager()
    expect(await screen.findByText('回收站没有可展示的关系')).toBeInTheDocument()
  })

  it('加载失败时弹出错误 toast', async () => {
    vi.mocked(memoryApi.getMemoryRecycleBin).mockRejectedValue(new Error('后端离线'))
    await renderManager()
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '加载记忆回收站失败',
          description: '后端离线',
          variant: 'destructive',
        }),
      )
    })
  })

  it('筛选关键词按 hash/文本过滤行并更新命中数', async () => {
    await renderManager()
    await screen.findByText('张三 喜欢 猫')

    fireEvent.change(screen.getByLabelText('筛选'), { target: { value: '李四' } })
    expect(screen.getByText('当前命中 1 条')).toBeInTheDocument()
    expect(screen.queryByText('张三 喜欢 猫')).not.toBeInTheDocument()
    expect(screen.getByText('李四 讨厌 狗')).toBeInTheDocument()
  })

  it('修改数量后点击刷新，按新数量请求；非法数量回退为 50', async () => {
    await renderManager()
    fireEvent.change(screen.getByLabelText('数量'), { target: { value: '10' } })
    fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    await waitFor(() => {
      expect(memoryApi.getMemoryRecycleBin).toHaveBeenLastCalledWith(10)
    })

    fireEvent.change(screen.getByLabelText('数量'), { target: { value: '-3' } })
    fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    await waitFor(() => {
      expect(memoryApi.getMemoryRecycleBin).toHaveBeenLastCalledWith(50)
    })
  })

  it('initialTarget 会填充维护目标与筛选框', async () => {
    await renderManager('hash-b')
    expect(screen.getByLabelText('维护目标')).toHaveValue('hash-b')
    expect(screen.getByLabelText('筛选')).toHaveValue('hash-b')
  })
})

describe('MemoryMaintenanceManager 维护操作', () => {
  it('目标为空时点击执行只弹出提示，不调用 API', async () => {
    await renderManager()
    fireEvent.click(screen.getByRole('button', { name: /执行强化/ }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '缺少维护目标', variant: 'destructive' }),
      )
    })
    expect(memoryApi.reinforceMemory).not.toHaveBeenCalled()
  })

  it('强化成功：调用 reinforceMemory 并刷新回收站', async () => {
    await renderManager()
    fireEvent.change(screen.getByLabelText('维护目标'), { target: { value: '  hash-a  ' } })
    fireEvent.click(screen.getByRole('button', { name: /执行强化/ }))

    await waitFor(() => {
      expect(memoryApi.reinforceMemory).toHaveBeenCalledWith('hash-a')
    })
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '记忆强化完成',
          description: '已强化 1 条关系',
          variant: 'default',
        }),
      )
    })
    // 操作成功后重新加载回收站
    expect(memoryApi.getMemoryRecycleBin).toHaveBeenCalledTimes(2)
  })

  it('后端返回 success=false 时以失败 toast 呈现', async () => {
    vi.mocked(memoryApi.reinforceMemory).mockResolvedValue({ success: false, error: '未命中关系' })
    await renderManager()
    fireEvent.change(screen.getByLabelText('维护目标'), { target: { value: 'hash-x' } })
    fireEvent.click(screen.getByRole('button', { name: /执行强化/ }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '记忆强化失败',
          description: '未命中关系',
          variant: 'destructive',
        }),
      )
    })
  })

  it('API 抛错时弹出失败 toast', async () => {
    vi.mocked(memoryApi.reinforceMemory).mockRejectedValue(new Error('网络超时'))
    await renderManager()
    fireEvent.change(screen.getByLabelText('维护目标'), { target: { value: 'hash-a' } })
    fireEvent.click(screen.getByRole('button', { name: /执行强化/ }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '记忆强化失败',
          description: '网络超时',
          variant: 'destructive',
        }),
      )
    })
  })

  it('冻结需要 confirm：取消则不调用，确认则调用 freezeMemory', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await renderManager()

    // 通过 Radix Select 切换动作为「冻结」
    await user.click(screen.getByRole('combobox'))
    await user.click(await screen.findByRole('option', { name: '冻结' }))
    fireEvent.change(screen.getByLabelText('维护目标'), { target: { value: 'hash-a' } })

    fireEvent.click(screen.getByRole('button', { name: /执行冻结/ }))
    expect(confirmSpy).toHaveBeenCalledWith('确认冻结命中的记忆关系？冻结后关系会从活跃图谱中移除。')
    expect(memoryApi.freezeMemory).not.toHaveBeenCalled()

    confirmSpy.mockReturnValue(true)
    fireEvent.click(screen.getByRole('button', { name: /执行冻结/ }))
    await waitFor(() => {
      expect(memoryApi.freezeMemory).toHaveBeenCalledWith('hash-a')
    })
  })

  it('保护动作解析保护时长：空值传 undefined，合法数字原样传递', async () => {
    const user = userEvent.setup()
    await renderManager()

    // 保护时长输入框在非 protect 动作下禁用
    expect(screen.getByLabelText('保护时长（小时）')).toBeDisabled()

    await user.click(screen.getByRole('combobox'))
    await user.click(await screen.findByRole('option', { name: '保护' }))
    const hoursInput = screen.getByLabelText('保护时长（小时）')
    expect(hoursInput).toBeEnabled()

    fireEvent.change(screen.getByLabelText('维护目标'), { target: { value: 'hash-a' } })
    fireEvent.click(screen.getByRole('button', { name: /执行保护/ }))
    await waitFor(() => {
      expect(memoryApi.protectMemory).toHaveBeenCalledWith('hash-a', undefined)
    })

    fireEvent.change(hoursInput, { target: { value: '12' } })
    fireEvent.click(screen.getByRole('button', { name: /执行保护/ }))
    await waitFor(() => {
      expect(memoryApi.protectMemory).toHaveBeenLastCalledWith('hash-a', 12)
    })
  })

  it('回收站按行恢复：取消 confirm 不调用，确认后按行 hash 调用', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await renderManager()
    await screen.findByText('张三 喜欢 猫')

    const restoreButtons = screen.getAllByRole('button', { name: '恢复' })
    expect(restoreButtons).toHaveLength(2)

    fireEvent.click(restoreButtons[0])
    expect(confirmSpy).toHaveBeenCalledWith('确认恢复命中的记忆关系？')
    expect(memoryApi.restoreMaintainedMemory).not.toHaveBeenCalled()

    confirmSpy.mockReturnValue(true)
    fireEvent.click(restoreButtons[0])
    await waitFor(() => {
      expect(memoryApi.restoreMaintainedMemory).toHaveBeenCalledWith('hash-a')
    })
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '记忆恢复完成', description: '已恢复' }),
      )
    })
  })
})
