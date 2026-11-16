import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { getPromptFile, type PromptFileContent, type PromptFileInfo } from '@/lib/prompt-api'
import { PromptSideBySide } from '../PromptSideBySide'

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))
vi.mock('@/lib/prompt-api', () => ({ getPromptFile: vi.fn() }))
vi.mock('../PromptBlockEditor', () => ({
  PromptBlockEditor: ({
    value,
    readOnly,
    onChange,
  }: {
    value: string
    readOnly?: boolean
    onChange?: (value: string) => void
  }) => (
    <textarea
      aria-label="prompt-preview"
      value={value}
      readOnly={readOnly}
      onChange={(event) => onChange?.(event.target.value)}
    />
  ),
}))
afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

const files = ['a', 'b', 'c'].map(
  (name) =>
    ({
      name: `${name}.prompt`,
      display_name: name,
      relative_path: `main/${name}.prompt`,
    }) as PromptFileInfo
)
const props = {
  language: 'zh-CN',
  files,
  filename: 'a.prompt',
  content: 'unsaved-a',
  onChange: vi.fn(),
  rightFilename: '',
  onEditTemplate: () => true,
}

describe('inline side-by-side Prompt viewing', () => {
  it('waits for a directory selection and preserves the editable left draft', async () => {
    vi.mocked(getPromptFile).mockResolvedValue({ content: 'applied-b' } as PromptFileContent)
    const view = render(<PromptSideBySide {...props} />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByDisplayValue('unsaved-a')).not.toHaveAttribute('readonly')
    expect(getPromptFile).not.toHaveBeenCalled()
    view.rerender(<PromptSideBySide {...props} rightFilename="b.prompt" />)
    expect(await screen.findByDisplayValue('applied-b')).toHaveAttribute('readonly')
    expect(screen.getByDisplayValue('unsaved-a')).toBeInTheDocument()
    expect(getPromptFile).toHaveBeenCalledWith('zh-CN', 'b.prompt')
  })

  it('ignores an earlier response after a new directory selection', async () => {
    let resolveEarlier!: (value: PromptFileContent) => void
    vi.mocked(getPromptFile).mockImplementation((_language, filename) =>
      filename === 'b.prompt'
        ? new Promise((resolve) => {
            resolveEarlier = resolve
          })
        : Promise.resolve({ content: 'applied-c' } as PromptFileContent)
    )
    const view = render(<PromptSideBySide {...props} rightFilename="b.prompt" />)
    view.rerender(<PromptSideBySide {...props} rightFilename="c.prompt" />)
    expect(await screen.findByDisplayValue('applied-c')).toBeInTheDocument()
    await act(async () => resolveEarlier({ content: 'stale-b' } as PromptFileContent))
    expect(screen.queryByDisplayValue('stale-b')).not.toBeInTheDocument()
    expect(screen.getByDisplayValue('applied-c')).toBeInTheDocument()
  })
})
