import type { BackendModule, ResourceKey } from 'i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import { initReactI18next } from 'react-i18next'
import i18next from 'i18next'

const SUPPORTED_LANGUAGES = ['zh', 'en', 'ja', 'ko'] as const
type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number]

interface LocaleModule {
  default: ResourceKey
}

const LOCALE_LOADERS = {
  en: () => import('./locales/en.json'),
  ja: () => import('./locales/ja.json'),
  ko: () => import('./locales/ko.json'),
  zh: () => import('./locales/zh.json'),
} satisfies Record<SupportedLanguage, () => Promise<LocaleModule>>

const localeBackend: BackendModule = {
  type: 'backend',
  init() {},
  read(language, _namespace, callback) {
    const normalizedLanguage = language.split('-')[0] as SupportedLanguage
    const loader = LOCALE_LOADERS[normalizedLanguage]
    if (!loader) {
      callback(new Error(`不支持的语言：${language}`), false)
      return
    }

    void loader().then(
      (module) => callback(null, module.default),
      (error: unknown) =>
        callback(error instanceof Error ? error : new Error(`加载语言包 ${language} 失败`), false),
    )
  },
}

function updateDocumentLanguage(language: string) {
  if (typeof document !== 'undefined') {
    document.documentElement.lang = language
  }
}

i18next.on('languageChanged', updateDocumentLanguage)

await i18next
  .use(LanguageDetector)
  .use(localeBackend)
  .use(initReactI18next)
  .init({
    fallbackLng: 'en',
    supportedLngs: SUPPORTED_LANGUAGES,
    load: 'languageOnly',
    interpolation: {
      escapeValue: false,
    },
    detection: {
      order: ['localStorage', 'navigator'],
      lookupLocalStorage: 'maibot-locale',
      caches: ['localStorage'],
    },
    keySeparator: '.',
  })

updateDocumentLanguage(i18next.resolvedLanguage ?? i18next.language)

export default i18next
