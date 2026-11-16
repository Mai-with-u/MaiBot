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
  editDefaultPrompt: false,
  onSave: vi.fn(),
  saving: false,
  hasUnsavedChanges: true,
  saveLabel: 'Save',
  onRightStateChange: vi.fn(),
  onSaved: vi.fn(),
}

describe('inline side-by-side Prompt viewing', () => {
  it('waits for a directory selection and preserves the editable left draft', async () => {
    vi.mocked(getPromptFile).mockResolvedValue(promptContent('b.prompt', 'applied-b'))
    const view = render(<PromptSideBySide {...props} />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByDisplayValue('unsaved-a')).not.toHaveAttribute('readonly')
    expect(getPromptFile).not.toHaveBeenCalled()
    view.rerender(<PromptSideBySide {...props} rightFilename="b.prompt" />)
    expect(await screen.findByDisplayValue('applied-b')).not.toHaveAttribute('readonly')
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
        : Promise.resolve(promptContent('c.prompt', 'applied-c'))
    )
    const view = render(<PromptSideBySide {...props} rightFilename="b.prompt" />)
    view.rerender(<PromptSideBySide {...props} rightFilename="c.prompt" />)
    expect(await screen.findByDisplayValue('applied-c')).toBeInTheDocument()
    await act(async () => resolveEarlier(promptContent('b.prompt', 'stale-b')))
    expect(screen.queryByDisplayValue('stale-b')).not.toBeInTheDocument()
    expect(screen.getByDisplayValue('applied-c')).toBeInTheDocument()
  })
})

function promptContent(filename: string, content: string): PromptFileContent {
  return {
    success: true,
    language: 'zh-CN',
    filename,
    content,
    customized: false,
    active_version_id: null,
    versions: [],
    validation: { valid: true, missing_placeholders: [], extra_placeholders: [], message: '' },
  }
}
