import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { TestConnectionResult } from '@/lib/config-api'

import { ProviderList } from '../ProviderList'
import type { APIProvider } from '../types'

const makeProvider = (overrides: Partial<APIProvider> = {}): APIProvider => ({
  name: 'Alpha',
  base_url: 'https://api.alpha.com/v1',
  api_key: 'sk-test',
  client_type: 'openai',
  max_retry: 2,
  timeout: 30,
  retry_interval: 10,
  ...overrides,
})

/** 三个名称/URL/类型均可区分的提供商，用于搜索与操作断言 */
const threeProviders = (): APIProvider[] => [
  makeProvider(),
  makeProvider({ name: 'Beta', base_url: 'https://api.beta.io/v2', client_type: 'gemini' }),
  makeProvider({ name: 'Gamma', base_url: 'https://api.gamma.dev/v1' }),
]

/** 生成 n 个提供商，用于分页断言 */
const manyProviders = (n: number): APIProvider[] =>
  Array.from({ length: n }, (_, i) =>
    makeProvider({ name: `P-${i + 1}`, base_url: `https://api.example/${i + 1}` })
  )

interface RenderOptions {
  providers?: APIProvider[]
  testingProviders?: Set<string>
  testResults?: Map<string, TestConnectionResult>
  selectedProviders?: Set<number>
}

function renderList(options: RenderOptions = {}) {
  const onEdit = vi.fn()
  const onDelete = vi.fn()
  const onTest = vi.fn()
  const onToggleSelect = vi.fn()
  const onToggleSelectAll = vi.fn()
  const providers = options.providers ?? threeProviders()
  render(
    <ProviderList
      providers={providers}
      testingProviders={options.testingProviders ?? new Set()}
      testResults={options.testResults ?? new Map()}
      selectedProviders={options.selectedProviders ?? new Set()}
      onEdit={onEdit}
      onDelete={onDelete}
      onTest={onTest}
      onToggleSelect={onToggleSelect}
      onToggleSelectAll={onToggleSelectAll}
    />
  )
  return { onEdit, onDelete, onTest, onToggleSelect, onToggleSelectAll, providers }
}

// 组件同时渲染移动端卡片与桌面端表格两套视图（jsdom 不应用媒体查询），
// 行内断言统一收敛到带 aria-label 的桌面表格，避免重复文本干扰
function getTable() {
  return screen.getByRole('table', { name: 'AI 模型提供商列表' })
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
})

afterEach(() => {
  cleanup()
})

describe('ProviderList 渲染与搜索', () => {
  it('表格渲染所有提供商行及其字段', () => {
    renderList()
    const table = getTable()
    expect(within(table).getByText('Alpha')).toBeInTheDocument()
    expect(within(table).getByText('Beta')).toBeInTheDocument()
    expect(within(table).getByText('https://api.beta.io/v2')).toBeInTheDocument()
    expect(within(table).getByText('gemini')).toBeInTheDocument()
    // 重试列合并展示次数与间隔
    expect(within(table).getAllByText('2 次 / 10 秒')).toHaveLength(3)
  })

  it('空列表时展示引导文案且不渲染分页', () => {
    renderList({ providers: [] })
    // 移动端与桌面端各出现一次
    expect(
      screen.getAllByText('暂无提供商配置，点击"添加提供商"开始配置')
    ).toHaveLength(2)
    expect(screen.queryByText('显示')).not.toBeInTheDocument()
  })

  it('按名称搜索时过滤行并显示结果数量', async () => {
    const user = userEvent.setup()
    renderList()
    await user.type(screen.getByPlaceholderText('搜索提供商名称、URL 或类型...'), 'beta')
    expect(screen.getByText('找到 1 个结果')).toBeInTheDocument()
    const table = getTable()
    expect(within(table).getByText('Beta')).toBeInTheDocument()
    expect(within(table).queryByText('Alpha')).not.toBeInTheDocument()
  })

  it('按客户端类型搜索同样命中', async () => {
    const user = userEvent.setup()
    renderList()
    await user.type(screen.getByPlaceholderText('搜索提供商名称、URL 或类型...'), 'gemini')
    expect(screen.getByText('找到 1 个结果')).toBeInTheDocument()
    expect(within(getTable()).getByText('Beta')).toBeInTheDocument()
  })

  it('搜索无结果时展示「未找到匹配的提供商」', async () => {
    const user = userEvent.setup()
    renderList()
    await user.type(screen.getByPlaceholderText('搜索提供商名称、URL 或类型...'), '不存在的')
    expect(screen.getByText('找到 0 个结果')).toBeInTheDocument()
    expect(screen.getAllByText('未找到匹配的提供商')).toHaveLength(2)
  })
})

describe('ProviderList 行操作与选择', () => {
  it('点击测试按钮以名称调用 onTest；测试中按钮禁用', async () => {
    const user = userEvent.setup()
    const { onTest } = renderList({ testingProviders: new Set(['Alpha']) })
    const table = getTable()
    expect(within(table).getByLabelText('测试厂商 Alpha 连接')).toBeDisabled()
    await user.click(within(table).getByLabelText('测试厂商 Beta 连接'))
    expect(onTest).toHaveBeenCalledWith('Beta')
  })

  it('点击编辑/删除按钮回传 provider 与实际索引', async () => {
    const user = userEvent.setup()
    const { onEdit, onDelete, providers } = renderList()
    const table = getTable()
    await user.click(within(table).getByLabelText('编辑厂商 Beta'))
    expect(onEdit).toHaveBeenCalledWith(providers[1], 1)
    await user.click(within(table).getByLabelText('删除厂商 Gamma'))
    expect(onDelete).toHaveBeenCalledWith(2)
  })

  it('搜索过滤后操作按钮仍回传原始列表中的实际索引', async () => {
    const user = userEvent.setup()
    const { onDelete } = renderList()
    await user.type(screen.getByPlaceholderText('搜索提供商名称、URL 或类型...'), 'gamma')
    await user.click(within(getTable()).getByLabelText('删除厂商 Gamma'))
    // Gamma 在过滤后是第 1 行，但实际索引应是原始列表中的 2
    expect(onDelete).toHaveBeenCalledWith(2)
  })

  it('行复选框以实际索引调用 onToggleSelect，选中行带 selected 状态', async () => {
    const user = userEvent.setup()
    const { onToggleSelect } = renderList({ selectedProviders: new Set([1]) })
    const table = getTable()
    const betaCheckbox = within(table).getByLabelText('选择厂商 Beta 用于批量删除')
    expect(betaCheckbox).toBeChecked()
    expect(betaCheckbox.closest('tr')).toHaveAttribute('data-state', 'selected')
    await user.click(within(table).getByLabelText('选择厂商 Alpha 用于批量删除'))
    expect(onToggleSelect).toHaveBeenCalledWith(0)
  })

  it('表头全选复选框在全部选中时为勾选态，点击触发 onToggleSelectAll', async () => {
    const user = userEvent.setup()
    const { onToggleSelectAll } = renderList({ selectedProviders: new Set([0, 1, 2]) })
    const selectAll = within(getTable()).getByLabelText('选择全部厂商用于批量删除')
    expect(selectAll).toBeChecked()
    await user.click(selectAll)
    expect(onToggleSelectAll).toHaveBeenCalledTimes(1)
  })

  it('测试结果通过状态徽章展示在表格状态列', () => {
    const results = new Map<string, TestConnectionResult>([
      [
        'Alpha',
        { network_ok: true, api_key_valid: true, latency_ms: 66, error: null, http_status: 200 },
      ],
    ])
    renderList({ testResults: results })
    const table = getTable()
    expect(
      within(table).getByLabelText('连接正常：网络可访问，API Key 有效，延迟 66ms')
    ).toBeInTheDocument()
    // 其余两个提供商仍为未测试占位
    expect(within(table).getAllByLabelText('未测试：尚未执行厂商连接测试')).toHaveLength(2)
  })
})

describe('ProviderList 分页', () => {
  it('默认每页 20 条，翻页按钮切换页码并更新统计文案', async () => {
    const user = userEvent.setup()
    renderList({ providers: manyProviders(25) })
    expect(screen.getByText('1 到 20 条，共 25 条')).toBeInTheDocument()
    // 第 1 页：表头行 + 20 数据行
    expect(within(getTable()).getAllByRole('row')).toHaveLength(21)
    expect(screen.getByLabelText('上一页')).toBeDisabled()

    await user.click(screen.getByLabelText('下一页'))
    expect(screen.getByText('21 到 25 条，共 25 条')).toBeInTheDocument()
    expect(within(getTable()).getAllByRole('row')).toHaveLength(6)
    expect(screen.getByLabelText('下一页')).toBeDisabled()
    expect(screen.getByLabelText('最后一页')).toBeDisabled()

    await user.click(screen.getByLabelText('第一页'))
    expect(screen.getByText('1 到 20 条，共 25 条')).toBeInTheDocument()
  })

  it('输入页码点击跳转生效，超出范围的页码被忽略', async () => {
    const user = userEvent.setup()
    renderList({ providers: manyProviders(25) })
    const jumpInput = screen.getByRole('spinbutton')

    await user.type(jumpInput, '2')
    await user.click(screen.getByRole('button', { name: '跳转' }))
    expect(screen.getByText('21 到 25 条，共 25 条')).toBeInTheDocument()
    // 跳转成功后输入框被清空
    expect(jumpInput).toHaveValue(null)

    await user.type(jumpInput, '9')
    await user.click(screen.getByRole('button', { name: '跳转' }))
    // 超出总页数：页码不变，输入内容保留
    expect(screen.getByText('21 到 25 条，共 25 条')).toBeInTheDocument()
    expect(jumpInput).toHaveValue(9)
  })

  it('跳转输入框支持回车触发', async () => {
    const user = userEvent.setup()
    renderList({ providers: manyProviders(25) })
    await user.type(screen.getByRole('spinbutton'), '2{Enter}')
    expect(screen.getByText('21 到 25 条，共 25 条')).toBeInTheDocument()
  })

  it('切换每页条数后重置到第一页', async () => {
    const user = userEvent.setup()
    renderList({ providers: manyProviders(25) })
    // 先翻到第 2 页
    await user.click(screen.getByLabelText('下一页'))
    expect(screen.getByText('21 到 25 条，共 25 条')).toBeInTheDocument()

    // 改为每页 50 条：回到第一页且单页展示全部
    await user.click(screen.getByLabelText('显示'))
    await user.click(await screen.findByRole('option', { name: '50' }))
    expect(screen.getByText('1 到 25 条，共 25 条')).toBeInTheDocument()
    expect(within(getTable()).getAllByRole('row')).toHaveLength(26)
  })
})
