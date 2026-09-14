import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '../dropdown-menu'

/** jsdom 未实现 Pointer Capture，Radix 下拉菜单打开时会调用 */
function stubPointerCapture() {
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {}
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {}
  }
}

function renderOpenedMenu(onSelect: () => void = () => {}) {
  const view = render(
    <DropdownMenu open>
      <DropdownMenuTrigger>打开菜单</DropdownMenuTrigger>
      <DropdownMenuContent className="extra-content">
        <DropdownMenuLabel inset>菜单标题</DropdownMenuLabel>
        <DropdownMenuGroup>
          <DropdownMenuItem inset onSelect={onSelect}>
            复制
            <DropdownMenuShortcut className="extra-shortcut">⌘C</DropdownMenuShortcut>
          </DropdownMenuItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator className="extra-separator" />
        <DropdownMenuCheckboxItem checked>显示网格</DropdownMenuCheckboxItem>
        <DropdownMenuCheckboxItem checked={false}>隐藏边框</DropdownMenuCheckboxItem>
        <DropdownMenuRadioGroup value="b">
          <DropdownMenuRadioItem value="a">选项 A</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="b">选项 B</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )

  return view
}

describe('DropdownMenu 包装层', () => {
  beforeEach(() => {
    stubPointerCapture()
  })

  it('打开后渲染菜单内容并带浮层标记与自定义类名', () => {
    renderOpenedMenu()

    const menu = screen.getByRole('menu')
    expect(menu).toHaveAttribute('data-dashboard-floating-content', 'true')
    expect(menu).toHaveClass('extra-content', 'bg-popover')
  })

  it('Label / Item 的 inset 属性追加 pl-8；未设置时不加缩进', () => {
    render(
      <DropdownMenu open>
        <DropdownMenuTrigger>打开菜单</DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuLabel>普通标题</DropdownMenuLabel>
          <DropdownMenuLabel inset={false}>显式非缩进标题</DropdownMenuLabel>
          <DropdownMenuItem>普通项</DropdownMenuItem>
          <DropdownMenuItem inset={false}>显式非缩进项</DropdownMenuItem>
          <DropdownMenuItem inset>缩进项</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    )

    expect(screen.getByText('普通标题')).not.toHaveClass('pl-8')
    expect(screen.getByText('显式非缩进标题')).not.toHaveClass('pl-8')
    expect(screen.getByRole('menuitem', { name: '普通项' })).not.toHaveClass('pl-8')
    expect(screen.getByRole('menuitem', { name: '显式非缩进项' })).not.toHaveClass('pl-8')
    expect(screen.getByRole('menuitem', { name: '缩进项' })).toHaveClass('pl-8')
    expect(screen.getByText('普通标题')).toHaveClass('font-semibold')
  })

  it('Shortcut 渲染为右对齐快捷键并合并 className', () => {
    renderOpenedMenu()

    const shortcut = screen.getByText('⌘C')
    expect(shortcut.tagName).toBe('SPAN')
    expect(shortcut).toHaveClass('ml-auto', 'tracking-widest', 'extra-shortcut')
  })

  it('Separator 渲染为分隔线并合并 className', () => {
    renderOpenedMenu()

    const separator = document.querySelector('[role="separator"]')
    expect(separator).not.toBeNull()
    expect(separator).toHaveClass('extra-separator', 'bg-muted')
  })

  it('CheckboxItem 勾选时展示 Check，未勾选不展示', () => {
    renderOpenedMenu()

    const checked = screen.getByRole('menuitemcheckbox', { name: '显示网格' })
    const unchecked = screen.getByRole('menuitemcheckbox', { name: '隐藏边框' })
    expect(checked).toHaveAttribute('aria-checked', 'true')
    expect(unchecked).toHaveAttribute('aria-checked', 'false')
    expect(checked.querySelector('.lucide-check')).not.toBeNull()
    expect(unchecked.querySelector('.lucide-check')).toBeNull()
  })

  it('RadioItem 仅选中项展示 Circle 指示器', () => {
    renderOpenedMenu()

    const optionA = screen.getByRole('menuitemradio', { name: '选项 A' })
    const optionB = screen.getByRole('menuitemradio', { name: '选项 B' })
    expect(optionA).toHaveAttribute('aria-checked', 'false')
    expect(optionB).toHaveAttribute('aria-checked', 'true')
    expect(optionA.querySelector('.lucide-circle')).toBeNull()
    expect(optionB.querySelector('.lucide-circle')).not.toBeNull()
  })

  it('点击菜单项触发 onSelect 回调', () => {
    const onSelect = vi.fn()
    renderOpenedMenu(onSelect)

    fireEvent.click(screen.getByRole('menuitem', { name: /复制/ }))
    expect(onSelect).toHaveBeenCalledTimes(1)
  })

  it('可覆盖 Content 的 sideOffset', () => {
    render(
      <DropdownMenu open>
        <DropdownMenuTrigger>打开菜单</DropdownMenuTrigger>
        <DropdownMenuContent sideOffset={16} className="offset-content">
          <DropdownMenuItem>一项</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    )

    expect(screen.getByRole('menu')).toHaveClass('offset-content')
  })

  it('SubTrigger 默认无缩进；inset 时追加 pl-8，方向键展开子菜单', () => {
    render(
      <DropdownMenu open>
        <DropdownMenuTrigger>打开菜单</DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>更多操作</DropdownMenuSubTrigger>
            <DropdownMenuSubContent className="sub-extra">
              <DropdownMenuItem>子项</DropdownMenuItem>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
          <DropdownMenuSub>
            <DropdownMenuSubTrigger inset>缩进更多</DropdownMenuSubTrigger>
            <DropdownMenuSubContent>
              <DropdownMenuItem>另一子项</DropdownMenuItem>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        </DropdownMenuContent>
      </DropdownMenu>
    )

    const plainTrigger = screen.getByText('更多操作')
    const insetTrigger = screen.getByText('缩进更多')
    expect(plainTrigger).not.toHaveClass('pl-8')
    expect(insetTrigger).toHaveClass('pl-8')
    expect(plainTrigger.querySelector('.lucide-chevron-right')).not.toBeNull()

    fireEvent.keyDown(plainTrigger, { key: 'ArrowRight' })

    const subItem = screen.getByRole('menuitem', { name: '子项' })
    const subContent = subItem.closest('[data-dashboard-floating-content="true"]')
    expect(subContent).not.toBeNull()
    expect(subContent).toHaveClass('sub-extra')
  })

  it('关闭态不渲染菜单内容', () => {
    render(
      <DropdownMenu>
        <DropdownMenuTrigger>打开菜单</DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem>隐藏项</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    )

    expect(screen.queryByRole('menuitem', { name: '隐藏项' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '打开菜单' })).toBeInTheDocument()
  })
})
