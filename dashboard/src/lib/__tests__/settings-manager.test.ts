import { beforeEach, describe, expect, it } from 'vitest'

import {
  DEFAULT_SETTINGS,
  STORAGE_KEYS,
  clearLocalCache,
  exportSettings,
  formatBytes,
  getAllSettings,
  getSetting,
  getStorageUsage,
  importSettings,
  resetAllSettings,
  setSetting,
} from '../settings-manager'

describe('settings-manager', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  describe('getSetting', () => {
    it('未存储时返回默认值', () => {
      expect(getSetting('theme')).toBe('system')
      expect(getSetting('logCacheSize')).toBe(1000)
      expect(getSetting('enableAnimations')).toBe(true)
    })

    it('按默认值类型解析布尔与数字', () => {
      localStorage.setItem(STORAGE_KEYS.ENABLE_ANIMATIONS, 'false')
      localStorage.setItem(STORAGE_KEYS.LOG_CACHE_SIZE, '250')
      localStorage.setItem(STORAGE_KEYS.LOG_LINE_SPACING, '2.5')

      expect(getSetting('enableAnimations')).toBe(false)
      expect(getSetting('logCacheSize')).toBe(250)
      expect(getSetting('logLineSpacing')).toBe(2.5)
    })

    it('数字设置存储了非法值时回落到默认值', () => {
      localStorage.setItem(STORAGE_KEYS.LOG_CACHE_SIZE, 'not-a-number')
      expect(getSetting('logCacheSize')).toBe(1000)
    })

    it('字符串设置原样返回存储值', () => {
      localStorage.setItem(STORAGE_KEYS.LOG_LEVEL_FILTER, 'ERROR')
      expect(getSetting('logLevelFilter')).toBe('ERROR')
    })
  })

  describe('setSetting', () => {
    it('写入 localStorage 并派发设置变更事件', () => {
      const events: CustomEvent[] = []
      const handler = (event: Event) => {
        events.push(event as CustomEvent)
      }
      window.addEventListener('maibot-settings-change', handler)

      try {
        setSetting('logCacheSize', 500)
      } finally {
        window.removeEventListener('maibot-settings-change', handler)
      }

      expect(localStorage.getItem(STORAGE_KEYS.LOG_CACHE_SIZE)).toBe('500')
      expect(events).toHaveLength(1)
      expect(events[0].detail).toEqual({ key: 'logCacheSize', value: 500 })
    })
  })

  describe('getAllSettings / exportSettings', () => {
    it('空存储时 getAllSettings 等于默认设置', () => {
      expect(getAllSettings()).toEqual(DEFAULT_SETTINGS)
    })

    it('exportSettings 附带已完成的引导列表', () => {
      localStorage.setItem(STORAGE_KEYS.COMPLETED_TOURS, JSON.stringify(['tour-a']))
      setSetting('theme', 'dark')

      const exported = exportSettings()
      expect(exported.theme).toBe('dark')
      expect(exported.completedTours).toEqual(['tour-a'])
    })

    it('exportSettings 无引导记录时 completedTours 为空数组', () => {
      expect(exportSettings().completedTours).toEqual([])
    })
  })

  describe('importSettings', () => {
    it('导入合法设置并跳过非法与未知项', () => {
      const result = importSettings({
        theme: 'dark',
        logCacheSize: 200,
        logFontSize: 'xl' as unknown as 'xs', // 非法枚举值应被跳过
        unknownKey: 'x',
      } as Parameters<typeof importSettings>[0])

      expect(result.success).toBe(true)
      expect(result.imported).toEqual(['theme', 'logCacheSize'])
      expect(result.skipped).toEqual(['logFontSize', 'unknownKey'])
      expect(getSetting('theme')).toBe('dark')
      expect(getSetting('logCacheSize')).toBe(200)
    })

    it('类型不匹配的值被跳过', () => {
      const result = importSettings({
        logCacheSize: '200' as unknown as number,
      })

      expect(result.success).toBe(false)
      expect(result.skipped).toEqual(['logCacheSize'])
      expect(getSetting('logCacheSize')).toBe(1000)
    })

    it('非法主题与非法日志级别被跳过', () => {
      const result = importSettings({
        theme: 'rainbow' as unknown as 'dark',
        logLevelFilter: 'VERBOSE' as unknown as 'INFO',
      })

      expect(result.success).toBe(false)
      expect(result.skipped).toEqual(['theme', 'logLevelFilter'])
    })

    it('completedTours 为数组时写入，非数组时跳过', () => {
      const arrayResult = importSettings({ completedTours: ['a', 'b'] })
      expect(arrayResult.imported).toEqual(['completedTours'])
      expect(localStorage.getItem(STORAGE_KEYS.COMPLETED_TOURS)).toBe(JSON.stringify(['a', 'b']))

      const invalidResult = importSettings({
        completedTours: 'oops' as unknown as string[],
      })
      expect(invalidResult.skipped).toEqual(['completedTours'])
    })
  })

  describe('resetAllSettings', () => {
    it('重置全部设置为默认值并清除引导记录', () => {
      setSetting('theme', 'dark')
      setSetting('logCacheSize', 42)
      localStorage.setItem(STORAGE_KEYS.COMPLETED_TOURS, JSON.stringify(['tour-a']))

      const resetEvents: Event[] = []
      const handler = (event: Event) => {
        resetEvents.push(event)
      }
      window.addEventListener('maibot-settings-reset', handler)

      try {
        resetAllSettings()
      } finally {
        window.removeEventListener('maibot-settings-reset', handler)
      }

      expect(getAllSettings()).toEqual(DEFAULT_SETTINGS)
      expect(localStorage.getItem(STORAGE_KEYS.COMPLETED_TOURS)).toBeNull()
      expect(resetEvents).toHaveLength(1)
    })
  })

  describe('clearLocalCache', () => {
    it('只清除 maibot 与 accent-color 前缀的键', () => {
      localStorage.setItem('maibot-ui-theme', 'dark')
      localStorage.setItem('maibot_user_id', 'user-1')
      localStorage.setItem('accent-color', '1 2% 3%')
      localStorage.setItem('third-party-key', 'keep')

      const result = clearLocalCache()

      expect(result.clearedKeys.sort()).toEqual(['accent-color', 'maibot-ui-theme', 'maibot_user_id'])
      // 现状特征化：preservedKeys 始终为空数组，不会记录被保留的键
      expect(result.preservedKeys).toEqual([])
      expect(localStorage.getItem('third-party-key')).toBe('keep')
      expect(localStorage.getItem('maibot-ui-theme')).toBeNull()
    })
  })

  describe('getStorageUsage', () => {
    it('按 UTF-16 双字节统计大小并按大小降序排列', () => {
      localStorage.setItem('a', 'xy') // (1 + 2) * 2 = 6
      localStorage.setItem('bb', 'wxyz') // (2 + 4) * 2 = 12

      const usage = getStorageUsage()

      expect(usage.items).toBe(2)
      expect(usage.used).toBe(18)
      expect(usage.details).toEqual([
        { key: 'bb', size: 12 },
        { key: 'a', size: 6 },
      ])
    })
  })

  describe('formatBytes', () => {
    it('格式化 0、整数与小数进位', () => {
      expect(formatBytes(0)).toBe('0 B')
      expect(formatBytes(512)).toBe('512 B')
      expect(formatBytes(2048)).toBe('2 KB')
      expect(formatBytes(1536)).toBe('1.5 KB')
      expect(formatBytes(3 * 1024 * 1024)).toBe('3 MB')
    })
  })
})
