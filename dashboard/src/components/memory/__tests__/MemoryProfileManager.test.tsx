import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MemoryProfileManager } from '../MemoryProfileManager'
import * as memoryApi from '@/lib/memory-api'
import type {
  MemoryProfileEvidencePayload,
  MemoryProfileItemPayload,
} from '@/lib/memory-api'

// toast 桩：用 hoisted 保证 vi.mock 工厂内能引用同一个实例
const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

// 组件消费的人物画像 API 全部打桩，避免真实请求
vi.mock('@/lib/memory-api', () => ({
  correctMemoryProfileEvidence: vi.fn(),
  deleteMemoryProfileOverride: vi.fn(),
  getMemoryProfileEvidence: vi.fn(),
  getMemoryProfiles: vi.fn(),
  queryMemoryProfile: vi.fn(),
  searchMemoryProfiles: vi.fn(),
  setMemoryProfileOverride: vi.fn(),
}))

/** 构造一条画像库条目 */
function makeProfile(overrides: Partial<MemoryProfileItemPayload> = {}): MemoryProfileItemPayload {
  return {
    person_id: 'p1',
    person_name: '张三',
    profile_version: 3,
    profile_text: '张三的画像',
    has_manual_override: true,
    manual_override: { override_text: '人工画像文本' },
    ...overrides,
  }
}

/** 构造画像证据载荷，person_id 跟随请求方便断言展示归属 */
function makeEvidencePayload(personId: string): MemoryProfileEvidencePayload {
  return {
    success: true,
    person_id: personId,
    profile_text: '证据画像文本',
    auto_profile_text: '自动画像文本',
    has_manual_override: true,
    evidence_count: 2,
    evidence: [
      {
        evidence_key: 'ek1',
        evidence_type: 'relation',
        hash: 'h1',
        content: '关系证据内容',
        source: '来源A',
        confidence: 0.87,
        deletable: true,
      },
      {
        evidence_type: 'person_fact',
        hash: 'h2',
        content: '事实证据内容',
        score: 1.5,
        deletable: false,
        not_deletable_reason: '系统内置',
      },
    ],
  }
}

beforeEach(() => {
  vi.mocked(memoryApi.getMemoryProfiles).mockResolvedValue({
    success: true,
    items: [makeProfile(), makeProfile({ person_id: 'p2', person_name: '李四', profile_version: 1, profile_text: '李四的画像', has_manual_override: false, manual_override: null })],
  })
  vi.mocked(memoryApi.getMemoryProfileEvidence).mockImplementation(async ({ personId }) =>
    makeEvidencePayload(personId),
  )
  vi.mocked(memoryApi.searchMemoryProfiles).mockResolvedValue({
    success: true,
    items: [makeProfile({ person_id: 'p9', person_name: '王五', profile_text: '王五的画像', has_manual_override: false, manual_override: null })],
  })
  vi.mocked(memoryApi.queryMemoryProfile).mockResolvedValue({
    success: true,
    person_id: 'p9',
    profile_text: '查询得到的画像',
  })
  vi.mocked(memoryApi.setMemoryProfileOverride).mockResolvedValue({ success: true })
  vi.mocked(memoryApi.deleteMemoryProfileOverride).mockResolvedValue({ success: true, deleted: true })
  vi.mocked(memoryApi.correctMemoryProfileEvidence).mockResolvedValue({
    success: true,
    operation_id: 'op-1',
    refreshed_evidence: { ...makeEvidencePayload('p1'), evidence: [], evidence_count: 0 },
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

/** 渲染组件并等待首次画像库加载完成 */
async function renderManager(initialPersonId?: string) {
  render(<MemoryProfileManager initialPersonId={initialPersonId} />)
  await waitFor(() => {
    expect(memoryApi.getMemoryProfiles).toHaveBeenCalled()
  })
}

describe('MemoryProfileManager 画像库加载', () => {
  it('挂载时加载画像库并自动选中第一个人物，同时拉取其证据', async () => {
    await renderManager()
    expect(memoryApi.getMemoryProfiles).toHaveBeenCalledWith(80)
    expect(await screen.findByText('张三')).toBeInTheDocument()
    expect(screen.getByText('李四')).toBeInTheDocument()
    // 有画像覆写的行展示徽章（限定在张三所在表格行内查询）
    const row = screen.getByText('张三').closest('tr')
    expect(row).not.toBeNull()
    expect(within(row as HTMLTableRowElement).getByText('画像覆写')).toBeInTheDocument()
    // 自动选中 p1 后按默认证据数量 12 拉取证据
    await waitFor(() => {
      expect(memoryApi.getMemoryProfileEvidence).toHaveBeenCalledWith({
        personId: 'p1',
        limit: 12,
        forceRefresh: false,
      })
    })
    // 画像覆写编辑框回填 manual_override 的 override_text
    expect(await screen.findByDisplayValue('人工画像文本')).toBeInTheDocument()
    expect(screen.getByText('当前编辑对象：张三')).toBeInTheDocument()
  })

  it('画像库加载失败时弹出错误 toast 并显示空态', async () => {
    vi.mocked(memoryApi.getMemoryProfiles).mockRejectedValue(new Error('服务不可用'))
    await renderManager()
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '加载人物画像失败',
          description: '服务不可用',
          variant: 'destructive',
        }),
      )
    })
    expect(screen.getByText('还没有人物画像快照')).toBeInTheDocument()
  })

  it('点击列表另一行切换选中：证据重新拉取且覆写编辑框重置', async () => {
    await renderManager()
    await screen.findByDisplayValue('人工画像文本')

    fireEvent.click(screen.getByText('李四'))
    await waitFor(() => {
      expect(memoryApi.getMemoryProfileEvidence).toHaveBeenLastCalledWith({
        personId: 'p2',
        limit: 12,
        forceRefresh: false,
      })
    })
    // p2 没有 manual_override，编辑框清空
    expect(screen.queryByDisplayValue('人工画像文本')).not.toBeInTheDocument()
  })
})

describe('MemoryProfileManager 查询流程', () => {
  it('没有任何查询条件时提交只弹提示，不发起请求', async () => {
    await renderManager()
    fireEvent.click(screen.getByRole('button', { name: /查询人物画像/ }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '请输入查询条件', variant: 'destructive' }),
      )
    })
    expect(memoryApi.searchMemoryProfiles).not.toHaveBeenCalled()
    expect(memoryApi.queryMemoryProfile).not.toHaveBeenCalled()
  })

  it('仅填关键词时走画像检索：更新列表并切换到检索结果模式', async () => {
    await renderManager()
    fireEvent.change(screen.getByLabelText('人物关键词'), { target: { value: ' 王五 ' } })
    fireEvent.click(screen.getByRole('button', { name: /查询人物画像/ }))

    await waitFor(() => {
      expect(memoryApi.searchMemoryProfiles).toHaveBeenCalledWith({
        personKeyword: '王五',
        limit: 80,
      })
    })
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '人物画像检索完成',
          description: '命中 1 个画像。',
        }),
      )
    })
    expect(screen.getByText('检索结果')).toBeInTheDocument()
    expect(await screen.findByText('王五')).toBeInTheDocument()
    // 原画像库条目被替换
    expect(screen.queryByText('张三')).not.toBeInTheDocument()
  })

  it('平台加账号定位时先查询画像再检索列表并拉取证据', async () => {
    await renderManager()
    fireEvent.change(screen.getByLabelText('平台'), { target: { value: 'qq' } })
    fireEvent.change(screen.getByLabelText('用户账号'), { target: { value: '10086' } })
    fireEvent.click(screen.getByRole('button', { name: /查询人物画像/ }))

    await waitFor(() => {
      expect(memoryApi.queryMemoryProfile).toHaveBeenCalledWith({
        personId: '',
        personKeyword: '',
        platform: 'qq',
        userId: '10086',
        limit: 12,
        forceRefresh: false,
      })
    })
    await waitFor(() => {
      expect(memoryApi.searchMemoryProfiles).toHaveBeenCalledWith({
        personId: 'p9',
        personKeyword: '',
        platform: 'qq',
        userId: '10086',
        limit: 80,
      })
    })
    // 查询命中的 person_id 用于拉取证据
    await waitFor(() => {
      expect(memoryApi.getMemoryProfileEvidence).toHaveBeenLastCalledWith({
        personId: 'p9',
        limit: 12,
        forceRefresh: false,
      })
    })
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '人物画像查询完成', description: '已获取画像结果。' }),
      )
    })
  })

  it('查询返回 success=false 时弹出失败 toast', async () => {
    vi.mocked(memoryApi.queryMemoryProfile).mockResolvedValue({
      success: false,
      error: '人物不存在',
    })
    await renderManager()
    fireEvent.change(screen.getByLabelText('平台'), { target: { value: 'qq' } })
    fireEvent.change(screen.getByLabelText('用户账号'), { target: { value: '10086' } })
    fireEvent.click(screen.getByRole('button', { name: /查询人物画像/ }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '人物画像查询失败',
          description: '人物不存在',
          variant: 'destructive',
        }),
      )
    })
  })

  it('initialPersonId 会展开高级入口并直接定位画像', async () => {
    // 画像库置空避免默认选中干扰初始定位
    vi.mocked(memoryApi.getMemoryProfiles).mockResolvedValue({ success: true, items: [] })
    await renderManager('p-init')

    expect(screen.getByLabelText('person_id')).toHaveValue('p-init')
    await waitFor(() => {
      expect(memoryApi.queryMemoryProfile).toHaveBeenCalledWith({
        personId: 'p-init',
        personKeyword: '',
        platform: '',
        userId: '',
        limit: 12,
        forceRefresh: false,
      })
    })
    await waitFor(() => {
      expect(memoryApi.searchMemoryProfiles).toHaveBeenCalledWith({
        personId: 'p-init',
        limit: 80,
      })
    })
    await waitFor(() => {
      expect(memoryApi.getMemoryProfileEvidence).toHaveBeenCalledWith({
        personId: 'p-init',
        limit: 12,
        forceRefresh: false,
      })
    })
  })
})

describe('MemoryProfileManager 证据展示与纠错', () => {
  it('渲染证据行：类型徽章、置信度/分数与不可删除原因', async () => {
    await renderManager()
    expect(await screen.findByText('关系证据内容')).toBeInTheDocument()
    expect(screen.getByText('关系')).toBeInTheDocument()
    expect(screen.getByText('人物事实')).toBeInTheDocument()
    expect(screen.getByText('置信度 0.87')).toBeInTheDocument()
    expect(screen.getByText('分数 1.50')).toBeInTheDocument()
    expect(screen.getByText(/2 条证据/)).toBeInTheDocument()
    // 可删除项渲染纠错按钮，不可删除项展示原因
    expect(screen.getByRole('button', { name: /纠错并刷新/ })).toBeInTheDocument()
    expect(screen.getByText('系统内置')).toBeInTheDocument()
  })

  it('存在画像覆写时可在覆写与自动画像之间切换展示', async () => {
    await renderManager()
    // 默认展示证据里的画像文本
    expect(await screen.findByDisplayValue('证据画像文本')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '自动画像' }))
    expect(screen.getByDisplayValue('自动画像文本')).toBeInTheDocument()

    // 精确的可访问名匹配不会命中「保存画像覆写」「删除画像覆写」
    fireEvent.click(screen.getByRole('button', { name: '画像覆写' }))
    expect(screen.getByDisplayValue('证据画像文本')).toBeInTheDocument()
  })

  it('纠错证据：取消 confirm 不调用，确认后调用并应用刷新结果', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await renderManager()
    const correctButton = await screen.findByRole('button', { name: /纠错并刷新/ })

    fireEvent.click(correctButton)
    expect(confirmSpy).toHaveBeenCalledWith('确认停用/删除这条支撑证据并刷新画像？')
    expect(memoryApi.correctMemoryProfileEvidence).not.toHaveBeenCalled()

    confirmSpy.mockReturnValue(true)
    fireEvent.click(correctButton)
    await waitFor(() => {
      expect(memoryApi.correctMemoryProfileEvidence).toHaveBeenCalledWith({
        person_id: 'p1',
        evidence_type: 'relation',
        hash: 'h1',
        requested_by: 'knowledge_base',
        reason: 'profile_evidence_correction',
        refresh: true,
        limit: 12,
      })
    })
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '画像证据已纠错', description: '删除记录 op-1' }),
      )
    })
    // 应用 refreshed_evidence（证据清空）后展示空态
    expect(await screen.findByText('当前没有可展示的支撑证据')).toBeInTheDocument()
    // 纠错成功后重新加载画像库
    expect(memoryApi.getMemoryProfiles).toHaveBeenCalledTimes(2)
  })

  it('纠错接口返回 success=false 时弹出失败 toast', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.mocked(memoryApi.correctMemoryProfileEvidence).mockResolvedValue({
      success: false,
      error: '证据已被移除',
    })
    await renderManager()
    fireEvent.click(await screen.findByRole('button', { name: /纠错并刷新/ }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '画像证据纠错失败',
          description: '证据已被移除',
          variant: 'destructive',
        }),
      )
    })
  })
})

describe('MemoryProfileManager 画像覆写', () => {
  it('未选中任何人物时保存只弹提示，不调用接口', async () => {
    vi.mocked(memoryApi.getMemoryProfiles).mockResolvedValue({ success: true, items: [] })
    await renderManager()
    expect(screen.getByText('选择一个人物或执行查询后查看详情。')).toBeInTheDocument()
    expect(screen.getByText('请选择或输入 person_id 后再编辑画像覆写。')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /保存画像覆写/ }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '缺少人物 ID', variant: 'destructive' }),
      )
    })
    expect(memoryApi.setMemoryProfileOverride).not.toHaveBeenCalled()
    // 删除按钮在无人物时禁用
    expect(screen.getByRole('button', { name: /删除画像覆写/ })).toBeDisabled()
  })

  it('保存覆写：以固定的 updated_by/source 提交并刷新画像库与证据', async () => {
    await renderManager()
    const overrideInput = await screen.findByDisplayValue('人工画像文本')
    fireEvent.change(overrideInput, { target: { value: '新的人工画像' } })
    fireEvent.click(screen.getByRole('button', { name: /保存画像覆写/ }))

    await waitFor(() => {
      expect(memoryApi.setMemoryProfileOverride).toHaveBeenCalledWith({
        person_id: 'p1',
        override_text: '新的人工画像',
        updated_by: 'knowledge_base',
        source: 'webui',
      })
    })
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({ title: '人物画像覆写已保存' })
    })
    // 保存成功后重新加载画像库
    expect(memoryApi.getMemoryProfiles).toHaveBeenCalledTimes(2)
  })

  it('保存覆写失败时弹出错误 toast', async () => {
    vi.mocked(memoryApi.setMemoryProfileOverride).mockRejectedValue(new Error('写入被拒绝'))
    await renderManager()
    await screen.findByDisplayValue('人工画像文本')
    fireEvent.click(screen.getByRole('button', { name: /保存画像覆写/ }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '保存人物画像覆写失败',
          description: '写入被拒绝',
          variant: 'destructive',
        }),
      )
    })
  })

  it('删除覆写：取消 confirm 不调用，确认后删除并清空编辑框', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await renderManager()
    await screen.findByDisplayValue('人工画像文本')

    fireEvent.click(screen.getByRole('button', { name: /删除画像覆写/ }))
    expect(confirmSpy).toHaveBeenCalledWith('确认删除 p1 的人物画像覆写？')
    expect(memoryApi.deleteMemoryProfileOverride).not.toHaveBeenCalled()

    confirmSpy.mockReturnValue(true)
    fireEvent.click(screen.getByRole('button', { name: /删除画像覆写/ }))
    await waitFor(() => {
      expect(memoryApi.deleteMemoryProfileOverride).toHaveBeenCalledWith('p1')
    })
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({ title: '人物画像覆写已删除' })
    })
  })
})
