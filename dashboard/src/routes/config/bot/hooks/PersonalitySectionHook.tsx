import type { FieldHookComponent } from '@/lib/field-hooks'

import type { PersonalityConfig } from '../types'
import { PersonalitySection } from '../sections/PersonalitySection'

/**
 * PersonalitySection as a Field Hook Component
 * This component replaces the entire 'personality' nested config section rendering
 */
export const PersonalitySectionHook: FieldHookComponent = ({ value, onChange }) => {
  return (
    <PersonalitySection
      config={value as PersonalityConfig}
      onChange={(newConfig) => onChange?.(newConfig)}
    />
  )
}
