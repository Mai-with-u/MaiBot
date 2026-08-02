/**
 * PersonManagementPage 页面集成测试（特征化）。
 *
 * 页面通过 useDataList（真实 hook）承载分页/搜索/筛选/多选，
 * 测试仅 mock person-api 与 toast，验证列表编排、详情/编辑/删除/批量删除
 * 以及分页与错误路径的可见行为。
 */
import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PersonManagementPage } from '../person'
import * as personApi from '@/lib/person-api'

import type { PersonInfo } from '@/types/person'

const toastMock = vi.fn()

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/person-api', () => ({
  batchDeletePersons: vi.fn(),
  deletePerson: vi.fn(),
  getPersonDetail: vi.fn(),
  getPersonList: vi.fn(),
  getPersonStats: vi.fn(),
  updatePerson: vi.fn(),
}))

// 构造一个字段完整的人物信息（按需覆盖）
function makePerson(id: number, overrides: Partial<PersonInfo> = {}): PersonInfo {
  return {
    id,
    is_known: true,
    person_id: `person-${id}`,
    person_name: `人物${id}`,
    name_reason: null,
    platform: 'qq',
    user_id: `${10000 + id}`,
    nickname: `昵称${id}`,
    group_nick_name: null,
    memory_points: null,
    know_times: null,
    know_since: null,
    last_know: null,
    ...overrides,
  }
}

function makeWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

// 页面同时渲染桌面表格与移动卡片两套视图（jsdom 不应用媒体查询 CSS），
// 行内断言统一收敛到带 aria-label 的桌面表格，避免重复文本干扰
function getDesktopTable() {
  return screen.getByRole('table', { name: '人物信息列表' })
}

beforeEach(() => {
  // Radix Select 在 jsdom 下依赖 PointerCapture API，按需补桩
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = vi.fn()
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = vi.fn()
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = vi.fn()
  }

  vi.mocked(personApi.getPersonList).mockResolvedValue({
    data: [makePerson(1), makePerson(2, { is_known: false, platform: 'telegram' })],
    total: 2,
    page: 1,
    page_size: 20,
  })
  vi.mocked(personApi.getPersonStats).mockResolvedValue({
    total: 5,
    known: 3,
    unknown: 2,
    platforms: { qq: 4, telegram: 1 },
  })
  vi.mocked(personApi.getPersonDetail).mockResolvedValue(
    makePerson(1, { memory_points: '喜欢摸鱼', name_reason: '群里都这么叫' })
  )
  vi.mocked(personApi.updatePerson).mockResolvedValue(makePerson(1))
  vi.mocked(personApi.deletePerson).mockResolvedValue(undefined)
  vi.mocked(personApi.batchDeletePersons).mockResolvedValue({
    message: '成功删除 2 个人物',
    deleted_count: 2,
    failed_count: 0,
    failed_ids: [],
  })
})

async function renderPage() {
  render(<PersonManagementPage />, { wrapper: makeWrapper() })
  await screen.findByRole('heading', { name: /人物信息管理/ })
}

describe('PersonManagementPage 特征化', () => {
  it('初始加载：以默认分页参数拉取列表与统计并渲染行数据', async () => {
    await renderPage()

    // 列表请求携带默认分页参数（搜索/筛选为空时传 undefined）
    await waitFor(() =>
      expect(personApi.getPersonList).toHaveBeenCalledWith({
        page: 1,
        page_size: 20,
        search: undefined,
        is_known: undefined,
        platform: undefined,
      })
    )
    expect(personApi.getPersonStats).toHaveBeenCalled()

    // 统计卡片显示后端数值（「已认识/未认识」也会出现在行状态标签里，收敛到统计网格内断言）
    const statsGrid = screen.getByText('总人数').closest('.grid') as HTMLElement
    expect(within(statsGrid).getByText('总人数').nextElementSibling).toHaveTextContent('5')
    expect(within(statsGrid).getByText('已认识').nextElementSibling).toHaveTextContent('3')
    expect(within(statsGrid).getByText('未认识').nextElementSibling).toHaveTextContent('2')

    // 桌面表格渲染人物行：名称、昵称、用户ID、平台与状态标签
    const table = getDesktopTable()
    expect(await within(table).findByText('人物1')).toBeInTheDocument()
    expect(within(table).getByText('昵称1')).toBeInTheDocument()
    expect(within(table).getByText('10001')).toBeInTheDocument()
    expect(within(table).getByText('telegram')).toBeInTheDocument()
    // person-2 未认识 → 状态标签为「未认识」
    expect(within(table).getByText('未认识')).toBeInTheDocument()
  })

  it('统计接口失败时卡片保持占位 0，列表不受影响', async () => {
    vi.mocked(personApi.getPersonStats).mockRejectedValue(new Error('统计失败'))
    await renderPage()

    const table = getDesktopTable()
    expect(await within(table).findByText('人物1')).toBeInTheDocument()
    expect(screen.getByText('总人数').nextElementSibling).toHaveTextContent('0')
  })

  it('搜索输入立即生效：以 search 参数重新拉取列表', async () => {
    const user = userEvent.setup()
    await renderPage()
    await within(getDesktopTable()).findByText('人物1')

    await user.type(screen.getByPlaceholderText('搜索名称、昵称或用户ID...'), '张三')

    await waitFor(() =>
      expect(personApi.getPersonList).toHaveBeenCalledWith({
        page: 1,
        page_size: 20,
        search: '张三',
        is_known: undefined,
        platform: undefined,
      })
    )
  })

  it('认识状态筛选：选择「已认识」后以 is_known=true 重新拉取', async () => {
    const user = userEvent.setup()
    await renderPage()
    await within(getDesktopTable()).findByText('人物1')

    await user.click(screen.getByLabelText('认识状态'))
    await user.click(await screen.findByRole('option', { name: '已认识' }))

    await waitFor(() =>
      expect(personApi.getPersonList).toHaveBeenCalledWith({
        page: 1,
        page_size: 20,
        search: undefined,
        is_known: true,
        platform: undefined,
      })
    )
  })

  it('查看详情：调用 getPersonDetail 并展示详情对话框内容', async () => {
    const user = userEvent.setup()
    await renderPage()
    await within(getDesktopTable()).findByText('人物1')

    await user.click(screen.getAllByRole('button', { name: '详情' })[0])

    expect(await screen.findByText('人物详情')).toBeInTheDocument()
    expect(personApi.getPersonDetail).toHaveBeenCalledWith('person-1')
    // 详情返回的记忆点与名称原因均展示
    expect(screen.getByText('喜欢摸鱼')).toBeInTheDocument()
    expect(screen.getByText('群里都这么叫')).toBeInTheDocument()
  })

  it('查看详情失败：toast 提示加载详情失败', async () => {
    vi.mocked(personApi.getPersonDetail).mockRejectedValue(new Error('后端错误'))
    const user = userEvent.setup()
    await renderPage()
    await within(getDesktopTable()).findByText('人物1')

    await user.click(screen.getAllByRole('button', { name: '详情' })[0])

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '加载详情失败',
        description: '后端错误',
        variant: 'destructive',
      })
    )
    // 对话框未打开
    expect(screen.queryByText('人物详情')).not.toBeInTheDocument()
  })

  it('编辑保存：提交表单调用 updatePerson 并 toast 保存成功', async () => {
    const user = userEvent.setup()
    await renderPage()
    await within(getDesktopTable()).findByText('人物1')

    await user.click(screen.getAllByRole('button', { name: '编辑' })[0])
    expect(await screen.findByText('编辑人物信息')).toBeInTheDocument()

    const nameInput = screen.getByLabelText('人物名称')
    await user.clear(nameInput)
    await user.type(nameInput, '新名字')
    await user.click(screen.getByRole('button', { name: '保存' }))

    // 表单初值来自被编辑人物，name_reason 为 null 时回填空串
    await waitFor(() =>
      expect(personApi.updatePerson).toHaveBeenCalledWith('person-1', {
        person_name: '新名字',
        name_reason: '',
        nickname: '昵称1',
        is_known: true,
      })
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '保存成功',
        description: '人物信息已更新',
      })
    )
  })

  it('单条删除：确认后调用 deletePerson 并 toast 删除成功', async () => {
    const user = userEvent.setup()
    await renderPage()
    await within(getDesktopTable()).findByText('人物1')

    await user.click(screen.getAllByRole('button', { name: '删除' })[0])
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText('确认删除')).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: '删除' }))

    await waitFor(() => expect(personApi.deletePerson).toHaveBeenCalledWith('person-1'))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '删除成功',
        description: '已删除人物信息: 人物1',
      })
    )
  })

  it('批量删除：全选后确认调用 batchDeletePersons', async () => {
    const user = userEvent.setup()
    await renderPage()
    await within(getDesktopTable()).findByText('人物1')

    // 表头全选 → 工具栏出现选中计数与批量删除按钮
    await user.click(screen.getByRole('checkbox', { name: '全选' }))
    expect(screen.getByText('已选择 2 个人物')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '批量删除' }))
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText('确认批量删除')).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: '批量删除' }))

    await waitFor(() =>
      expect(personApi.batchDeletePersons).toHaveBeenCalledWith(['person-1', 'person-2'])
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '批量删除完成',
        description: '成功删除 2 个人物',
      })
    )
  })

  it('取消选择：清空选中集并隐藏批量操作按钮', async () => {
    const user = userEvent.setup()
    await renderPage()
    await within(getDesktopTable()).findByText('人物1')

    await user.click(screen.getByRole('checkbox', { name: '全选' }))
    expect(screen.getByText('已选择 2 个人物')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '取消选择' }))
    expect(screen.queryByText('已选择 2 个人物')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '批量删除' })).not.toBeInTheDocument()
  })

  it('分页：多页时点击下一页以 page=2 重新拉取', async () => {
    vi.mocked(personApi.getPersonList).mockResolvedValue({
      data: [makePerson(1), makePerson(2)],
      total: 50,
      page: 1,
      page_size: 20,
    })
    const user = userEvent.setup()
    await renderPage()

    expect(await screen.findByText('共 50 条记录，第 1 / 3 页')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '下一页' }))

    await waitFor(() =>
      expect(personApi.getPersonList).toHaveBeenCalledWith({
        page: 2,
        page_size: 20,
        search: undefined,
        is_known: undefined,
        platform: undefined,
      })
    )
    expect(await screen.findByText('共 50 条记录，第 2 / 3 页')).toBeInTheDocument()
  })

  it('页码跳转：超出范围 toast 无效页码，合法页码触发拉取', async () => {
    vi.mocked(personApi.getPersonList).mockResolvedValue({
      data: [makePerson(1), makePerson(2)],
      total: 50,
      page: 1,
      page_size: 20,
    })
    const user = userEvent.setup()
    await renderPage()
    await screen.findByText('共 50 条记录，第 1 / 3 页')

    const jumpInput = screen.getByRole('spinbutton')

    // 超出总页数 → 提示无效页码，不发起请求
    await user.type(jumpInput, '9')
    await user.click(screen.getByRole('button', { name: '跳转' }))
    expect(toastMock).toHaveBeenCalledWith({
      title: '无效的页码',
      description: '请输入1-3之间的页码',
      variant: 'destructive',
    })

    // 合法页码 → 以 page=3 重新拉取
    await user.clear(jumpInput)
    await user.type(jumpInput, '3')
    await user.click(screen.getByRole('button', { name: '跳转' }))
    await waitFor(() =>
      expect(personApi.getPersonList).toHaveBeenCalledWith({
        page: 3,
        page_size: 20,
        search: undefined,
        is_known: undefined,
        platform: undefined,
      })
    )
  })

  it('空列表：显示暂无数据且不渲染分页栏', async () => {
    vi.mocked(personApi.getPersonList).mockResolvedValue({
      data: [],
      total: 0,
      page: 1,
      page_size: 20,
    })
    await renderPage()

    // 桌面表格与移动卡片两套视图各渲染一份空态
    expect(await screen.findAllByText('暂无数据')).toHaveLength(2)
    expect(screen.queryByText(/共 0 条记录/)).not.toBeInTheDocument()
  })

  it('列表加载失败：展示错误信息，点击重试后恢复', async () => {
    vi.mocked(personApi.getPersonList).mockRejectedValueOnce(new Error('列表加载失败了'))
    const user = userEvent.setup()
    await renderPage()

    // 两套视图各展示一份错误文案与重试按钮
    expect(await screen.findAllByText('列表加载失败了')).toHaveLength(2)

    await user.click(screen.getAllByRole('button', { name: '重试' })[0])

    // 重试后走 beforeEach 的成功桩，列表恢复
    expect(await within(getDesktopTable()).findByText('人物1')).toBeInTheDocument()
    expect(screen.queryByText('列表加载失败了')).not.toBeInTheDocument()
  })
})
