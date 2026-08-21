import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  getPlatformModifierAriaLabel,
  getShortcutKeyLabel,
  isEditableTarget,
  isMacLikePlatform,
  matchesShortcut,
} from './keyboard'

/** jsdom 的 platform/userAgent 在原型上，测试里用自有属性遮蔽，用完删除即可恢复。 */
function mockNavigator(overrides: { platform?: string; userAgent?: string }) {
  if (overrides.platform !== undefined) {
    Object.defineProperty(navigator, 'platform', { configurable: true, value: overrides.platform })
  }
  if (overrides.userAgent !== undefined) {
    Object.defineProperty(navigator, 'userAgent', { configurable: true, value: overrides.userAgent })
  }
}

function restoreNavigator() {
  if (typeof navigator === 'undefined') {
    return
  }
  Reflect.deleteProperty(navigator, 'platform')
  Reflect.deleteProperty(navigator, 'userAgent')
}

function keyEvent(init: {
  key: string
  altKey?: boolean
  ctrlKey?: boolean
  metaKey?: boolean
  shiftKey?: boolean
}): KeyboardEvent {
  return new KeyboardEvent('keydown', {
    key: init.key,
    altKey: init.altKey ?? false,
    ctrlKey: init.ctrlKey ?? false,
    metaKey: init.metaKey ?? false,
    shiftKey: init.shiftKey ?? false,
  })
}

afterEach(() => {
  restoreNavigator()
  vi.unstubAllGlobals()
})

describe('isMacLikePlatform', () => {
  it('根据 platform 识别 Mac / iOS 与 Windows', () => {
    mockNavigator({ platform: 'MacIntel' })
    expect(isMacLikePlatform()).toBe(true)

    mockNavigator({ platform: 'iPhone' })
    expect(isMacLikePlatform()).toBe(true)

    mockNavigator({ platform: 'Win32' })
    expect(isMacLikePlatform()).toBe(false)
  })

  it('platform 为空时回退到 userAgent 判断', () => {
    mockNavigator({
      platform: '',
      userAgent: 'Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)',
    })
    expect(isMacLikePlatform()).toBe(true)

    mockNavigator({
      platform: '',
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    })
    expect(isMacLikePlatform()).toBe(false)
  })

  it('没有 navigator 时视为非 Mac（SSR）', () => {
    vi.stubGlobal('navigator', undefined)
    expect(isMacLikePlatform()).toBe(false)
  })
})

describe('getShortcutKeyLabel', () => {
  it('Mac 平台用符号表示修饰键和回车', () => {
    mockNavigator({ platform: 'MacIntel' })

    expect(getShortcutKeyLabel('mod')).toBe('⌘')
    expect(getShortcutKeyLabel('shift')).toBe('⇧')
    expect(getShortcutKeyLabel('alt')).toBe('⌥')
    expect(getShortcutKeyLabel('enter')).toBe('↵')
  })

  it('非 Mac 平台用文字标签，含完整 alt 分支', () => {
    mockNavigator({ platform: 'Win32' })

    expect(getShortcutKeyLabel('mod')).toBe('Ctrl')
    expect(getShortcutKeyLabel('shift')).toBe('Shift')
    expect(getShortcutKeyLabel('alt')).toBe('Alt')
    expect(getShortcutKeyLabel('enter')).toBe('Enter')
  })

  it('方向键、Esc 以及默认键位按现有映射输出', () => {
    mockNavigator({ platform: 'Win32' })

    expect(getShortcutKeyLabel('esc')).toBe('Esc')
    expect(getShortcutKeyLabel('escape')).toBe('Esc')
    expect(getShortcutKeyLabel('up')).toBe('↑')
    expect(getShortcutKeyLabel('down')).toBe('↓')
    expect(getShortcutKeyLabel('left')).toBe('←')
    expect(getShortcutKeyLabel('right')).toBe('→')
    expect(getShortcutKeyLabel('k')).toBe('K')
    // 多字符默认键不改写，只把单字符转大写
    expect(getShortcutKeyLabel('F5')).toBe('F5')
    expect(getShortcutKeyLabel('Tab')).toBe('Tab')
  })
})

describe('getPlatformModifierAriaLabel', () => {
  it('Mac 读 Command，其他平台读 Control', () => {
    mockNavigator({ platform: 'MacIntel' })
    expect(getPlatformModifierAriaLabel()).toBe('Command')

    mockNavigator({ platform: 'Win32' })
    expect(getPlatformModifierAriaLabel()).toBe('Control')
  })
})

describe('matchesShortcut', () => {
  it('Mac 用 metaKey 匹配 mod，ctrlKey 不能代替', () => {
    mockNavigator({ platform: 'MacIntel' })

    expect(matchesShortcut(keyEvent({ key: 'k', metaKey: true }), ['mod', 'k'])).toBe(true)
    expect(matchesShortcut(keyEvent({ key: 'k', ctrlKey: true }), ['mod', 'k'])).toBe(false)
  })

  it('非 Mac 用 ctrlKey 匹配 mod，metaKey 不能代替', () => {
    mockNavigator({ platform: 'Win32' })

    expect(matchesShortcut(keyEvent({ key: 'k', ctrlKey: true }), ['mod', 'k'])).toBe(true)
    expect(matchesShortcut(keyEvent({ key: 'k', metaKey: true }), ['mod', 'k'])).toBe(false)
  })

  it('声明了 shift/alt 时对应修饰键必须按下，否则失败', () => {
    mockNavigator({ platform: 'Win32' })

    expect(matchesShortcut(keyEvent({ key: 'k', shiftKey: true }), ['shift', 'k'])).toBe(true)
    expect(matchesShortcut(keyEvent({ key: 'k' }), ['shift', 'k'])).toBe(false)
    expect(matchesShortcut(keyEvent({ key: 'k', altKey: true }), ['alt', 'k'])).toBe(true)
    expect(matchesShortcut(keyEvent({ key: 'k' }), ['alt', 'k'])).toBe(false)
  })

  it('主键不匹配时返回 false，全部命中时返回 true', () => {
    mockNavigator({ platform: 'Win32' })

    expect(matchesShortcut(keyEvent({ key: 'Enter' }), ['enter'])).toBe(true)
    expect(matchesShortcut(keyEvent({ key: 'Escape' }), ['esc'])).toBe(false)
    expect(
      matchesShortcut(keyEvent({ key: 'k', ctrlKey: true, shiftKey: true }), ['mod', 'shift', 'k'])
    ).toBe(true)
  })
})

describe('isEditableTarget', () => {
  it('非 HTMLElement 一律视为不可编辑', () => {
    expect(isEditableTarget(null)).toBe(false)
    expect(isEditableTarget(document.createTextNode('文本'))).toBe(false)
    expect(isEditableTarget(window)).toBe(false)
  })

  it('INPUT / TEXTAREA / contentEditable / role=textbox 视为可编辑', () => {
    const input = document.createElement('input')
    const textarea = document.createElement('textarea')
    const editable = document.createElement('div')
    // jsdom 的 isContentEditable 未实现（恒为 undefined），这里直接补上源码读取的属性
    Object.defineProperty(editable, 'isContentEditable', { configurable: true, value: true })
    const textbox = document.createElement('div')
    textbox.setAttribute('role', 'textbox')
    const plain = document.createElement('div')

    expect(isEditableTarget(input)).toBe(true)
    expect(isEditableTarget(textarea)).toBe(true)
    expect(isEditableTarget(editable)).toBe(true)
    expect(isEditableTarget(textbox)).toBe(true)
    expect(isEditableTarget(plain)).toBe(false)
  })
})
