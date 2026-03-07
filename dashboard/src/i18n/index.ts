import LanguageDetector from 'i18next-browser-languagedetector'
import { initReactI18next } from 'react-i18next'
import i18next from 'i18next'

import en from './locales/en.json'
import ja from './locales/ja.json'
import ko from './locales/ko.json'
import zh from './locales/zh.json'

i18next
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      zh: { translation: zh },
      en: { translation: en },
      ja: { translation: ja },
      ko: { translation: ko },
    },
    fallbackLng: 'en',
    supportedLngs: ['zh', 'en', 'ja', 'ko'],
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

i18next.on('languageChanged', (lng) => {
  document.documentElement.lang = lng
})

export default i18next
