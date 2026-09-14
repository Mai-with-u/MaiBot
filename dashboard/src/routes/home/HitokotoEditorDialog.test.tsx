import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { HitokotoEditorDialog } from './HitokotoEditorDialog'
import type { HitokotoSettings } from './hooks/useMaibotVersion'

vi.mock('react-i18next', () => {
  const t = (key: string) => key
  return { useTranslation: () => ({ t }) }
})

function createDeferred<T>() {
  let resolve: (value: T | PromiseLike<T>) => void = () => {}
  let reject: (reason?: unknown) => void = () => {}
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

const emptySettings: HitokotoSettings = {
  defaultEnabled: true,
  customItems: [],
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('HitokotoEditorDialog', () => {
  it('打开时展示标题、默认来源开关与空列表', () => {
    render(
      <HitokotoEditorDialog
        initialSettings={emptySettings}
        onOpenChange={vi.fn()}
        onSave={vi.fn()}
      />
    )

    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('home.hitokoto.editor.title')).toBeInTheDocument()
    expect(screen.getByText('home.hitokoto.editor.description')).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'home.hitokoto.editor.defaultSource' })).toBeChecked()
    expect(
      screen.getByRole('button', { name: /home\.hitokoto\.editor\.empty/ })
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'common.cancel' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'common.save' })).toBeInTheDocument()
  })

  it('取消关闭对话框且不保存', async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    const onSave = vi.fn()
    render(
      <HitokotoEditorDialog
        initialSettings={emptySettings}
        onOpenChange={onOpenChange}
        onSave={onSave}
      />
    )

    await user.click(screen.getByRole('button', { name: 'common.cancel' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
    expect(onSave).not.toHaveBeenCalled()
  })

  it('保存当前设置后关闭', async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(
      <HitokotoEditorDialog
        initialSettings={emptySettings}
        onOpenChange={onOpenChange}
        onSave={onSave}
      />
    )

    await user.click(screen.getByRole('button', { name: 'common.save' }))
    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith({
        defaultEnabled: true,
        customItems: [],
      })
    })
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('保存中禁用按钮，完成后恢复', async () => {
    const user = userEvent.setup()
    const deferred = createDeferred<void>()
    const onOpenChange = vi.fn()
    const onSave = vi.fn().mockReturnValue(deferred.promise)
    render(
      <HitokotoEditorDialog
        initialSettings={emptySettings}
        onOpenChange={onOpenChange}
        onSave={onSave}
      />
    )

    await user.click(screen.getByRole('button', { name: 'common.save' }))
    expect(screen.getByRole('button', { name: 'home.hitokoto.editor.saving' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'common.cancel' })).toBeDisabled()
    expect(onOpenChange).not.toHaveBeenCalled()

    await act(async () => {
      deferred.resolve()
      await deferred.promise
    })
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('可关闭默认来源、新增并编辑自定义内容后保存', async () => {
    const user = userEvent.setup()
    const onSave = vi.fn().mockResolvedValue(undefined)
    const initialSettings: HitokotoSettings = {
      defaultEnabled: true,
      customItems: [{ id: 'keep', content: '原句', source: '原出处' }],
    }
    render(
      <HitokotoEditorDialog
        initialSettings={initialSettings}
        onOpenChange={vi.fn()}
        onSave={onSave}
      />
    )

    await user.click(screen.getByRole('switch', { name: 'home.hitokoto.editor.defaultSource' }))
    expect(
      screen.getByRole('switch', { name: 'home.hitokoto.editor.defaultSource' })
    ).not.toBeChecked()

    await user.clear(screen.getByRole('textbox', { name: 'home.hitokoto.editor.content' }))
    await user.type(
      screen.getByRole('textbox', { name: 'home.hitokoto.editor.content' }),
      '改写的一言'
    )
    await user.clear(screen.getByRole('textbox', { name: 'home.hitokoto.editor.source' }))
    await user.type(screen.getByRole('textbox', { name: 'home.hitokoto.editor.source' }), '新出处')
    await user.click(screen.getByRole('button', { name: /home\.hitokoto\.editor\.add/ }))
    await user.type(
      screen.getAllByRole('textbox', { name: 'home.hitokoto.editor.content' })[1],
      '第二条'
    )
    await user.type(
      screen.getAllByRole('textbox', { name: 'home.hitokoto.editor.source' })[1],
      '二号'
    )

    await user.click(screen.getByRole('button', { name: 'common.save' }))
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1))
    expect(onSave).toHaveBeenCalledWith({
      defaultEnabled: false,
      customItems: [
        { id: 'keep', content: '改写的一言', source: '新出处' },
        { id: expect.any(String), content: '第二条', source: '二号' },
      ],
    })
    expect(initialSettings.customItems[0]).toEqual({
      id: 'keep',
      content: '原句',
      source: '原出处',
    })
  })

  it('可从空态添加并删除自定义内容，关闭默认来源时展示留白提示', async () => {
    const user = userEvent.setup()
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(
      <HitokotoEditorDialog
        initialSettings={emptySettings}
        onOpenChange={vi.fn()}
        onSave={onSave}
      />
    )

    await user.click(screen.getByRole('switch', { name: 'home.hitokoto.editor.defaultSource' }))
    expect(screen.getByText('home.hitokoto.editor.blankHint')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /home\.hitokoto\.editor\.empty/ }))
    expect(screen.queryByText('home.hitokoto.editor.blankHint')).not.toBeInTheDocument()
    expect(
      screen.getByRole('textbox', { name: 'home.hitokoto.editor.content' })
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'home.hitokoto.editor.remove' }))
    expect(screen.getByText('home.hitokoto.editor.blankHint')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'common.save' }))
    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith({
        defaultEnabled: false,
        customItems: [],
      })
    })
  })
})
