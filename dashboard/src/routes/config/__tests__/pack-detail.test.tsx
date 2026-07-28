/**
 * PackDetailPage 特征化测试
 *
 * Pack 详情页：加载骨架 / 加载失败空态 / 详情与三个标签页渲染 / 点赞 /
 * 应用向导（冲突检测、三步流程、API Key 校验、应用成功与失败）。
 * pack-api 与路由全量打桩；页面状态由 useState/useEffect 真实驱动。
 */
import type {
  ApplyPackConflicts,
  ModelPack,
} from '@/lib/pack-api'

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import PackDetailPage from '../pack-detail'
import * as packApi from '@/lib/pack-api'

const { navigateMock, toastMock } = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  toastMock: vi.fn(),
}))

// 页面直接 import { toast }，同时兜住 useToast 形态
vi.mock('@/hooks/use-toast', () => ({
  toast: (...args: unknown[]) => toastMock(...args),
  useToast: () => ({ toast: toastMock }),
}))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigateMock }))
vi.mock('@/router', () => ({
  packDetailRoute: { useParams: () => ({ packId: 'pack-1' }) },
}))
vi.mock('@/lib/pack-api', () => ({
  applyPack: vi.fn(),
  checkPackLike: vi.fn(),
  detectPackConflicts: vi.fn(),
  getPack: vi.fn(),
  getPackUserId: vi.fn(),
  recordPackDownload: vi.fn(),
  togglePackLike: vi.fn(),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

/** 构造一个完整 Pack */
function makePack(overrides: Partial<ModelPack> = {}): ModelPack {
  return {
    id: 'pack-1',
    name: '模板甲',
    description: '一套整合配置',
    author: '作者乙',
    version: '1.2.0',
    created_at: '2026-01-02T12:00:00Z',
    updated_at: '2026-01-02T12:00:00Z',
    status: 'approved',
    downloads: 10,
    likes: 3,
    tags: ['openai'],
    providers: [{ name: 'prov-a', base_url: 'https://api.a.com/v1', client_type: 'openai' }],
    models: [
      {
        model_identifier: 'gpt-x',
        name: 'model-a',
        api_provider: 'prov-a',
        price_in: 1,
        price_out: 2,
      },
    ],
    task_config: { replyer: { model_list: ['model-a'], temperature: 0.7 } },
    ...overrides,
  }
}

/** 构造冲突检测结果：一个已存在提供商 + 一个需要 API Key 的新提供商 */
function makeConflicts(overrides: Partial<ApplyPackConflicts> = {}): ApplyPackConflicts {
  return {
    existing_providers: [
      {
        pack_provider: { name: 'prov-a', base_url: 'https://api.a.com/v1', client_type: 'openai' },
        local_providers: [{ name: 'local-a', base_url: 'https://api.a.com/v1' }],
      },
    ],
    new_providers: [{ name: 'prov-new', base_url: 'https://api.new.com', client_type: 'openai' }],
    conflicting_models: [],
    ...overrides,
  }
}

beforeEach(() => {
  vi.mocked(packApi.getPack).mockResolvedValue(makePack())
  vi.mocked(packApi.checkPackLike).mockResolvedValue(false)
  vi.mocked(packApi.getPackUserId).mockReturnValue('user-1')
  vi.mocked(packApi.togglePackLike).mockResolvedValue({ liked: true, likes: 4 })
  vi.mocked(packApi.detectPackConflicts).mockResolvedValue(makeConflicts())
  vi.mocked(packApi.applyPack).mockResolvedValue(undefined as never)
  vi.mocked(packApi.recordPackDownload).mockResolvedValue(undefined as never)
})

async function renderReady() {
  render(<PackDetailPage />)
  await waitFor(() => {
    expect(screen.getByText('模板甲')).toBeInTheDocument()
  })
}

/** 打开应用向导并等待冲突检测完成（步骤 1 复选框出现） */
async function openWizard() {
  fireEvent.click(screen.getByRole('button', { name: '应用模板' }))
  await waitFor(() => {
    expect(screen.getByText(/应用提供商配置 \(1 个\)/)).toBeInTheDocument()
  })
  return screen.getByRole('dialog')
}

/** 向导内点击「下一步」 */
function nextStep(dialog: HTMLElement) {
  fireEvent.click(within(dialog).getByRole('button', { name: '下一步' }))
}

describe('PackDetailPage 加载与空态', () => {
  it('请求未返回时渲染骨架屏', () => {
    vi.mocked(packApi.getPack).mockReturnValue(new Promise(() => {}))
    render(<PackDetailPage />)

    expect(document.querySelector('.animate-pulse')).not.toBeNull()
    expect(packApi.getPack).toHaveBeenCalledWith('pack-1')
    expect(screen.queryByText('模板甲')).not.toBeInTheDocument()
  })

  it('加载失败时提示并展示「模板不存在」，返回按钮跳转市场', async () => {
    vi.mocked(packApi.getPack).mockRejectedValue(new Error('网络错误'))
    render(<PackDetailPage />)

    await waitFor(() => {
      expect(screen.getByText('模板不存在')).toBeInTheDocument()
    })
    expect(toastMock).toHaveBeenCalledWith({ title: '加载模板失败', variant: 'destructive' })

    fireEvent.click(screen.getByRole('button', { name: '返回市场' }))
    expect(navigateMock).toHaveBeenCalledWith({ to: '/config/pack-market' })
  })
})

describe('PackDetailPage 详情渲染', () => {
  it('渲染头部信息、统计卡与提供商表格', async () => {
    await renderReady()

    expect(screen.getByText('v1.2.0')).toBeInTheDocument()
    expect(screen.getByText('一套整合配置')).toBeInTheDocument()
    expect(screen.getByText('作者乙')).toBeInTheDocument()
    expect(screen.getByText('10 次下载')).toBeInTheDocument()
    expect(screen.getByText('3 赞')).toBeInTheDocument()
    // openai 出现两处：标签徽标 + 提供商表格 client_type 徽标
    expect(screen.getAllByText('openai')).toHaveLength(2)
    // 统计卡标签（「API 提供商」同时是默认标签页卡片标题，共两处）
    expect(screen.getAllByText('API 提供商')).toHaveLength(2)
    expect(screen.getByText('模型配置')).toBeInTheDocument()
    // 默认提供商标签页：表格行
    expect(screen.getByText('prov-a')).toBeInTheDocument()
    expect(screen.getByText('https://api.a.com/v1')).toBeInTheDocument()
  })

  it('切换到模型与任务配置标签页展示对应内容', async () => {
    // Radix Tabs 在 mousedown 阶段激活，需用 userEvent 触发完整指针事件序列
    const user = userEvent.setup()
    await renderReady()

    // 模型标签页
    await user.click(screen.getByRole('tab', { name: /^模型/ }))
    expect(screen.getByText('model-a')).toBeInTheDocument()
    expect(screen.getByText('gpt-x')).toBeInTheDocument()
    expect(screen.getByText('¥1 / ¥2')).toBeInTheDocument()

    // 任务配置标签页：任务名映射 + 展开后展示模型与 temperature
    await user.click(screen.getByRole('tab', { name: /任务/ }))
    const trigger = screen.getByRole('button', { name: /回复生成/ })
    expect(within(trigger).getByText('1 个模型')).toBeInTheDocument()
    await user.click(trigger)
    expect(screen.getByText('model-a')).toBeInTheDocument()
    expect(screen.getByText('0.7')).toBeInTheDocument()
  })

  it('点赞成功后按钮变为已点赞并更新点赞数', async () => {
    await renderReady()

    fireEvent.click(screen.getByRole('button', { name: '点赞' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '已点赞' })).toBeInTheDocument()
    })
    expect(packApi.togglePackLike).toHaveBeenCalledWith('pack-1', 'user-1')
    expect(screen.getByText('4 赞')).toBeInTheDocument()
  })

  it('点赞失败时给出错误提示', async () => {
    vi.mocked(packApi.togglePackLike).mockRejectedValue(new Error('boom'))
    await renderReady()

    fireEvent.click(screen.getByRole('button', { name: '点赞' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({ title: '点赞失败', variant: 'destructive' })
    })
    expect(screen.getByText('3 赞')).toBeInTheDocument()
  })
})

describe('PackDetailPage 应用向导', () => {
  it('打开向导触发冲突检测，步骤 2 展示已匹配与待补 Key 的提供商', async () => {
    await renderReady()
    const dialog = await openWizard()

    expect(packApi.detectPackConflicts).toHaveBeenCalledWith(makePack())
    // 步骤 1：默认选项（任务模式追加）
    expect(within(dialog).getByText('任务配置应用模式')).toBeInTheDocument()

    nextStep(dialog)
    // 步骤 2：单个 URL 匹配的提供商直接映射
    expect(within(dialog).getByText('发现已有的提供商')).toBeInTheDocument()
    expect(within(dialog).getByText('local-a')).toBeInTheDocument()
    expect(within(dialog).getByText('URL 匹配')).toBeInTheDocument()
    // 新提供商需要 API Key
    expect(within(dialog).getByText('需要配置 API Key')).toBeInTheDocument()
    expect(within(dialog).getByPlaceholderText('输入 prov-new 的 API Key')).toBeInTheDocument()
  })

  it('新提供商未填 API Key 时应用被拦截', async () => {
    await renderReady()
    const dialog = await openWizard()

    nextStep(dialog)
    nextStep(dialog)
    // 步骤 3：确认页
    expect(within(dialog).getByText('确认应用')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: '应用模板' }))

    expect(toastMock).toHaveBeenCalledWith({
      title: '请填写提供商 "prov-new" 的 API Key',
      variant: 'destructive',
    })
    expect(packApi.applyPack).not.toHaveBeenCalled()
  })

  it('填写 API Key 后完成应用：记录下载、更新下载数并关闭对话框', async () => {
    await renderReady()
    const dialog = await openWizard()

    nextStep(dialog)
    fireEvent.change(within(dialog).getByPlaceholderText('输入 prov-new 的 API Key'), {
      target: { value: 'sk-1' },
    })
    nextStep(dialog)
    // 步骤 3 汇总文案
    expect(within(dialog).getByText('应用 1 个提供商配置')).toBeInTheDocument()
    expect(within(dialog).getByText('应用 1 个模型配置')).toBeInTheDocument()
    expect(within(dialog).getByText(/追加 1 个任务配置/)).toBeInTheDocument()

    fireEvent.click(within(dialog).getByRole('button', { name: '应用模板' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({ title: '配置模板应用成功！' })
    })
    expect(packApi.applyPack).toHaveBeenCalledWith(
      makePack(),
      {
        apply_providers: true,
        apply_models: true,
        apply_task_config: true,
        task_mode: 'append',
        selected_providers: undefined,
        selected_models: undefined,
        selected_tasks: undefined,
      },
      { 'prov-a': 'local-a' },
      { 'prov-new': 'sk-1' }
    )
    expect(packApi.recordPackDownload).toHaveBeenCalledWith('pack-1', 'user-1')
    // 下载数 +1，对话框关闭
    expect(screen.getByText('11 次下载')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
  })

  it('步骤 1 取消应用任务配置后隐藏任务模式选项', async () => {
    await renderReady()
    const dialog = await openWizard()

    fireEvent.click(within(dialog).getByRole('checkbox', { name: /应用任务配置/ }))
    expect(within(dialog).queryByText('任务配置应用模式')).not.toBeInTheDocument()

    // 切换任务模式为替换：重新勾选后点替换模式单选
    fireEvent.click(within(dialog).getByRole('checkbox', { name: /应用任务配置/ }))
    fireEvent.click(within(dialog).getByRole('radio', { name: /替换模式/ }))
    expect(within(dialog).getByRole('radio', { name: /替换模式/ })).toHaveAttribute(
      'aria-checked',
      'true'
    )
  })

  it('冲突检测失败时提示并关闭对话框', async () => {
    vi.mocked(packApi.detectPackConflicts).mockRejectedValue(new Error('boom'))
    await renderReady()

    fireEvent.click(screen.getByRole('button', { name: '应用模板' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '检测配置冲突失败',
        variant: 'destructive',
      })
    })
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
  })
})
