import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LearningCreateDialog } from '../learning-create-dialog'
import { getExpressionChatTargets } from '@/lib/expression-api'
import { getJargonChatList } from '@/lib/jargon-api'
import * as learning from '@/lib/text-learning-api'
import type { ChatInfo } from '@/types/expression'
import type { JargonChatInfo } from '@/types/jargon'

vi.mock('@/lib/expression-api', () => ({ getExpressionChatTargets: vi.fn() }))
vi.mock('@/lib/jargon-api', () => ({ getJargonChatList: vi.fn() }))
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: vi.fn() }) }))
vi.mock('@/lib/text-learning-api', async (original) => ({
  ...(await original<typeof learning>()),
  prepareLearningText: vi.fn(),
  extractTextCandidates: vi.fn(),
  importLearningCandidates: vi.fn(),
}))

const first = { title: '调侃', description: '这波直接起飞', evidence: '这波直接起飞' }
const second = { title: '赞同', description: '确实如此', evidence: '确实如此' }
const third = { title: '安慰', description: '慢慢来', evidence: '慢慢来' }

beforeEach(() => {
  vi.mocked(getExpressionChatTargets).mockResolvedValue([
    { chat_id: 'chat1', chat_name: '测试群', account_id: '123' } as ChatInfo,
  ])
  vi.mocked(getJargonChatList).mockResolvedValue({
    success: true,
    data: [{ session_id: 'chat1', chat_name: '测试群', account_id: '123' } as JargonChatInfo],
  })
  vi.mocked(learning.prepareLearningText).mockResolvedValue(['文段一', '文段二'])
  vi.mocked(learning.importLearningCandidates).mockResolvedValue({
    success: true,
    message: '完成',
    imported_count: 1,
    skipped_count: 0,
    failed_count: 0,
  })
})
afterEach(cleanup)

function show(kind: learning.LearningKind = 'expression') {
  const manual = vi.fn()
  const success = vi.fn()
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <LearningCreateDialog
        open
        onOpenChange={vi.fn()}
        kind={kind}
        initialTargetIds={['chat1']}
        onManual={manual}
        onSuccess={success}
      />
    </QueryClientProvider>
  )
  return { manual, success }
}

async function start() {
  fireEvent.click(screen.getByRole('button', { name: /自动学习/ }))
  fireEvent.change(screen.getByLabelText('学习文段'), { target: { value: '长篇学习素材' } })
  fireEvent.click(screen.getByRole('button', { name: '开始学习' }))
}

describe('LearningCreateDialog', () => {
  it('手动新增打开原有表单，不调用学习接口', () => {
    const { manual } = show()
    fireEvent.click(screen.getByRole('button', { name: /手动新增/ }))
    expect(manual).toHaveBeenCalledOnce()
    expect(learning.prepareLearningText).not.toHaveBeenCalled()
  })

  it.each(['expression', 'jargon'] as const)('分段学习 %s 后只导入勾选候选', async (kind) => {
    vi.mocked(learning.extractTextCandidates)
      .mockResolvedValueOnce([first])
      .mockResolvedValueOnce([first, second])
    const { success } = show(kind)
    await start()
    await screen.findByText('学习完成')
    expect(screen.getByText('2 / 2 段')).toBeInTheDocument()
    expect(screen.getByText('已选 2 / 2 条')).toBeInTheDocument()
    expect(learning.importLearningCandidates).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('checkbox', { name: /：赞同/ }))
    fireEvent.click(screen.getByRole('button', { name: '导入所选（1）' }))
    await waitFor(() =>
      expect(learning.importLearningCandidates).toHaveBeenCalledWith(
        kind,
        ['chat1'],
        [first],
        false
      )
    )
    expect(success).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: '导入所选（0）' })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: /：调侃/ })).toBeDisabled()
  })

  it('单段失败后继续后续段，结果可先导入，再单独重试补充新候选', async () => {
    vi.mocked(learning.prepareLearningText).mockResolvedValue(['文段一', '文段二', '文段三'])
    vi.mocked(learning.extractTextCandidates)
      .mockResolvedValueOnce([first])
      .mockRejectedValueOnce(new Error('模型暂不可用'))
      .mockResolvedValueOnce([second])
      .mockResolvedValueOnce([first, third])
    vi.mocked(learning.importLearningCandidates).mockResolvedValueOnce({
      success: true,
      message: '完成',
      imported_count: 2,
      skipped_count: 0,
      failed_count: 0,
    })
    show()
    await start()
    await screen.findByText('本轮学习结束，部分段落失败')
    await screen.findByText('模型暂不可用')
    expect(screen.getByText('3 / 3 段')).toBeInTheDocument()
    expect(screen.getByText('失败段落（1）')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '导入所选（2）' }))
    await waitFor(() =>
      expect(learning.importLearningCandidates).toHaveBeenCalledWith(
        'expression',
        ['chat1'],
        [first, second],
        false
      )
    )
    await screen.findByRole('button', { name: '导入所选（0）' })
    fireEvent.click(screen.getByRole('button', { name: '重试第 2 段' }))
    await screen.findByText('学习完成')
    expect(learning.extractTextCandidates).toHaveBeenNthCalledWith(1, 'expression', '文段一')
    expect(learning.extractTextCandidates).toHaveBeenNthCalledWith(2, 'expression', '文段二')
    expect(learning.extractTextCandidates).toHaveBeenNthCalledWith(3, 'expression', '文段三')
    expect(learning.extractTextCandidates).toHaveBeenNthCalledWith(4, 'expression', '文段二')
    expect(screen.queryByText('失败段落（1）')).not.toBeInTheDocument()
    expect(screen.getByText('已选 1 / 1 条')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '导入所选（1）' }))
    await waitFor(() =>
      expect(learning.importLearningCandidates).toHaveBeenNthCalledWith(
        2,
        'expression',
        ['chat1'],
        [third],
        false
      )
    )
  })

  it('批量重试只处理失败段，重试再失败也不影响其他段', async () => {
    vi.mocked(learning.prepareLearningText).mockResolvedValue([
      '文段一',
      '文段二',
      '文段三',
      '文段四',
    ])
    vi.mocked(learning.extractTextCandidates)
      .mockRejectedValueOnce(new Error('第一段失败'))
      .mockResolvedValueOnce([first])
      .mockRejectedValueOnce(new Error('第三段失败'))
      .mockResolvedValueOnce([second])
      .mockRejectedValueOnce(new Error('第一段仍失败'))
      .mockResolvedValueOnce([third])
    show()
    await start()
    await screen.findByText('失败段落（2）')
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '重试全部失败段' })).toBeEnabled()
    )
    fireEvent.click(screen.getByRole('button', { name: '重试全部失败段' }))
    await screen.findByText('第一段仍失败')
    await waitFor(() => expect(screen.getByText('失败段落（1）')).toBeInTheDocument())
    expect(learning.extractTextCandidates).toHaveBeenCalledTimes(6)
    expect(learning.extractTextCandidates).toHaveBeenNthCalledWith(5, 'expression', '文段一')
    expect(learning.extractTextCandidates).toHaveBeenNthCalledWith(6, 'expression', '文段三')
    expect(screen.getByText('已选 3 / 3 条')).toBeInTheDocument()
    expect(screen.getByText('4 / 4 段')).toBeInTheDocument()
  })

  it('后续段仍在学习时可导入已有结果，随后新增结果可继续导入', async () => {
    let finish!: (value: learning.LearnedCandidate[]) => void
    vi.mocked(learning.extractTextCandidates)
      .mockResolvedValueOnce([first])
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve
          })
      )
    show()
    await start()
    await screen.findByText('正在学习第 2 段')
    fireEvent.click(screen.getByRole('button', { name: '导入所选（1）' }))
    await waitFor(() =>
      expect(learning.importLearningCandidates).toHaveBeenCalledWith(
        'expression',
        ['chat1'],
        [first],
        false
      )
    )
    await screen.findByRole('button', { name: '导入所选（0）' })
    await act(async () => finish([second]))
    await screen.findByText('学习完成')
    fireEvent.click(screen.getByRole('button', { name: '导入所选（1）' }))
    await waitFor(() =>
      expect(learning.importLearningCandidates).toHaveBeenNthCalledWith(
        2,
        'expression',
        ['chat1'],
        [second],
        false
      )
    )
  })

  it('失败段重试过程中不能重置文段，避免清除正在处理的结果', async () => {
    let finish!: (value: learning.LearnedCandidate[]) => void
    vi.mocked(learning.extractTextCandidates)
      .mockRejectedValueOnce(new Error('首段失败'))
      .mockResolvedValueOnce([first])
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve
          })
      )
    show()
    await start()
    await screen.findByText('本轮学习结束，部分段落失败')
    fireEvent.click(screen.getByRole('button', { name: '重试第 1 段' }))
    await screen.findByText('正在学习第 1 段')
    expect(screen.getByRole('button', { name: '重新学习' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '重试全部失败段' })).toBeDisabled()
    await act(async () => finish([second]))
    await screen.findByText('学习完成')
    expect(screen.getByRole('button', { name: '重新学习' })).toBeEnabled()
    expect(screen.getByText('已选 2 / 2 条')).toBeInTheDocument()
  })

  it('暂停会完成当前段，继续时不重复学习', async () => {
    let finish!: (value: learning.LearnedCandidate[]) => void
    vi.mocked(learning.extractTextCandidates)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve
          })
      )
      .mockResolvedValueOnce([second])
    show()
    await start()
    await screen.findByText('正在学习第 1 段')
    fireEvent.click(screen.getByRole('button', { name: '暂停学习' }))
    await act(async () => finish([first]))
    await screen.findByText('学习已暂停')
    expect(learning.extractTextCandidates).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: '继续学习' }))
    await screen.findByText('学习完成')
    expect(learning.extractTextCandidates).toHaveBeenCalledTimes(2)
  })

  it.each(['expression', 'jargon'] as const)(
    '%s 无原文依据也能编辑后导入全局，无需可用聊天',
    async (kind) => {
      vi.mocked(getExpressionChatTargets).mockResolvedValue([])
      vi.mocked(getJargonChatList).mockResolvedValue({ success: true, data: [] })
      vi.mocked(learning.prepareLearningText).mockResolvedValue(['原文'])
      vi.mocked(learning.extractTextCandidates).mockResolvedValue([
        { title: '旧内容', description: '旧含义' },
      ])
      const { success } = show(kind)
      await start()
      await screen.findByText('学习完成')
      expect(screen.getByText('未提供原文依据，可确认后导入')).toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: '全局（所有聊天）' }))
      expect(screen.getByRole('button', { name: '导入所选（1）' })).toBeEnabled()
      const noun = kind === 'expression' ? '表达方式' : '黑话'
      fireEvent.click(screen.getByRole('button', { name: `编辑第 1 条${noun}` }))
      const titleLabel = kind === 'expression' ? '情境' : '黑话'
      const descriptionLabel = kind === 'expression' ? '表达方式' : '含义'
      fireEvent.change(screen.getByLabelText(`第 1 条${titleLabel}`), {
        target: { value: '编辑后的内容' },
      })
      fireEvent.change(screen.getByLabelText(`第 1 条${descriptionLabel}`), {
        target: { value: '编辑后的含义' },
      })
      fireEvent.click(screen.getByRole('button', { name: `完成编辑第 1 条${noun}` }))
      fireEvent.click(screen.getByRole('button', { name: '导入所选（1）' }))
      await waitFor(() =>
        expect(learning.importLearningCandidates).toHaveBeenCalledWith(
          kind,
          [],
          [{ title: '编辑后的内容', description: '编辑后的含义' }],
          true
        )
      )
      expect(success).toHaveBeenCalledWith(true)
    }
  )

  it('编辑为空时阻止导入，补全后无需填写原文即可导入', async () => {
    vi.mocked(learning.prepareLearningText).mockResolvedValue(['原文'])
    vi.mocked(learning.extractTextCandidates).mockResolvedValue([first])
    show()
    await start()
    await screen.findByText('学习完成')
    fireEvent.click(screen.getByRole('button', { name: '编辑第 1 条表达方式' }))
    fireEvent.change(screen.getByLabelText('第 1 条情境'), { target: { value: ' ' } })
    expect(screen.getByRole('button', { name: '导入所选（1）' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('第 1 条情境'), { target: { value: '补全情境' } })
    fireEvent.change(screen.getByLabelText('第 1 条原文依据（可选）'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: '导入所选（1）' }))
    await waitFor(() =>
      expect(learning.importLearningCandidates).toHaveBeenCalledWith(
        'expression',
        ['chat1'],
        [{ ...first, title: '补全情境', evidence: '' }],
        false
      )
    )
  })

  it('后续段返回时保留用户对已有候选的编辑', async () => {
    let finish!: (value: learning.LearnedCandidate[]) => void
    vi.mocked(learning.extractTextCandidates)
      .mockResolvedValueOnce([first])
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve
          })
      )
    show()
    await start()
    await screen.findByText('正在学习第 2 段')
    fireEvent.click(screen.getByRole('button', { name: '编辑第 1 条表达方式' }))
    fireEvent.change(screen.getByLabelText('第 1 条表达方式'), {
      target: { value: '用户修正的表达' },
    })
    fireEvent.click(screen.getByRole('button', { name: '完成编辑第 1 条表达方式' }))
    await act(async () => finish([second]))
    await screen.findByText('学习完成')
    expect(screen.getByText('用户修正的表达')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '导入所选（2）' }))
    await waitFor(() =>
      expect(learning.importLearningCandidates).toHaveBeenCalledWith(
        'expression',
        ['chat1'],
        [{ ...first, description: '用户修正的表达' }, second],
        false
      )
    )
  })
})
