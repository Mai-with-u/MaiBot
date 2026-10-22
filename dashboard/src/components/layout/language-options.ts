// 界面语言选项：通用设置中的桌面端与手机端共用
export const LANGUAGE_CODES = ['zh', 'en', 'ja', 'ko'] as const

export type LanguageCode = (typeof LANGUAGE_CODES)[number]

export const LANGUAGE_NAMES: Record<LanguageCode, string> = {
  zh: '中文',
  en: 'English',
  ja: '日本語',
  ko: '한국어',
}
