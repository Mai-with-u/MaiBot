import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ContextMenu,
  ContextMenuCheckboxItem,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuRadioGroup,
  ContextMenuRadioItem,
  ContextMenuSeparator,
  ContextMenuShortcut,
  ContextMenuSub,
  ContextMenuSubContent,
  ContextMenuSubTrigger,
  ContextMenuTrigger,
} from '../context-menu'

/** 通过右键事件打开一个包含常用子组件的菜单 */
function renderOpenedMenu(onSelect: () => void = () => {}) {
  const view = render(
    <ContextMenu>
      <ContextMenuTrigger>右键区域</ContextMenuTrigger>
      <ContextMenuContent className="extra-content">
        <ContextMenuLabel inset>菜单标题</ContextMenuLabel>
        <ContextMenuItem inset onSelect={onSelect}>
          复制
          <ContextMenuShortcut>⌘C</ContextMenuShortcut>
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuCheckboxItem checked>显示网格</ContextMenuCheckboxItem>
        <ContextMenuRadioGroup value="b">
          <ContextMenuRadioItem value="a">选项 A</ContextMenuRadioItem>
          <ContextMenuRadioItem value="b">选项 B</ContextMenuRadioItem>
        </ContextMenuRadioGroup>
      </ContextMenuContent>
    </ContextMenu>
  )

  fireEvent.contextMenu(screen.getByText('右键区域'))
  return view
}

describe('ContextMenu 包装层', () => {
  beforeEach(() => {
    // Radix 菜单在 jsdom 下依赖 pointer capture API，统一打桩
    if (!Element.prototype.hasPointerCapture) {
      Element.prototype.hasPointerCapture = () => false
    }
    if (!Element.prototype.setPointerCapture) {
      Element.prototype.setPointerCapture = () => {}
    }
    if (!Element.prototype.releasePointerCapture) {
      Element.prototype.releasePointerCapture = () => {}
    }
  })

  it('右键触发后渲染菜单内容并带浮层标记与自定义类名', () => {
    renderOpenedMenu()

    const menu = screen.getByRole('menu')
    expect(menu).toHaveAttribute('data-dashboard-floating-content', 'true')
    expect(menu).toHaveClass('extra-content')
    expect(menu).toHaveClass('bg-popover')
  })

  it('Label / Item 的 inset 属性追加 pl-8 缩进类', () => {
    renderOpenedMenu()

    expect(screen.getByText('菜单标题')).toHaveClass('pl-8', 'font-semibold')
    expect(screen.getByRole('menuitem', { name: /复制/ })).toHaveClass('pl-8')
  })

  it('Shortcut 渲染为右对齐的快捷键文本', () => {
    renderOpenedMenu()

    const shortcut = screen.getByText('⌘C')
    expect(shortcut.tagName).toBe('SPAN')
    expect(shortcut).toHaveClass('ml-auto', 'text-muted-foreground')
  })

  it('Separator 渲染为分隔线元素', () => {
    const { baseElement } = renderOpenedMenu()

    const separator = baseElement.querySelector('[role="separator"], [data-orientation="horizontal"].bg-border')
    expect(separator).not.toBeNull()
    expect(separator).toHaveClass('bg-border')
  })

  it('CheckboxItem 勾选时展示 Check 指示器', () => {
    renderOpenedMenu()

    const checkboxItem = screen.getByRole('menuitemcheckbox', { name: '显示网格' })
    expect(checkboxItem).toHaveAttribute('aria-checked', 'true')
    expect(checkboxItem.querySelector('.lucide-check')).not.toBeNull()
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

  it('SubTrigger 渲染右箭头，方向键展开的子菜单内容带浮层标记', () => {
    render(
      <ContextMenu>
        <ContextMenuTrigger>右键区域</ContextMenuTrigger>
        <ContextMenuContent>
          <ContextMenuSub>
            <ContextMenuSubTrigger inset>更多操作</ContextMenuSubTrigger>
            <ContextMenuSubContent className="sub-extra">
              <ContextMenuItem>子项</ContextMenuItem>
            </ContextMenuSubContent>
          </ContextMenuSub>
        </ContextMenuContent>
      </ContextMenu>
    )

    fireEvent.contextMenu(screen.getByText('右键区域'))

    const subTrigger = screen.getByText('更多操作')
    expect(subTrigger).toHaveClass('pl-8')
    expect(subTrigger.querySelector('.lucide-chevron-right')).not.toBeNull()

    // 按 Radix 键盘交互用右方向键展开子菜单
    fireEvent.keyDown(subTrigger, { key: 'ArrowRight' })

    const subItem = screen.getByRole('menuitem', { name: '子项' })
    const subContent = subItem.closest('[data-dashboard-floating-content="true"]')
    expect(subContent).not.toBeNull()
    expect(subContent).toHaveClass('sub-extra')
  })
})
