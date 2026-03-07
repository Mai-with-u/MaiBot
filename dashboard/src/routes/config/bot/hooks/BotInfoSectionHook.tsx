import type { FieldHookComponent } from '@/lib/field-hooks'

import type { BotConfig } from '../types'
import { BotInfoSection } from '../sections/BotInfoSection'

/**
 * BotInfoSection as a Field Hook Component
 * This component replaces the entire 'bot' nested config section rendering
 */
export const BotInfoSectionHook: FieldHookComponent = ({ value, onChange }) => {
  return (
    <BotInfoSection
      config={value as BotConfig}
      onChange={(newConfig) => onChange?.(newConfig)}
    />
  )
}
