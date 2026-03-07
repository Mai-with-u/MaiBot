import { cn } from '@/lib/utils'

import { type ThemeOptionProps } from './types'

export function ThemeOption({ value, current, onChange, label, description }: ThemeOptionProps) {
  const isSelected = current === value

  return (
    <button
      onClick={() => onChange(value)}
      className={cn(
        'relative rounded-lg border-2 p-3 sm:p-4 text-left transition-all',
        'hover:border-primary/50 hover:bg-accent/50',
        isSelected ? 'border-primary bg-accent' : 'border-border'
      )}
    >
      {isSelected && (
        <div className="absolute top-2 right-2 sm:top-3 sm:right-3 h-2 w-2 rounded-full bg-primary" />
      )}

      <div className="space-y-1">
        <div className="text-sm sm:text-base font-medium">{label}</div>
        <div className="text-[10px] sm:text-xs text-muted-foreground">{description}</div>
      </div>

      <div className="mt-2 sm:mt-3 flex gap-1">
        {value === 'light' && (
          <>
            <div className="h-2 w-2 rounded-full bg-slate-200" />
            <div className="h-2 w-2 rounded-full bg-slate-300" />
            <div className="h-2 w-2 rounded-full bg-slate-400" />
          </>
        )}
        {value === 'dark' && (
          <>
            <div className="h-2 w-2 rounded-full bg-slate-700" />
            <div className="h-2 w-2 rounded-full bg-slate-800" />
            <div className="h-2 w-2 rounded-full bg-slate-900" />
          </>
        )}
        {value === 'system' && (
          <>
            <div className="h-2 w-2 rounded-full bg-gradient-to-r from-slate-200 to-slate-700" />
            <div className="h-2 w-2 rounded-full bg-gradient-to-r from-slate-300 to-slate-800" />
            <div className="h-2 w-2 rounded-full bg-gradient-to-r from-slate-400 to-slate-900" />
          </>
        )}
      </div>
    </button>
  )
}
