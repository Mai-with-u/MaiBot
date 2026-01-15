import { useState, useCallback } from 'react'
import { Search, FileText, Server, Boxes, Smile, MessageSquare, UserCircle, FileSearch, BarChart3, Package, Settings, Home, Hash } from 'lucide-react'
import { useNavigate } from '@tanstack/react-router'
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

const searchItems: SearchItem[] = [
  {
    icon: Home,
    title: '首页',
    description: '查看仪表板概览',
    path: '/',
    category: '概览',
  },
  {
    icon: FileText,
    title: '麦麦主程序配置',
    description: '配置麦麦的核心设置',
    path: '/config/bot',
    category: '配置',
  },
  {
    icon: Server,
    title: '麦麦模型提供商配置',
    description: '配置模型提供商',
    path: '/config/modelProvider',
    category: '配置',
  },
  {
    icon: Boxes,
    title: '麦麦模型配置',
    description: '配置模型参数',
    path: '/config/model',
    category: '配置',
  },
  {
    icon: Smile,
    title: '表情包管理',
    description: '管理麦麦的表情包',
    path: '/resource/emoji',
    category: '资源',
  },
  {
    icon: MessageSquare,
    title: '表达方式管理',
    description: '管理麦麦的表达方式',
    path: '/resource/expression',
    category: '资源',
  },
  {
    icon: UserCircle,
    title: '人物信息管理',
    description: '管理人物信息',
    path: '/resource/person',
    category: '资源',
  },
  {
    icon: Hash,
    title: '黑话管理',
    description: '管理麦麦学习到的黑话和俚语',
    path: '/resource/jargon',
    category: '资源',
  },
  {
    icon: BarChart3,
    title: '统计信息',
    description: '查看使用统计',
    path: '/statistics',
    category: '监控',
  },
  {
    icon: Package,
    title: '插件市场',
    description: '浏览和安装插件',
    path: '/plugins',
    category: '扩展',
  },
  {
    icon: FileSearch,
    title: '日志查看器',
    description: '查看系统日志',
    path: '/logs',
    category: '监控',
  },
  {
    icon: Settings,
    title: '系统设置',
    description: '配置系统参数',
    path: '/settings',
    category: '系统',
  },
]

export function SearchDialog({ open, onOpenChange }: SearchDialogProps) {
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedIndex, setSelectedIndex] = useState(0)
  const navigate = useNavigate()

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
          <DialogTitle className="sr-only">搜索</DialogTitle>
          <div className="relative">
            <Search className="absolute left-3 top-1/2 h-5 w-5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value)
                setSelectedIndex(0)
              }}
              onKeyDown={handleKeyDown}
              placeholder="搜索页面..."
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
                  {searchQuery ? '未找到匹配的页面' : '输入关键词开始搜索'}
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
              导航
            </span>
            <span className="flex items-center gap-1">
              <kbd className="px-1.5 py-0.5 bg-muted rounded border">Enter</kbd>
              选择
            </span>
            <span className="flex items-center gap-1">
              <kbd className="px-1.5 py-0.5 bg-muted rounded border">Esc</kbd>
              关闭
            </span>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
