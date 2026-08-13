import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Search } from 'lucide-react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ConfigSchema } from '@/types/config-schema'

import { SearchDialog } from './search-dialog'

const navigateMock = vi.fn()
const getBotConfigSchemaMock = vi.fn()
const getModelConfigSchemaMock = vi.fn()
const searchWithAIStreamMock = vi.fn()
const onOpenChangeMock = vi.fn()

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigateMock,
}))

// t 必须是稳定引用，不能在每次 render 时新建函数
const i18nMock = vi.hoisted(() => {
  const t = (key: string) => key
  return { i18n: { language: 'zh' }, t }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: i18nMock.i18n,
    t: i18nMock.t,
  }),
}))

vi.mock('@/components/layout/use-menu-sections', () => ({
  useMenuSections: () => [
    {
      title: '配置',
      items: [
        {
          icon: Search,
          label: '麦麦设置',
          path: '/config/bot',
          searchDescription: '编辑麦麦配置',
        },
      ],
    },
  ],
}))

vi.mock('@/router', () => ({
  registeredRoutePaths: new Set(['/config/bot']),
}))

vi.mock('@/lib/config-api', () => ({
  getBotConfigSchema: () => getBotConfigSchemaMock(),
  getModelConfigSchema: () => getModelConfigSchemaMock(),
}))

vi.mock('@/lib/ai-search-api', () => ({
  searchWithAIStream: (...args: unknown[]) => searchWithAIStreamMock(...args),
}))

const botConfigSchema: ConfigSchema = {
  className: 'Config',
  classDoc: '麦麦配置',
  fields: [
    {
      name: 'personality',
      type: 'object',
      label: '人格',
      description: '人格相关设置',
      required: true,
    },
  ],
  nested: {
    personality: {
      className: 'PersonalityConfig',
      classDoc: '人格配置',
      fields: [
        {
          name: 'personality',
          type: 'string',
          label: '人格设定',
          description: '麦麦的人格和身份设定',
          required: true,
        },
      ],
    },
  },
}

const modelConfigSchema: ConfigSchema = {
  className: 'ModelConfig',
  classDoc: '模型配置',
  fields: [
    {
      name: 'models',
      type: 'array',
      label: '模型列表',
      description: '已配置的推理模型',
      required: true,
    },
  ],
}

const RECENT_SEARCH_ROUTES_KEY = 'maibot-search-recent-routes'

function resultButtons() {
  return screen.getAllByRole('button').filter((button) => button.getAttribute('title')?.includes(' · '))
}

/** 等配置索引异步写入完成，避免测试结束后的 act 警告 */
async function flushConfigIndex() {
  await waitFor(() => {
    expect(getBotConfigSchemaMock).toHaveBeenCalled()
  })
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

describe('SearchDialog', () => {
  beforeEach(() => {
    navigateMock.mockReset()
    getBotConfigSchemaMock.mockResolvedValue(botConfigSchema)
    getModelConfigSchemaMock.mockRejectedValue(new Error('模型配置不可用'))
    searchWithAIStreamMock.mockReset()
    onOpenChangeMock.mockReset()
    localStorage.clear()
  })

  afterEach(() => {
    cleanup()
  })

  it('保留与页面共用同一路径的配置项搜索结果', async () => {
    const user = userEvent.setup()
    render(<SearchDialog open onOpenChange={vi.fn()} />)

    await user.type(screen.getByPlaceholderText('search.aiHint'), '人格')

    expect(await screen.findByText('人格设定')).toBeInTheDocument()
    expect(screen.queryByText('search.noResults')).not.toBeInTheDocument()
  })

  it('用 AI 返回的真实索引 ID 导航并定位配置字段', async () => {
    searchWithAIStreamMock.mockImplementation(
      async (
        _payload: unknown,
        onProgress: (event: {
          type: 'progress'
          stage: 'tool' | 'correcting'
          status: 'started'
          tool?: string
          query?: string
          error?: string
        }) => void
      ) => {
        onProgress({
          type: 'progress',
          stage: 'tool',
          status: 'started',
          tool: 'search_official_docs',
          query: '人格 身份设定',
        })
        onProgress({
          type: 'progress',
          stage: 'correcting',
          status: 'started',
          error: '移除无依据技术项',
        })
        return {
          success: true,
          cached: false,
          model_name: 'test-utils-model',
          answer: '可以在 **人格设置** 中调整麦麦的性格描述。',
          suggestions: ['修改后先在 `测试群` 观察回复效果'],
          sources: [
            {
              title: 'Bot 配置',
              url: 'https://docs.mai-mai.org/manual/configuration/bot-config',
            },
          ],
          expanded_terms: ['人格', '身份设定'],
          results: [
            {
              id: 'c2',
              score: 0.98,
              reason: '这里用于调整麦麦的人格与身份',
            },
          ],
          prompt_tokens: 100,
          completion_tokens: 20,
          total_tokens: 120,
        }
      }
    )
    const user = userEvent.setup()
    render(<SearchDialog open onOpenChange={onOpenChangeMock} />)

    await user.type(screen.getByPlaceholderText('search.aiHint'), '我想修改麦麦的性格')
    await user.click(await screen.findByRole('button', { name: 'search.aiSearch' }))

    expect(await screen.findByText('这里用于调整麦麦的人格与身份')).toBeInTheDocument()
    expect(screen.getByText('人格设置').tagName).toBe('STRONG')
    expect(screen.getByText('测试群').tagName).toBe('CODE')
    expect(screen.getByRole('link', { name: 'Bot 配置' })).toHaveAttribute(
      'href',
      'https://docs.mai-mai.org/manual/configuration/bot-config'
    )
    expect(screen.getByText('search.progressTitle')).toBeInTheDocument()
    const progressToggle = screen.getByRole('button', { name: 'search.progressExpand' })
    expect(progressToggle).toHaveAttribute('aria-expanded', 'false')
    await user.click(progressToggle)
    expect(screen.getByRole('button', { name: 'search.progressCollapse' })).toHaveAttribute(
      'aria-expanded',
      'true'
    )
    expect(screen.getByText('search.progressSearchDocs')).toBeInTheDocument()
    expect(screen.getByText('人格 身份设定')).toBeInTheDocument()
    expect(screen.getByText('search.progressCorrecting')).toBeInTheDocument()
    expect(searchWithAIStreamMock).toHaveBeenCalledWith(
      expect.objectContaining({
        query: '我想修改麦麦的性格',
        language: 'zh',
        candidates: expect.arrayContaining([
          expect.objectContaining({ id: 'c2', title: '人格设定' }),
        ]),
      }),
      expect.any(Function),
      expect.any(AbortSignal)
    )

    await user.click(screen.getByRole('button', { name: /人格设定/ }))

    expect(navigateMock).toHaveBeenCalledWith({
      to: '/config/bot?field=personality.personality',
    })
    expect(onOpenChangeMock).not.toHaveBeenCalledWith(false)
    expect(screen.getByPlaceholderText('search.aiHint')).toHaveValue('我想修改麦麦的性格')
  })

  it('在过程列表末尾明确显示回答生成失败及原因', async () => {
    searchWithAIStreamMock.mockImplementation(
      async (
        _payload: unknown,
        onProgress: (event: { type: 'progress'; stage: 'finalizing' }) => void
      ) => {
        onProgress({
          type: 'progress',
          stage: 'finalizing',
        })
        throw new Error('AI 搜索结果解析失败: 模型返回的 JSON 不完整')
      }
    )
    const user = userEvent.setup()
    render(<SearchDialog open onOpenChange={onOpenChangeMock} />)

    await user.type(screen.getByPlaceholderText('search.aiHint'), '麦麦说话太多')
    await user.click(await screen.findByRole('button', { name: 'search.aiSearch' }))

    expect(await screen.findByText('search.progressAnswerFailed')).toBeInTheDocument()
    expect(screen.getAllByText('AI 搜索结果解析失败: 模型返回的 JSON 不完整')).not.toHaveLength(0)
    expect(screen.getByRole('button', { name: 'search.progressCollapse' })).toHaveAttribute(
      'aria-expanded',
      'true'
    )
  })

  it('从 localStorage 恢复最近访问，并过滤掉非字符串路径', async () => {
    localStorage.setItem(
      RECENT_SEARCH_ROUTES_KEY,
      JSON.stringify(['/config/bot', 12, null, '/missing'])
    )
    render(<SearchDialog open onOpenChange={onOpenChangeMock} />)

    expect(await screen.findByText('search.recent')).toBeInTheDocument()
    await flushConfigIndex()
    const recent = resultButtons().find((button) => button.textContent?.includes('search.recent'))
    expect(recent).toHaveTextContent('麦麦设置')
    expect(resultButtons().filter((button) => button.textContent?.includes('search.recent'))).toHaveLength(1)
  })

  it('最近访问不是合法 JSON 时按空列表处理', async () => {
    localStorage.setItem(RECENT_SEARCH_ROUTES_KEY, '{not-json')
    render(<SearchDialog open onOpenChange={onOpenChangeMock} />)

    expect(screen.queryByText('search.recent')).not.toBeInTheDocument()
    expect(resultButtons().some((button) => button.textContent?.includes('麦麦设置'))).toBe(true)
    await flushConfigIndex()
  })

  it('最近访问不是数组时按空列表处理', async () => {
    localStorage.setItem(RECENT_SEARCH_ROUTES_KEY, JSON.stringify({ path: '/config/bot' }))
    render(<SearchDialog open onOpenChange={onOpenChangeMock} />)

    expect(screen.queryByText('search.recent')).not.toBeInTheDocument()
    await flushConfigIndex()
  })

  it('模型配置字段走 getModelConfigPath 并带上 tab 查询参数', async () => {
    getModelConfigSchemaMock.mockResolvedValue(modelConfigSchema)
    const user = userEvent.setup()
    render(<SearchDialog open onOpenChange={onOpenChangeMock} />)

    await user.type(screen.getByPlaceholderText('search.aiHint'), '模型列表')
    await user.click(await screen.findByRole('button', { name: /模型列表/ }))

    expect(navigateMock).toHaveBeenCalledWith({
      to: '/config/model?field=models&tab=models',
    })
    expect(JSON.parse(localStorage.getItem(RECENT_SEARCH_ROUTES_KEY) ?? '[]')).toEqual([
      '/config/model',
    ])
  })

  it('Escape 关闭对话框，方向键与 Home/End/Enter 改变选中并导航', async () => {
    const user = userEvent.setup()
    render(<SearchDialog open onOpenChange={onOpenChangeMock} />)

    const input = screen.getByPlaceholderText('search.aiHint')
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(onOpenChangeMock).toHaveBeenCalledWith(false)

    await user.type(input, '人格')
    await screen.findByRole('button', { name: /人格设定/ })
    await waitFor(() => {
      expect(resultButtons().length).toBeGreaterThanOrEqual(2)
    })

    fireEvent.keyDown(input, { key: 'End' })
    expect(resultButtons()[resultButtons().length - 1]?.className).toContain('bg-accent')

    fireEvent.keyDown(input, { key: 'Home' })
    expect(resultButtons()[0]?.className).toContain('bg-accent')

    fireEvent.keyDown(input, { key: 'ArrowUp' })
    expect(resultButtons()[resultButtons().length - 1]?.className).toContain('bg-accent')

    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(resultButtons()[0]?.className).toContain('bg-accent')

    fireEvent.keyDown(input, { key: 'Enter' })
    expect(navigateMock).toHaveBeenCalledWith({
      to: '/config/bot?field=personality',
    })
  })

  it('Ctrl+Enter 触发 AI 搜索；无结果时方向键与 Enter 不导航', async () => {
    searchWithAIStreamMock.mockResolvedValue({
      success: true,
      cached: false,
      model_name: 'test-utils-model',
      answer: '',
      suggestions: [],
      sources: [],
      expanded_terms: [],
      results: [],
      prompt_tokens: 1,
      completion_tokens: 1,
      total_tokens: 2,
    })
    const user = userEvent.setup()
    render(<SearchDialog open onOpenChange={onOpenChangeMock} />)

    const input = screen.getByPlaceholderText('search.aiHint')
    await user.type(input, 'zzzz-no-match-zzzz')
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'search.aiSearch' })).toBeEnabled()
    })

    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'ArrowUp' })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(navigateMock).not.toHaveBeenCalled()

    fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true })
    await waitFor(() => {
      expect(searchWithAIStreamMock).toHaveBeenCalled()
    })
    expect(await screen.findByText('search.aiNoResults')).toBeInTheDocument()
  })
})
