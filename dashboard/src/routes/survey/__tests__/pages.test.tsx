import type { SurveyConfig } from '@/types/survey'

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { APP_VERSION } from '@/lib/version'
import { MaiBotFeedbackSurveyPage } from '../maibot-feedback'
import { WebUIFeedbackSurveyPage } from '../webui-feedback'
import * as systemApi from '@/lib/system-api'

const { surveyState } = vi.hoisted(() => ({
  surveyState: {
    onSubmitError: undefined as undefined | ((message: string) => void),
  },
}))

vi.mock('@/lib/system-api', () => ({
  getMaiBotStatus: vi.fn(),
}))

vi.mock('@/components/survey', () => ({
  SurveyRenderer: ({
    config,
    initialAnswers,
    onSubmitError,
    showProgress,
    paginateQuestions,
  }: {
    config: SurveyConfig
    initialAnswers: Array<{ questionId: string; value: unknown }>
    onSubmitError: (message: string) => void
    showProgress: boolean
    paginateQuestions: boolean
  }) => {
    surveyState.onSubmitError = onSubmitError
    return (
      <div
        data-testid="survey-renderer"
        data-config-id={config.id}
        data-show-progress={String(showProgress)}
        data-paginate={String(paginateQuestions)}
      >
        {initialAnswers.map((answer) => (
          <span key={answer.questionId}>
            {answer.questionId}:{String(answer.value)}
          </span>
        ))}
        <button type="button" onClick={() => onSubmitError('提交失败')}>
          触发提交错误
        </button>
      </div>
    )
  },
}))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

beforeEach(() => {
  vi.mocked(systemApi.getMaiBotStatus).mockResolvedValue({
    running: true,
    uptime: 120,
    version: '2.3.4',
    start_time: '2026-07-26T00:00:00Z',
  })
})

describe('反馈问卷页面', () => {
  it('麦麦问卷获取版本并作为初始答案传入', async () => {
    render(<MaiBotFeedbackSurveyPage />)

    expect(await screen.findByText('maibot_version:2.3.4')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '麦麦使用体验反馈问卷' })).toBeInTheDocument()
    expect(screen.getByTestId('survey-renderer')).toHaveAttribute('data-show-progress', 'true')
    expect(screen.getByTestId('survey-renderer')).toHaveAttribute('data-paginate', 'false')
  })

  it('麦麦版本请求失败时把获取失败写入初始答案', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.mocked(systemApi.getMaiBotStatus).mockRejectedValue(new Error('离线'))
    render(<MaiBotFeedbackSurveyPage />)

    expect(await screen.findByText('maibot_version:获取失败')).toBeInTheDocument()
    expect(console.error).toHaveBeenCalledWith('Failed to get MaiBot version:', expect.any(Error))
  })

  it('WebUI 问卷使用当前应用版本并保持原始配置不可变', () => {
    render(<WebUIFeedbackSurveyPage />)

    expect(screen.getByText(`webui_version:v${APP_VERSION}`)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'WebUI 使用反馈问卷' })).toBeInTheDocument()
  })

  it('两个页面都把提交错误交给可观测日志', async () => {
    const user = userEvent.setup()
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const { rerender } = render(<WebUIFeedbackSurveyPage />)

    await user.click(screen.getByRole('button', { name: '触发提交错误' }))
    expect(errorSpy).toHaveBeenCalledWith('WebUI Survey submission error:', '提交失败')

    rerender(<MaiBotFeedbackSurveyPage />)
    await waitFor(() => expect(screen.getByText(/maibot_version:/)).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: '触发提交错误' }))
    expect(errorSpy).toHaveBeenCalledWith('MaiBot Survey submission error:', '提交失败')
  })

  it('麦麦问卷初始化期间显示加载指示器，不渲染标题', () => {
    vi.mocked(systemApi.getMaiBotStatus).mockImplementation(() => new Promise(() => {}))
    render(<MaiBotFeedbackSurveyPage />)

    expect(document.querySelector('.animate-spin')).not.toBeNull()
    expect(screen.queryByRole('heading', { name: '麦麦使用体验反馈问卷' })).not.toBeInTheDocument()
  })

  it('麦麦版本为空字符串时写入未知版本', async () => {
    vi.mocked(systemApi.getMaiBotStatus).mockResolvedValue({
      running: true,
      uptime: 1,
      version: '',
      start_time: '2026-07-26T00:00:00Z',
    })
    render(<MaiBotFeedbackSurveyPage />)

    expect(await screen.findByText('maibot_version:未知版本')).toBeInTheDocument()
  })

  it('问卷配置解析失败时展示错误空态与重试按钮', async () => {
    vi.spyOn(JSON, 'parse').mockReturnValue(null)
    render(<MaiBotFeedbackSurveyPage />)

    expect(await screen.findByText('无法加载问卷配置')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
    expect(screen.queryByTestId('survey-renderer')).not.toBeInTheDocument()
  })

  it('WebUI 问卷配置解析失败时展示错误空态', () => {
    vi.spyOn(JSON, 'parse').mockReturnValue(null)
    render(<WebUIFeedbackSurveyPage />)

    expect(screen.getByText('无法加载问卷配置')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
  })

  it('WebUI 问卷深拷贝配置，不修改原始对象', async () => {
    const { webuiFeedbackSurvey } = await import('@/config/surveys')
    const snapshot = structuredClone(webuiFeedbackSurvey)

    render(<WebUIFeedbackSurveyPage />)

    expect(webuiFeedbackSurvey).toEqual(snapshot)
    expect(screen.getByText(`webui_version:v${APP_VERSION}`)).toBeInTheDocument()
  })
})
