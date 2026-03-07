import { type LibraryItemProps } from './types'

export function LibraryItem({ name, description, license }: LibraryItemProps) {
  return (
    <div className="flex items-start justify-between gap-2 rounded-lg border bg-muted/30 p-2.5 sm:p-3">
      <div className="flex-1 min-w-0">
        <p className="font-medium text-foreground truncate">{name}</p>
        <p className="text-muted-foreground text-xs mt-0.5">{description}</p>
      </div>
      <span className="inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary flex-shrink-0">
        {license}
      </span>
    </div>
  )
}
