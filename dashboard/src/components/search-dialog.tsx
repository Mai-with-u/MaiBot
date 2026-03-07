import { useState, useCallback, useMemo } from 'react'
import { Search, FileText, Server, Boxes, Smile, MessageSquare, UserCircle, FileSearch, BarChart3, Package, Settings, Home, Hash } from 'lucide-react'
import { useNavigate } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

interface SearchDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

interface SearchItem {
  icon: React.ComponentType<{ className?: string }>
  title: string
  description: string
  path: string
  category: string
}

export function SearchDialog({ open, onOpenChange }: SearchDialogProps) {
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedIndex, setSelectedIndex] = useState(0)
  const navigate = useNavigate()
  const { t } = useTranslation()

  const searchItems: SearchItem[] = useMemo(() => [
    {
      icon: Home,
      title: t('search.items.home'),
      description: t('search.items.homeDesc'),
      path: '/',
      category: t('search.categories.overview'),
    },
    {
      icon: FileText,
      title: t('search.items.botConfig'),
      description: t('search.items.botConfigDesc'),
      path: '/config/bot',
      category: t('search.categories.config'),
    },
    {
      icon: Server,
      title: t('search.items.modelProvider'),
      description: t('search.items.modelProviderDesc'),
      path: '/config/modelProvider',
      category: t('search.categories.config'),
    },
    {
      icon: Boxes,
      title: t('search.items.model'),
      description: t('search.items.modelDesc'),
      path: '/config/model',
      category: t('search.categories.config'),
    },
    {
      icon: Smile,
      title: t('search.items.emoji'),
      description: t('search.items.emojiDesc'),
      path: '/resource/emoji',
      category: t('search.categories.resources'),
    },
    {
      icon: MessageSquare,
      title: t('search.items.expression'),
      description: t('search.items.expressionDesc'),
      path: '/resource/expression',
      category: t('search.categories.resources'),
    },
    {
      icon: UserCircle,
      title: t('search.items.person'),
      description: t('search.items.personDesc'),
      path: '/resource/person',
      category: t('search.categories.resources'),
    },
    {
      icon: Hash,
      title: t('search.items.jargon'),
      description: t('search.items.jargonDesc'),
      path: '/resource/jargon',
      category: t('search.categories.resources'),
    },
    {
      icon: BarChart3,
      title: t('search.items.statistics'),
      description: t('search.items.statisticsDesc'),
      path: '/statistics',
      category: t('search.categories.monitor'),
    },
    {
      icon: Package,
      title: t('search.items.plugins'),
      description: t('search.items.pluginsDesc'),
      path: '/plugins',
      category: t('search.categories.extensions'),
    },
    {
      icon: FileSearch,
      title: t('search.items.logs'),
      description: t('search.items.logsDesc'),
      path: '/logs',
      category: t('search.categories.monitor'),
    },
    {
      icon: Settings,
      title: t('search.items.settings'),
      description: t('search.items.settingsDesc'),
      path: '/settings',
      category: t('search.categories.system'),
    },
  ], [t])

  // 过滤搜索结果
  const filteredItems = searchItems.filter(
    (item) =>
      item.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      item.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
      item.category.toLowerCase().includes(searchQuery.toLowerCase())
  )

  // 导航到页面
  const handleNavigate = useCallback((path: string) => {
    navigate({ to: path })
    onOpenChange(false)
    // 在导航后重置状态
    setSearchQuery('')
    setSelectedIndex(0)
  }, [navigate, onOpenChange])

  // 键盘导航
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSelectedIndex((prev) => (prev + 1) % filteredItems.length)
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSelectedIndex((prev) => (prev - 1 + filteredItems.length) % filteredItems.length)
      } else if (e.key === 'Enter' && filteredItems[selectedIndex]) {
        e.preventDefault()
        handleNavigate(filteredItems[selectedIndex].path)
      }
    },
    [filteredItems, selectedIndex, handleNavigate]
  )

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl p-0 gap-0">
        <DialogHeader className="px-4 pt-4 pb-0">
          <DialogTitle className="sr-only">{t('search.title')}</DialogTitle>
          <div className="relative">
            <Search className="absolute left-3 top-1/2 h-5 w-5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value)
                setSelectedIndex(0)
              }}
              onKeyDown={handleKeyDown}
              placeholder={t('search.placeholder')}
              className="h-12 pl-11 text-base border-0 focus-visible:ring-0 shadow-none"
              autoFocus
            />
          </div>
        </DialogHeader>

        <div className="border-t">
          <ScrollArea className="h-[400px]">
            {filteredItems.length > 0 ? (
              <div className="p-2">
                {filteredItems.map((item, index) => {
                  const Icon = item.icon
                  return (
                    <button
                      key={item.path}
                      onClick={() => handleNavigate(item.path)}
                      onMouseEnter={() => setSelectedIndex(index)}
                      className={cn(
                        'w-full flex items-center gap-3 px-3 py-2.5 rounded-md text-left transition-colors',
                        index === selectedIndex
                          ? 'bg-accent text-accent-foreground'
                          : 'hover:bg-accent/50'
                      )}
                    >
                      <Icon className="h-5 w-5 flex-shrink-0" />
                      <div className="flex-1 min-w-0">
                        <div className="font-medium text-sm">{item.title}</div>
                        <div className="text-xs text-muted-foreground truncate">
                          {item.description}
                        </div>
                      </div>
                      <div className="text-xs text-muted-foreground px-2 py-1 bg-muted rounded">
                        {item.category}
                      </div>
                    </button>
                  )
                })}
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <Search className="h-12 w-12 text-muted-foreground/50 mb-4" />
                <p className="text-sm text-muted-foreground">
                  {searchQuery ? t('search.noResults') : t('search.startSearch')}
                </p>
              </div>
            )}
          </ScrollArea>
        </div>

        <div className="border-t px-4 py-3 flex items-center justify-between text-xs text-muted-foreground">
          <div className="flex items-center gap-4">
            <span className="flex items-center gap-1">
              <kbd className="px-1.5 py-0.5 bg-muted rounded border">↑</kbd>
              <kbd className="px-1.5 py-0.5 bg-muted rounded border">↓</kbd>
              {t('search.navigate')}
            </span>
            <span className="flex items-center gap-1">
              <kbd className="px-1.5 py-0.5 bg-muted rounded border">Enter</kbd>
              {t('search.select')}
            </span>
            <span className="flex items-center gap-1">
              <kbd className="px-1.5 py-0.5 bg-muted rounded border">Esc</kbd>
              {t('search.close')}
            </span>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
