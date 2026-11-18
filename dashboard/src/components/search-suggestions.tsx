import { Link } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

import type { SearchItem } from '@/hooks/use-search-index'
import { buildSearchNavigationPath } from '@/lib/config-search-navigation'

export function SearchSuggestions({ items }: { items: SearchItem[] }) {
  const { t } = useTranslation()
  if (items.length === 0) return null

  return (
    <ul
      aria-label={t('search.title')}
      className="border-border bg-popover text-popover-foreground absolute inset-x-0 bottom-full z-30 mb-2 max-h-64 overflow-y-auto rounded-md border p-1 shadow-md"
    >
      {items.map(({ id, title, description, path, fieldPath, icon: Icon }) => {
        const [, query] = buildSearchNavigationPath(path, fieldPath).split('?')
        return (
          <li key={id}>
            <Link
              to={path}
              search={query ? Object.fromEntries(new URLSearchParams(query)) : undefined}
              className="hover:bg-accent focus-visible:bg-accent flex items-center gap-2 rounded px-3 py-2"
            >
              <Icon className="size-4 shrink-0" aria-hidden="true" />
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium">{title}</span>
                <span className="text-muted-foreground block truncate text-xs">{description}</span>
              </span>
            </Link>
          </li>
        )
      })}
    </ul>
  )
}
