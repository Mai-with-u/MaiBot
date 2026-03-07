import type { FieldHookComponent } from '@/lib/field-hooks'

import type { ExpressionConfig } from '../types'
import { ExpressionSection } from '../sections/ExpressionSection'

/**
 * ExpressionSection as a Field Hook Component
 * This component replaces the entire 'expression' nested config section rendering
 */
export const ExpressionSectionHook: FieldHookComponent = ({ value, onChange }) => {
  return (
    <ExpressionSection
      config={value as ExpressionConfig}
      onChange={(newConfig) => onChange?.(newConfig)}
    />
  )
}
