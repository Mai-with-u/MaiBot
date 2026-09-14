import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { HelpTooltip } from '../help-tooltip'

describe('HelpTooltip', () => {
  it('渲染帮助按钮、默认图标尺寸与读屏文案', () => {
    render(<HelpTooltip content="默认帮助" />)

    const trigger = screen.getByRole('button', { name: '帮助信息' })
    expect(trigger).toHaveAttribute('type', 'button')
    expect(trigger).toHaveClass('cursor-help', 'text-muted-foreground')
    expect(trigger.querySelector('svg')).toHaveClass('h-4', 'w-4')
  })

  it('点击触发器会 preventDefault，避免触发表单提交', () => {
    render(
      <form>
        <HelpTooltip content="表单帮助" />
      </form>
    )

    const trigger = screen.getByRole('button', { name: '帮助信息' })
    const click = new MouseEvent('click', { bubbles: true, cancelable: true })
    expect(trigger.dispatchEvent(click)).toBe(false)
    expect(click.defaultPrevented).toBe(true)
  })

  it('合并触发器与图标的自定义 className', () => {
    render(
      <HelpTooltip content="样式帮助" className="extra-trigger" iconClassName="extra-icon" />
    )

    const trigger = screen.getByRole('button', { name: '帮助信息' })
    expect(trigger).toHaveClass('extra-trigger', 'inline-flex')
    expect(trigger.querySelector('svg')).toHaveClass('extra-icon', 'h-4')
  })

  it('悬停后以默认 side/align/maxWidth 展示内容', async () => {
    const user = userEvent.setup()
    render(<HelpTooltip content="默认提示正文" />)

    await user.hover(screen.getByRole('button', { name: '帮助信息' }))
    expect(await screen.findByRole('tooltip')).toHaveTextContent('默认提示正文')

    const tooltip = document.querySelector('[data-dashboard-floating-content="true"]')
    expect(tooltip).not.toBeNull()
    expect(tooltip).toHaveAttribute('data-side', 'top')
    expect(tooltip).toHaveAttribute('data-align', 'center')
    expect((tooltip as HTMLElement).style.getPropertyValue('--max-width')).toBe('300px')
    expect(tooltip).toHaveClass('max-w-[var(--max-width)]', 'border-primary')
  })

  it('可覆盖 side、align 与 maxWidth', async () => {
    const user = userEvent.setup()
    render(
      <HelpTooltip content="右侧提示" side="right" align="start" maxWidth="160px" />
    )

    await user.hover(screen.getByRole('button', { name: '帮助信息' }))
    expect(await screen.findByRole('tooltip')).toHaveTextContent('右侧提示')

    const tooltip = document.querySelector('[data-dashboard-floating-content="true"]')
    expect(tooltip).not.toBeNull()
    expect(tooltip).toHaveAttribute('data-side', 'right')
    expect(tooltip).toHaveAttribute('data-align', 'start')
    expect((tooltip as HTMLElement).style.getPropertyValue('--max-width')).toBe('160px')
  })

  it('fireEvent.click 也会走到 preventDefault 处理函数', () => {
    render(<HelpTooltip content="点击帮助" />)
    fireEvent.click(screen.getByRole('button', { name: '帮助信息' }))
    expect(screen.getByRole('button', { name: '帮助信息' })).toBeInTheDocument()
  })
})
