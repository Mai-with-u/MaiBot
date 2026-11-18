import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { PromptBlockEditor } from '../PromptBlockEditor'
import { getPromptBlockInstructions, getPromptFile, type PromptFileInfo } from '@/lib/prompt-api'

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))
vi.mock('@/components/CodeEditor', () => ({
  CodeEditor: ({
    onPlaceholderClick,
  }: {
    onPlaceholderClick: (value: { name: string; x: number; y: number }) => void
  }) => (
    <div>
      {['reply_guidance', 'dialogue_prompt', 'chat_anlyze_block'].map((name) => (
        <button key={name} onClick={() => onPlaceholderClick({ name, x: 100, y: 100 })}>
          {name}
        </button>
      ))}
    </div>
  ),
}))
vi.mock('@/lib/prompt-api', () => ({ getPromptBlockInstructions: vi.fn(), getPromptFile: vi.fn() }))
afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

describe('prompt block inspection', () => {
  it('loads actual instructions in the template language', async () => {
    vi.mocked(getPromptBlockInstructions).mockResolvedValue({
      reply_guidance: { 长回复: 'Actual long reply instruction' },
    })
    render(
      <PromptBlockEditor
        value="{reply_guidance}"
        promptLanguage="ja-JP"
        files={[]}
        onEditTemplate={() => true}
      />
    )
    fireEvent.click(screen.getByText('reply_guidance'))
    expect(await screen.findByText('Actual long reply instruction')).toBeInTheDocument()
    expect(getPromptBlockInstructions).toHaveBeenCalledWith('ja-JP', undefined)
  })

  it('explains dynamic history with an example without loading a request', () => {
    render(
      <PromptBlockEditor
        value="{dialogue_prompt}"
        promptLanguage="zh-CN"
        files={[]}
        onEditTemplate={() => true}
      />
    )
    fireEvent.click(screen.getByText('dialogue_prompt'))
    expect(screen.getByText('promptBlocks.descriptions.history')).toBeInTheDocument()
    expect(screen.getByText('promptBlocks.example')).toBeInTheDocument()
    expect(getPromptBlockInstructions).not.toHaveBeenCalled()
    expect(getPromptFile).not.toHaveBeenCalled()
  })

  it('previews the applied linked template and keeps the card if navigation is blocked', async () => {
    const file = {
      name: 'reply_conversation_analysis.prompt',
      display_name: 'Analysis',
      kind: 'instruction',
    } as PromptFileInfo
    vi.mocked(getPromptFile).mockResolvedValue({
      content: 'Applied custom template: {chat_anlyze}',
    } as Awaited<ReturnType<typeof getPromptFile>>)
    const navigate = vi.fn(() => false)
    render(
      <PromptBlockEditor
        value="{chat_anlyze_block}"
        promptLanguage="en-US"
        files={[file]}
        onEditTemplate={navigate}
      />
    )
    fireEvent.click(screen.getByText('chat_anlyze_block'))
    expect(await screen.findByText('Applied custom template: {chat_anlyze}')).toBeInTheDocument()
    fireEvent.click(screen.getByText('promptBlocks.edit'))
    expect(navigate).toHaveBeenCalledWith(file)
    expect(screen.getByText('Applied custom template: {chat_anlyze}')).toBeInTheDocument()
    navigate.mockReturnValue(true)
    fireEvent.click(screen.getByText('promptBlocks.edit'))
    await waitFor(() =>
      expect(screen.queryByText('Applied custom template: {chat_anlyze}')).not.toBeInTheDocument()
    )
  })
})
