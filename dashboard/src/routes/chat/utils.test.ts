import { beforeEach, describe, expect, it, vi } from 'vitest'

import { VIRTUAL_TABS_STORAGE_KEY } from './types'
import type { ChatTab, SavedVirtualTab } from './types'
import {
  generateUserId,
  getChatTabDisplayName,
  getOrCreateUserId,
  getSavedVirtualTabs,
  getStoredUserAvatarVersion,
  getStoredUserName,
  saveUserAvatarVersion,
  saveUserName,
  saveVirtualTabs,
} from './utils'

const USER_ID_STORAGE_KEY = 'maibot_webui_user_id'
const USER_NAME_STORAGE_KEY = 'maibot_webui_user_name'
const USER_AVATAR_VERSION_STORAGE_KEY = 'maibot_webui_user_avatar_version'

/** 构造一个最小可用的 webui 类型标签页 */
function makeWebuiTab(sessionInfo: ChatTab['sessionInfo'] = {}): ChatTab {
  return {
    id: 'tab-webui',
    type: 'webui',
    label: '内部占位名',
    messages: [],
    isConnected: false,
    isTyping: false,
    sessionInfo,
  }
}

/** 构造一个最小可用的虚拟身份标签页 */
function makeVirtualTab(label: string): ChatTab {
  return {
    id: 'tab-virtual',
    type: 'virtual',
    label,
    messages: [],
    isConnected: false,
    isTyping: false,
    sessionInfo: {},
  }
}

/** 构造一个持久化虚拟标签页配置 */
function makeSavedTab(id: string): SavedVirtualTab {
  return {
    id,
    label: `虚拟会话-${id}`,
    virtualConfig: {
      platform: 'qq',
      personId: 'person-1',
      userId: 'user-1',
      userName: '小明',
      groupName: '测试群',
      groupId: 'group-1',
    },
    createdAt: 1700000000000,
  }
}

beforeEach(() => {
  localStorage.clear()
})

describe('generateUserId', () => {
  it('生成 webui_ 前缀、随机段和时间戳段组成的 ID', () => {
    const id = generateUserId()
    expect(id).toMatch(/^webui_[0-9a-z]+_[0-9a-z]+$/)
  })

  it('随机数不同则生成的 ID 不同', () => {
    vi.spyOn(Math, 'random').mockReturnValueOnce(0.111111111).mockReturnValueOnce(0.999999999)
    const first = generateUserId()
    const second = generateUserId()
    expect(first).not.toBe(second)
  })
})

describe('getOrCreateUserId', () => {
  it('无存储时生成新 ID 并写入 localStorage，重复调用返回同一 ID', () => {
    const id = getOrCreateUserId()
    expect(id).toMatch(/^webui_/)
    expect(localStorage.getItem(USER_ID_STORAGE_KEY)).toBe(id)
    // 第二次调用直接命中存储，不会再生成
    expect(getOrCreateUserId()).toBe(id)
  })

  it('已有存储时直接返回存储值，不覆盖', () => {
    localStorage.setItem(USER_ID_STORAGE_KEY, 'webui_existing_abc')
    expect(getOrCreateUserId()).toBe('webui_existing_abc')
    expect(localStorage.getItem(USER_ID_STORAGE_KEY)).toBe('webui_existing_abc')
  })
})

describe('用户昵称存取', () => {
  it('未保存昵称时返回默认值「人类」', () => {
    expect(getStoredUserName()).toBe('人类')
  })

  it('saveUserName 保存后 getStoredUserName 读到同一昵称', () => {
    saveUserName('麦麦的朋友')
    expect(localStorage.getItem(USER_NAME_STORAGE_KEY)).toBe('麦麦的朋友')
    expect(getStoredUserName()).toBe('麦麦的朋友')
  })
})

describe('用户头像版本存取', () => {
  it('未保存版本号时返回 undefined', () => {
    expect(getStoredUserAvatarVersion()).toBeUndefined()
  })

  it('saveUserAvatarVersion 保存正整数版本后可读回数字', () => {
    saveUserAvatarVersion(3)
    expect(localStorage.getItem(USER_AVATAR_VERSION_STORAGE_KEY)).toBe('3')
    expect(getStoredUserAvatarVersion()).toBe(3)
  })

  it('存储值非数字时返回 undefined', () => {
    localStorage.setItem(USER_AVATAR_VERSION_STORAGE_KEY, 'abc')
    expect(getStoredUserAvatarVersion()).toBeUndefined()
  })

  it('存储值为 0 或负数时返回 undefined', () => {
    localStorage.setItem(USER_AVATAR_VERSION_STORAGE_KEY, '0')
    expect(getStoredUserAvatarVersion()).toBeUndefined()
    localStorage.setItem(USER_AVATAR_VERSION_STORAGE_KEY, '-2')
    expect(getStoredUserAvatarVersion()).toBeUndefined()
  })
})

describe('虚拟标签页持久化', () => {
  it('未存储时 getSavedVirtualTabs 返回空数组', () => {
    expect(getSavedVirtualTabs()).toEqual([])
  })

  it('saveVirtualTabs 写入 JSON，getSavedVirtualTabs 读回等价对象', () => {
    const tabs = [makeSavedTab('a'), makeSavedTab('b')]
    saveVirtualTabs(tabs)
    // 存储的是 JSON 字符串
    expect(JSON.parse(localStorage.getItem(VIRTUAL_TABS_STORAGE_KEY)!)).toEqual(tabs)
    // 读回与写入等价
    expect(getSavedVirtualTabs()).toEqual(tabs)
  })

  it('存储内容损坏（非法 JSON）时打日志并返回空数组', () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    localStorage.setItem(VIRTUAL_TABS_STORAGE_KEY, '{损坏的 JSON')
    expect(getSavedVirtualTabs()).toEqual([])
    expect(errorSpy).toHaveBeenCalledWith('[Chat] 加载虚拟标签页失败:', expect.any(SyntaxError))
  })

  it('写入 localStorage 抛错时 saveVirtualTabs 捕获异常并打日志', () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const quotaError = new Error('配额已满')
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw quotaError
    })
    expect(() => saveVirtualTabs([makeSavedTab('a')])).not.toThrow()
    expect(errorSpy).toHaveBeenCalledWith('[Chat] 保存虚拟标签页失败:', quotaError)
  })
})

describe('getChatTabDisplayName', () => {
  it('虚拟标签页直接返回其 label', () => {
    const tab = makeVirtualTab('虚拟群聊')
    expect(getChatTabDisplayName(tab, '麦麦')).toBe('虚拟群聊')
  })

  it('webui 标签页返回去除首尾空白后的 bot_name', () => {
    const tab = makeWebuiTab({ bot_name: '  麦麦  ' })
    expect(getChatTabDisplayName(tab, '默认名')).toBe('麦麦')
  })

  it('bot_name 为纯空白时回退到 botNameFallback', () => {
    const tab = makeWebuiTab({ bot_name: '   ' })
    expect(getChatTabDisplayName(tab, '默认名')).toBe('默认名')
  })

  it('bot_name 缺失时回退到 botNameFallback', () => {
    const tab = makeWebuiTab({})
    expect(getChatTabDisplayName(tab, '默认名')).toBe('默认名')
  })
})
