import { cleanup, render, screen } from '@testing-library/react'
import { createInstance } from 'i18next'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import { afterEach, describe, expect, it } from 'vitest'

import en from '@/i18n/locales/en.json'
import ja from '@/i18n/locales/ja.json'
import ko from '@/i18n/locales/ko.json'
import type { JargonInferenceReasonCode } from '@/types/jargon-inference'
import { InferenceStatus } from '../JargonInferenceStatus'

afterEach(cleanup)

const reasonCodes: JargonInferenceReasonCode[] = [
  'has_meaning',
  'threshold',
  'locked',
  'manual',
  'no_session',
  'duplicate',
]

describe('selection reason localization', () => {
  it.each([['en', en], ['ja', ja], ['ko', ko]] as const)(
    'renders every stable selection reason in %s',
    async (language, resource) => {
      const i18n = createInstance()
      await i18n.use(initReactI18next).init({
        lng: language,
        fallbackLng: false,
        resources: { [language]: { translation: resource } },
        interpolation: { escapeValue: false },
      })
      render(
        <I18nextProvider i18n={i18n}>
          {reasonCodes.map((code) => (
            <p key={code}><InferenceStatus status={code} /></p>
          ))}
        </I18nextProvider>
      )
      for (const code of reasonCodes) {
        expect(screen.getByText(resource.jargonInference.states[code])).toBeInTheDocument()
      }
    }
  )
})
