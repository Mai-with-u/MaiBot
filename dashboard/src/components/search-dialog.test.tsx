import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Search } from 'lucide-react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

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

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: { language: 'zh' },
    t: (key: string) => key,
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

describe('SearchDialog', () => {
  beforeEach(() => {
    navigateMock.mockReset()
    getBotConfigSchemaMock.mockResolvedValue(botConfigSchema)
    getModelConfigSchemaMock.mockRejectedValue(new Error('模型配置不可用'))
    searchWithAIStreamMock.mockReset()
    onOpenChangeMock.mockReset()
    localStorage.clear()
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
})
