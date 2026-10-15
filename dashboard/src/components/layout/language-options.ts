// 界面语言选项：顶栏语言菜单与手机端设置页共用
export const LANGUAGE_CODES = ['zh', 'en', 'ja', 'ko'] as const

export type LanguageCode = (typeof LANGUAGE_CODES)[number]

export const LANGUAGE_NAMES: Record<LanguageCode, string> = {
  zh: '中文',
  en: 'English',
  ja: '日本語',
  ko: '한국어',
}
