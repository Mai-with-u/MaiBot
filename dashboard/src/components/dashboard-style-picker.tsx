import { Keyboard, Monitor, ScanLine } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { OptionCard } from '@/components/ui/management'
import type { DashboardStyle } from '@/lib/theme/tokens'

const options = [
  { value: 'modern', icon: Monitor },
  { value: 'future-retro', icon: ScanLine },
  { value: 'millennium', icon: Keyboard },
] as const

export function DashboardStylePicker({
  value,
  onChange,
  disabled,
  className,
}: {
  value: DashboardStyle
  onChange: (style: DashboardStyle) => void
  disabled?: boolean
  className: string
}) {
  const { t } = useTranslation()

  return (
    <div className={className}>
      {options.map(({ value: style, icon: Icon }) => (
        <OptionCard
          key={style}
          selected={value === style}
          onSelect={() => onChange(style)}
          disabled={disabled}
          icon={<Icon />}
          title={t(`settings.appearance.dashboardStyles.${style}.label`)}
          description={t(`settings.appearance.dashboardStyles.${style}.description`)}
        />
      ))}
    </div>
  )
}
