import type { BackendModule, CallbackError, i18n as I18n, ReadCallback } from 'i18next'
import { beforeAll, describe, expect, it, vi } from 'vitest'

/**
 * i18n/index.ts 含 top-level await，导入即初始化。
 * 韩语包在本文件打成失败桩，必须先写入 locale 再动态导入，避免 LanguageDetector 选中 ko。
 */
vi.mock('../locales/ko.json', async () => {
  throw 'broken-ko-pack'
})

let i18n: I18n
let localeBackend: BackendModule

// i18next 的 callback 错误类型为 Error | string | null，这里原样透传，由用例自行断言形态
function readLocale(language: string): Promise<{ err: CallbackError; data: unknown }> {
  return new Promise((resolve, reject) => {
    if (!localeBackend.read) {
      reject(new Error('locale backend 未暴露 read，无法直接覆盖不支持语言分支'))
      return
    }
    const callback: ReadCallback = (err, data) => {
      resolve({ err, data })
    }
    localeBackend.read(language, 'translation', callback)
  })
}

beforeAll(async () => {
  localStorage.setItem('maibot-locale', 'zh')
  const mod = await import('../index')
  i18n = mod.default
  localeBackend = i18n.services.backendConnector.backend as BackendModule
})

describe('i18n locale backend', () => {
  it('初始化后可加载受支持的语言包', async () => {
    expect(localeBackend).toEqual(
      expect.objectContaining({
        type: 'backend',
        init: expect.any(Function),
        read: expect.any(Function),
      })
    )

    const zh = await readLocale('zh')
    expect(zh.err).toBeFalsy()
    expect(zh.data).toMatchObject({
      header: { docs: '麦麦文档' },
    })

    const zhCN = await readLocale('zh-CN')
    expect(zhCN.err).toBeFalsy()
    expect(zhCN.data).toEqual(zh.data)

    const ja = await readLocale('ja')
    expect(ja.err).toBeFalsy()
    expect(ja.data).toMatchObject({
      header: { collapseSidebar: 'サイドバーを折りたたむ' },
    })

    await i18n.changeLanguage('en')
    expect(document.documentElement.lang).toBe('en')
    expect(i18n.t('header.docs')).toBe('MaiBot Docs')
  })

  it('不支持的语言走 callback error', async () => {
    const { err, data } = await readLocale('fr')

    expect(err).toBeInstanceOf(Error)
    expect((err as Error).message).toBe('不支持的语言：fr')
    expect(data).toBe(false)
  })

  it('语言代码归一化后仍不支持时带上原始 language 报错', async () => {
    const { err, data } = await readLocale('de-DE')

    expect(err).toBeInstanceOf(Error)
    expect((err as Error).message).toBe('不支持的语言：de-DE')
    expect(data).toBe(false)
  })

  it('语言包加载失败时走 callback error', async () => {
    const { err, data } = await readLocale('ko')

    // vi.mock 工厂抛错会被 Vitest 包成 Error，仍会进入 loader().then 的 rejection 分支
    expect(err).toBeInstanceOf(Error)
    expect(data).toBe(false)
  })
})
