import { describe, expect, it, vi } from 'vitest'

import { importExpressions } from '@/lib/expression-api'
import { importJargons } from '@/lib/jargon-api'
import { importLearningCandidates, mergeLearningCandidates } from '@/lib/text-learning-api'

vi.mock('@/lib/expression-api', () => ({ importExpressions: vi.fn() }))
vi.mock('@/lib/jargon-api', () => ({ importJargons: vi.fn() }))

const candidate = { title: '起飞', description: '进展顺利', evidence: '这波起飞' }

describe('长文学习导入', () => {
  it('表达候选转换为经过用户确认的表达，并保留原文', async () => {
    await importLearningCandidates('expression', ['real-session'], [candidate])
    expect(importExpressions).toHaveBeenCalledWith({
      chat_id: 'real-session',
      expressions: [
        expect.objectContaining({
          situation: '起飞',
          style: '进展顺利',
          content_list: '["这波起飞"]',
          checked: true,
          modified_by: 'user',
        }),
      ],
    })
  })

  it('黑话导入复用真实聊天列表并跳过重复，不覆盖已有记录', async () => {
    await importLearningCandidates('jargon', ['real-session', 'another-session'], [candidate])
    expect(importJargons).toHaveBeenCalledWith({
      target_session_ids: ['real-session', 'another-session'],
      conflict_strategy: 'skip',
      jargons: [
        expect.objectContaining({
          content: '起飞',
          meaning: '进展顺利',
          created_by: 'MANUAL',
          is_global: false,
        }),
      ],
    })
  })

  it('缺少用户选项时禁止入库', async () => {
    await expect(importLearningCandidates('expression', [], [candidate])).rejects.toThrow('请选择')
    await expect(importLearningCandidates('jargon', ['session'], [])).rejects.toThrow('请选择')
    expect(importExpressions).not.toHaveBeenCalled()
    expect(importJargons).not.toHaveBeenCalled()
  })

  it('跨段去重区分表达组合和黑话词条', () => {
    const differentMeaning = { ...candidate, description: '非常高兴' }
    expect(
      mergeLearningCandidates([candidate], [candidate, differentMeaning], 'expression')
    ).toHaveLength(2)
    expect(mergeLearningCandidates([candidate], [candidate, differentMeaning], 'jargon')).toEqual([
      candidate,
    ])
  })

  it('无原文表达导入全局时不写聊天 ID，保留编辑内容', async () => {
    await importLearningCandidates(
      'expression',
      [],
      [{ title: ' 编辑情境 ', description: ' 编辑表达 ' }],
      true
    )
    expect(importExpressions).toHaveBeenCalledWith({
      is_global: true,
      expressions: [
        expect.objectContaining({ situation: '编辑情境', style: '编辑表达', content_list: '[]' }),
      ],
    })
    expect(vi.mocked(importExpressions).mock.calls[0][0]).not.toHaveProperty('chat_id')
  })

  it('黑话全局导入无需聊天，无原文也可以导入', async () => {
    await importLearningCandidates(
      'jargon',
      [],
      [{ title: '全局黑话', description: '编辑含义' }],
      true
    )
    expect(importJargons).toHaveBeenCalledWith({
      target_session_ids: [],
      is_global: true,
      conflict_strategy: 'skip',
      jargons: [
        expect.objectContaining({ content: '全局黑话', meaning: '编辑含义', is_global: true }),
      ],
    })
  })

  it('全局导入拒绝混入聊天目标，所选内容为空时阻止入库', async () => {
    await expect(
      importLearningCandidates('jargon', ['session'], [candidate], true)
    ).rejects.toThrow('不能同时指定聊天')
    await expect(
      importLearningCandidates('expression', [], [{ title: ' ', description: '含义' }], true)
    ).rejects.toThrow('请填写')
    expect(importExpressions).not.toHaveBeenCalled()
    expect(importJargons).not.toHaveBeenCalled()
  })
})
