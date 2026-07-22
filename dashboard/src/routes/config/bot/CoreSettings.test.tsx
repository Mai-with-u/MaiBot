import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { CoreSettings } from './CoreSettings'

const botSection = {
  nickname: '麦麦',
}

const personalitySection = {
  behavior_style: '先观察，再行动。',
  multiple_probability: 0,
  personality: '你是一个温和的人。',
  reply_style: '说话简短自然。',
}

describe('CoreSettings', () => {
  it('醒目展示三个核心字段及其运行阶段', () => {
    render(
      <CoreSettings
        botSection={botSection}
        personalitySection={personalitySection}
        onPersonalitySectionChange={vi.fn()}
      />
    )

    expect(screen.getByRole('heading', { name: '麦麦' })).toBeInTheDocument()
    expect(screen.getByText('说话 · replyer')).toBeInTheDocument()
    expect(screen.getByText('行动 · planner')).toBeInTheDocument()
    expect(screen.getByLabelText('人格配置')).toHaveValue('你是一个温和的人。')
    expect(screen.getByLabelText('表达方式')).toHaveValue('说话简短自然。')
    expect(screen.getByLabelText('行为风格')).toHaveValue('先观察，再行动。')
  })

  it('编辑字段时保留同一配置节的其他值', () => {
    const handleChange = vi.fn()
    render(
      <CoreSettings
        botSection={botSection}
        personalitySection={personalitySection}
        onPersonalitySectionChange={handleChange}
      />
    )

    fireEvent.change(screen.getByLabelText('行为风格'), {
      target: { value: '只在适合的时候参与。' },
    })

    expect(handleChange).toHaveBeenCalledWith({
      ...personalitySection,
      behavior_style: '只在适合的时候参与。',
    })
  })
})
