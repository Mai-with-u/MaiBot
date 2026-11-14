import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Dialog, DialogContent, DialogTitle } from '../ui/dialog'

afterEach(cleanup)

describe('DialogContent Enter 快捷确认', () => {
  it.each([false, true])('skipConfirm=%s 时仅显式退出的控件使用自己的 Enter 动作', async (skipConfirm) => {
    const user = userEvent.setup()
    const onConfirm = vi.fn()
    const onHelp = vi.fn()
    render(
      <Dialog open>
        <DialogContent confirmOnEnter aria-describedby={undefined}>
          <DialogTitle>键盘操作</DialogTitle>
          <button type="button" data-dialog-skip-confirm={skipConfirm ? '' : undefined} onClick={onHelp}>
            说明
          </button>
          <button type="button" data-dialog-action="confirm" onClick={onConfirm}>保存</button>
        </DialogContent>
      </Dialog>,
    )

    screen.getByRole('button', { name: '说明' }).focus()
    await user.keyboard('{Enter}')

    expect(onHelp).toHaveBeenCalledTimes(skipConfirm ? 1 : 0)
    expect(onConfirm).toHaveBeenCalledTimes(skipConfirm ? 0 : 1)
  })
})
