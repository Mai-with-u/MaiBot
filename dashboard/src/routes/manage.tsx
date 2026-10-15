import { Link } from '@tanstack/react-router'
import { ChevronRight, Search } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { HTMLAttributes, ReactNode } from 'react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useTheme } from '@/components/use-theme'
import { MOBILE_EXTRA_MANAGE_SECTIONS } from '@/components/layout/mobile-manage-sections'
import { useMobileNav } from '@/components/layout/mobile-nav-context'
import type { MenuItem, MenuSection } from '@/components/layout/types'
import { cn } from '@/lib/utils'

// 首页、聊天已经在底部标签栏里，管理页不再重复列出。
const TAB_PATHS = new Set(['/', '/chat'])

interface ManageItemLinkProps extends HTMLAttributes<HTMLAnchorElement> {
  item: MenuItem
  children: ReactNode
  'data-dashboard-nav-item'?: 'true'
}

function ManageItemLink({ item, children, ...rest }: ManageItemLinkProps) {
  if (item.external) {
    return (
      <a href={item.path} target="_blank" rel="noreferrer" {...rest}>
        {children}
      </a>
    )
  }
  return (
    <Link to={item.path} {...rest}>
      {children}
    </Link>
  )
}

/**
 * 移动端「管理」页：把侧栏的全部入口按分组铺开。
 * 原版风格是分组列表；千禧风格是印着丝印标题的模块，里面一排排键帽。
 */
export function ManagePage() {
  const { t } = useTranslation()
  const { themeConfig } = useTheme()
  const { menuSections, openSearch } = useMobileNav()
  const millennium = themeConfig.dashboardStyle === 'millennium'

  const sections = [...menuSections, ...MOBILE_EXTRA_MANAGE_SECTIONS]
    .map((section) => ({
      ...section,
      items: section.items.filter((item) => !TAB_PATHS.has(item.path)),
    }))
    .filter((section) => section.items.length > 0)

  const sectionTitle = (section: MenuSection) =>
    section.literalTitle ? section.title : t(section.title)
  const itemLabel = (item: MenuItem) => (item.literalLabel ? item.label : t(item.label))

  return (
    <div data-manage-page="true" className="mx-auto w-full max-w-2xl space-y-6 p-4 sm:p-6">
      <h1 data-mobile-large-title="true" className="px-1 text-3xl font-bold tracking-tight">
        {t('mobileNav.manage')}
      </h1>

      <button
        type="button"
        data-dashboard-input="true"
        onClick={openSearch}
        className="bg-muted text-muted-foreground flex h-10 w-full items-center gap-2 rounded-xl px-3 text-left text-base"
      >
        <Search className="h-[17px] w-[17px] shrink-0" aria-hidden="true" />
        <span className="truncate">{t('manage.searchPlaceholder')}</span>
      </button>

      {sections.map((section) =>
        millennium ? (
          <Card key={section.title} data-manage-group="true">
            <CardHeader>
              <CardTitle>{sectionTitle(section)}</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-2.5 p-1 pt-0">
              {section.items.map((item) => {
                const Icon = item.icon
                return (
                  <ManageItemLink
                    key={item.path}
                    item={item}
                    data-dashboard-nav-item="true"
                    className="flex h-[2.875rem] min-w-0 items-center gap-2.5 px-3"
                  >
                    <Icon className="h-5 w-5 shrink-0" size={20} />
                    <span className="truncate text-sm font-bold">{itemLabel(item)}</span>
                  </ManageItemLink>
                )
              })}
            </CardContent>
          </Card>
        ) : (
          <section key={section.title}>
            <h2 className="text-muted-foreground mb-2 ml-4 text-[13px] font-normal">
              {sectionTitle(section)}
            </h2>
            <Card
              data-manage-group="true"
              className="border-border/60 overflow-hidden rounded-3xl shadow-none"
            >
              <ul>
                {section.items.map((item, index) => {
                  const Icon = item.icon
                  return (
                    <li key={item.path}>
                      <ManageItemLink
                        item={item}
                        className="active:bg-muted flex h-[54px] items-center gap-3.5 pl-4 transition-colors"
                      >
                        <Icon className="text-foreground h-[22px] w-[22px] shrink-0" size={22} />
                        <span
                          className={cn(
                            'flex h-full min-w-0 flex-1 items-center gap-2 pr-4 text-base',
                            index > 0 && 'border-border/70 border-t'
                          )}
                        >
                          <span className="truncate">{itemLabel(item)}</span>
                          <ChevronRight
                            className="text-muted-foreground/50 ml-auto h-4 w-4 shrink-0"
                            strokeWidth={2.4}
                            aria-hidden="true"
                          />
                        </span>
                      </ManageItemLink>
                    </li>
                  )
                })}
              </ul>
            </Card>
          </section>
        )
      )}
    </div>
  )
}
