import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PromptManagementPage } from '../prompts'
import * as promptApi from '@/lib/prompt-api'

const toastMock = vi.fn()

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))
vi.mock('@/components/CodeEditor', () => ({
  CodeEditor: ({
    value,
    onChange,
    readOnly,
  }: {
    value: string
    onChange?: (value: string) => void
    readOnly?: boolean
  }) => (
    <textarea
      aria-label={readOnly ? '只读编辑器' : '可编辑编辑器'}
      data-testid={readOnly ? 'readonly-code-editor' : 'editable-code-editor'}
      readOnly={readOnly}
      value={value}
      onChange={(event) => onChange?.(event.target.value)}
    />
  ),
}))
vi.mock('@/lib/prompt-api', () => ({
  activatePromptVersion: vi.fn(),
  getDefaultPromptFile: vi.fn(),
  getPromptCatalog: vi.fn(),
  getPromptFile: vi.fn(),
  getPromptVersionFile: vi.fn(),
  resetPromptFile: vi.fn(),
  updatePromptFile: vi.fn(),
}))

const catalog = {
  success: true,
  languages: ['zh-CN'],
  files: {
    'zh-CN': [
      {
        name: 'first.prompt',
        size: 13,
        modified_at: 1,
        display_name: '第一 Prompt',
        advanced: false,
        description: '',
        customized: true,
        custom_version_count: 0,
      },
      {
        name: 'second.prompt',
        size: 14,
        modified_at: 2,
        display_name: '第二 Prompt',
        advanced: false,
        description: '',
        customized: true,
        custom_version_count: 0,
      },
    ],
  },
}

function makePromptContent(filename: string, content: string) {
  return {
    success: true,
    language: 'zh-CN',
    filename,
    content,
    customized: true,
    active_version_id: null,
    versions: [],
    validation: {
      valid: true,
      missing_placeholders: [],
      extra_placeholders: [],
      message: '',
    },
  }
}

beforeEach(() => {
  vi.mocked(promptApi.getPromptCatalog).mockResolvedValue(catalog)
  vi.mocked(promptApi.getPromptFile).mockImplementation((_, filename) =>
    Promise.resolve(makePromptContent(filename, `${filename} current`))
  )
  vi.mocked(promptApi.getDefaultPromptFile).mockImplementation((_, filename) =>
    Promise.resolve(makePromptContent(filename, `${filename} default`))
  )
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  window.localStorage.clear()
})

describe('PromptManagementPage', () => {
  it('对比默认模式下切换 Prompt 文件会刷新默认版本面板', async () => {
    const user = userEvent.setup()

    render(<PromptManagementPage />)

    await screen.findByDisplayValue('first.prompt current')
    await user.click(screen.getByRole('button', { name: /对比默认/ }))

    await screen.findByDisplayValue('first.prompt default')
    await user.click(screen.getByRole('button', { name: /第二 Prompt/ }))

    await screen.findByDisplayValue('second.prompt current')
    await waitFor(() =>
      expect(screen.getByTestId('readonly-code-editor')).toHaveValue('second.prompt default')
    )
  })
})
