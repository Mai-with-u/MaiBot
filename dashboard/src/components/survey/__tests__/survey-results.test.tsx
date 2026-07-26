/**
 * SurveyResults 问卷结果查看组件测试
 * 覆盖：加载态、概览统计、各题型统计展示（选项计数/平均分/样本答案/暂无数据）、
 * 我的提交标签页（空态与答案格式化）、showUserSubmissions 关闭、错误路径
 */
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SurveyResults } from '../survey-results'
import * as surveyApi from '@/lib/survey-api'
import type { StoredSubmission, SurveyConfig, SurveyStats, SurveyStatsResponse } from '@/types/survey'

// 打桩问卷 API，避免真实网络请求
vi.mock('@/lib/survey-api', () => ({
  getSurveyStats: vi.fn(),
  getUserSubmissions: vi.fn(),
}))

afterEach(() => {
  cleanup()
})

const config: SurveyConfig = {
  id: 'stats-survey',
  version: '1.0.0',
  title: '统计问卷',
  description: '统计描述',
  questions: [
    {
      id: 'q1',
      type: 'single',
      title: '单选题',
      options: [
        { id: 'a', label: '选项A', value: 'a' },
        { id: 'b', label: '选项B', value: 'b' },
      ],
    },
    { id: 'q2', type: 'rating', title: '评分题' },
    { id: 'q3', type: 'textarea', title: '文本题' },
    { id: 'q4', type: 'text', title: '无数据题' },
    {
      id: 'q5',
      type: 'multiple',
      title: '多选题',
      options: [
        { id: 'c', label: '甲', value: 'c' },
        { id: 'd', label: '乙', value: 'd' },
      ],
    },
  ],
}

const stats: SurveyStats = {
  surveyId: 'stats-survey',
  totalSubmissions: 10,
  uniqueUsers: 8,
  lastSubmissionAt: '2026-07-01T00:00:00.000Z',
  questionStats: {
    q1: { answered: 10, optionCounts: { a: 6, b: 4 } },
    q2: { answered: 5, average: 4.5 },
    q3: { answered: 3, sampleAnswers: ['很好用'] },
  },
}

const submission: StoredSubmission = {
  id: 'sub-42',
  surveyId: 'stats-survey',
  surveyVersion: '1.0.0',
  submittedAt: '2026-06-15T08:00:00.000Z',
  answers: [
    { questionId: 'q1', value: 'a' }, // 字符串且能匹配选项 → 显示选项标签
    { questionId: 'q2', value: 4 }, // 数字 → 转字符串
    { questionId: 'q3', value: '自由发挥' }, // 字符串但无选项 → 原样展示
    { questionId: 'q5', value: ['c', 'd'] }, // 数组 → 标签用顿号连接
    { questionId: 'ghost', value: '幽灵答案' }, // 配置中不存在的问题 → 跳过
  ],
}

beforeEach(() => {
  vi.mocked(surveyApi.getSurveyStats).mockResolvedValue({ success: true, stats })
  vi.mocked(surveyApi.getUserSubmissions).mockResolvedValue({ success: true, submissions: [] })
})

describe('SurveyResults 统计展示', () => {
  it('加载中显示旋转指示', () => {
    vi.mocked(surveyApi.getSurveyStats).mockReturnValue(new Promise<SurveyStatsResponse>(() => {}))
    const { container } = render(<SurveyResults config={config} />)
    expect(container.querySelector('.animate-spin')).toBeInTheDocument()
    expect(screen.queryByText('总提交数')).not.toBeInTheDocument()
  })

  it('渲染概览统计与各题型统计', async () => {
    render(<SurveyResults config={config} />)
    expect(await screen.findByText('统计问卷 - 统计结果')).toBeInTheDocument()
    expect(screen.getByText('统计描述')).toBeInTheDocument()

    // 概览：总提交数 / 独立用户 / 最后提交
    expect(screen.getByText('总提交数')).toBeInTheDocument()
    expect(screen.getByText('10')).toBeInTheDocument()
    expect(screen.getByText('8')).toBeInTheDocument()
    expect(
      screen.getByText(new Date('2026-07-01T00:00:00.000Z').toLocaleDateString())
    ).toBeInTheDocument()

    // 选择题：选项计数与百分比
    expect(screen.getByText('回答人数：10')).toBeInTheDocument()
    expect(screen.getByText('6 (60.0%)')).toBeInTheDocument()
    expect(screen.getByText('4 (40.0%)')).toBeInTheDocument()

    // 评分题：平均分保留两位小数
    expect(screen.getByText('平均分：4.50')).toBeInTheDocument()

    // 文本题：样本答案带引号展示
    expect(screen.getByText('"很好用"')).toBeInTheDocument()

    // 没有统计数据的问题显示暂无数据（q4 与 q5 均无数据）
    expect(screen.getAllByText('暂无数据')).toHaveLength(2)
  })

  it('统计获取失败时概览回退为默认值', async () => {
    vi.mocked(surveyApi.getSurveyStats).mockResolvedValue({ success: false, error: '后端出错' })
    render(<SurveyResults config={config} />)
    await screen.findByText('总提交数')
    // totalSubmissions 与 uniqueUsers 均显示 0，最后提交显示占位符
    expect(screen.getAllByText('0')).toHaveLength(2)
    expect(screen.getByText('-')).toBeInTheDocument()
  })

  it('统计接口抛出异常时显示错误信息', async () => {
    vi.mocked(surveyApi.getSurveyStats).mockRejectedValue(new Error('加载数据失败了'))
    render(<SurveyResults config={config} />)
    expect(await screen.findByText('加载数据失败了')).toBeInTheDocument()
    expect(screen.queryByText('总提交数')).not.toBeInTheDocument()
  })
})

describe('SurveyResults 我的提交', () => {
  it('无提交记录时给出空态提示', async () => {
    const user = userEvent.setup()
    render(<SurveyResults config={config} />)
    await screen.findByText('总提交数')
    expect(surveyApi.getUserSubmissions).toHaveBeenCalledWith('stats-survey')
    await user.click(screen.getByRole('tab', { name: '我的提交' }))
    expect(await screen.findByText('你还没有提交过这份问卷')).toBeInTheDocument()
  })

  it('按答案类型格式化展示提交记录', async () => {
    vi.mocked(surveyApi.getUserSubmissions).mockResolvedValue({
      success: true,
      submissions: [submission],
    })
    const user = userEvent.setup()
    render(<SurveyResults config={config} />)
    await screen.findByText('总提交数')
    await user.click(screen.getByRole('tab', { name: '我的提交' }))

    expect(await screen.findByText('ID: sub-42')).toBeInTheDocument()
    expect(
      screen.getByText(new Date('2026-06-15T08:00:00.000Z').toLocaleString())
    ).toBeInTheDocument()
    // 字符串答案匹配选项 → 展示标签
    expect(screen.getByText('选项A')).toBeInTheDocument()
    // 数字答案 → 转为字符串
    expect(screen.getByText('4')).toBeInTheDocument()
    // 无选项的字符串答案 → 原样展示
    expect(screen.getByText('自由发挥')).toBeInTheDocument()
    // 数组答案 → 标签以顿号连接
    expect(screen.getByText('甲、乙')).toBeInTheDocument()
    // 配置中不存在的问题被跳过
    expect(screen.queryByText('幽灵答案')).not.toBeInTheDocument()
  })

  it('showUserSubmissions 关闭时不请求提交记录也不渲染标签页', async () => {
    render(<SurveyResults config={config} showUserSubmissions={false} />)
    await screen.findByText('总提交数')
    expect(surveyApi.getUserSubmissions).not.toHaveBeenCalled()
    expect(screen.queryByRole('tab', { name: '我的提交' })).not.toBeInTheDocument()
  })
})
