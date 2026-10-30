import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PERSONALITY_IMPORT_EXAMPLE, parsePersonalityImport, readPersonalityImport, rememberPersonalityImport } from '@/lib/personality-import'
import { PersonalityProfiles } from './PersonalityProfiles'

const current = {
  ...parsePersonalityImport(PERSONALITY_IMPORT_EXAMPLE),
  personality: '当前使用的设定',
  profiles: [],
  selected_profile: '',
}

describe('personality import card', () => {
  beforeEach(() => sessionStorage.clear())
  afterEach(cleanup)

  it('only writes after preview, editing and explicit creation; does not activate imported profile', () => {
    const onChange = vi.fn()
    render(<PersonalityProfiles values={current} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: '导入' }))
    fireEvent.change(screen.getByLabelText('人设 JSON'), { target: { value: PERSONALITY_IMPORT_EXAMPLE } })
    expect(screen.getByText('已解析：温柔的麦麦')).toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '继续编辑' }))
    fireEvent.change(screen.getByLabelText('人设名称'), { target: { value: '导入后编辑' } })
    fireEvent.change(screen.getByLabelText('群聊提示词'), { target: { value: '导入群聊' } })
    fireEvent.change(screen.getByLabelText('私聊提示词'), { target: { value: '导入私聊' } })
    expect(onChange).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '创建人设' }))
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      personality: '当前使用的设定', selected_profile: '',
      profiles: [expect.objectContaining({ name: '导入后编辑', group_chat_prompt: '导入群聊', private_chat_prompts: '导入私聊' })],
    }))
  })

  it('opens retained external content and cancellation clears it without saving', () => {
    rememberPersonalityImport(new URLSearchParams({ 'personality-import': PERSONALITY_IMPORT_EXAMPLE }).toString())
    const onChange = vi.fn()
    render(<PersonalityProfiles values={current} onChange={onChange} />)
    expect(screen.getByLabelText('人设 JSON')).toHaveValue(PERSONALITY_IMPORT_EXAMPLE)
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(readPersonalityImport()).toBeNull()
    expect(onChange).not.toHaveBeenCalled()
  })

  it('shows validation errors and prevents continuing with invalid input', () => {
    render(<PersonalityProfiles values={current} onChange={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: '导入' }))
    fireEvent.change(screen.getByLabelText('人设 JSON'), { target: { value: '{"version":2}' } })
    expect(screen.getByRole('alert')).toHaveTextContent('仅支持')
    expect(screen.getByRole('button', { name: '继续编辑' })).toBeDisabled()
  })
})
