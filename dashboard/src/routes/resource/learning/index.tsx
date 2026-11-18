import { useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { useNavigate, useSearch } from '@tanstack/react-router'
import { BookOpenText, MessageSquareText, Plus, Smile } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { DashboardFolderTab, DashboardFolderTabBar } from '@/components/ui/dashboard-tabs'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useMediaQuery } from '@/hooks/use-media-query'

import { EmojiManagementPage } from '../emoji'
import { ExpressionManagementPage } from '../expression'
import { JargonManagementPage } from '../jargon'

import { LearningImportDialog } from './LearningImportDialog'

/** 学习页注入给三个子页的文件夹标签；summary 是子页自己的统计，排在标签行右侧。 */
export type LearningBrowserTabs = (summary: ReactNode) => ReactNode

/** 表达方式 / 黑话子页暴露给学习页的新增入口；新增对话框仍由子页持有，保留当前聊天和刷新逻辑。 */
export interface LearningCreateHandle {
  open: (mode: 'manual' | 'auto') => void
}

export function LearningPage() {
  const { t } = useTranslation()
  const { tab } = useSearch({ from: '/protected/resource/learning' })
  const navigate = useNavigate()
  const isMobile = useMediaQuery('(max-width: 1023.98px)')
  const [importOpen, setImportOpen] = useState(false)
  const expressionCreate = useRef<LearningCreateHandle>(null)
  const jargonCreate = useRef<LearningCreateHandle>(null)
  // 新增和导入共用一个入口，放在标签行最右侧，三个页签都看得到。
  const importButton = (
    <Button size="sm" data-learning-create="true" aria-label="添加学习内容" onClick={() => setImportOpen(true)} title="新增或导入表达方式、黑话和表情包">
      <Plus className="h-4 w-4" />
      <span>添加学习内容</span>
    </Button>
  )
  const createKind = tab === 'expression' || tab === 'jargon' ? tab : null
  const mobileHeader = (
    <TabsList aria-label={t('sidebar.menu.learning')} className="w-auto shrink-0">
      <TabsTrigger value="expression" size="sm" className="min-w-0 px-3 text-xs">
        {t('sidebar.menu.expressionManagement')}
      </TabsTrigger>
      <TabsTrigger value="jargon" size="sm" className="min-w-0 px-3 text-xs">
        {t('sidebar.menu.slangManagement')}
      </TabsTrigger>
      <TabsTrigger value="emoji" size="sm" className="min-w-0 px-3 text-xs">
        {t('sidebar.menu.emojiManagement')}
      </TabsTrigger>
    </TabsList>
  )
  // 桌面端用公共组件库的文件夹标签，接在内容窗口上沿；聊天范围侧栏不在标签里。
  const folderTabs: LearningBrowserTabs | undefined = isMobile
    ? undefined
    : (summary) => (
        <DashboardFolderTabBar
          size="large"
          data-learning-tabs="true"
          aria-label={t('sidebar.menu.learning')}
          trailing={
            <>
              {summary}
              {importButton}
            </>
          }
        >
          <DashboardFolderTab
            value="expression"
            icon={<MessageSquareText />}
            description="情境与风格"
          >
            {t('sidebar.menu.expressionManagement')}
          </DashboardFolderTab>
          <DashboardFolderTab value="jargon" icon={<BookOpenText />} description="词条与释义">
            {t('sidebar.menu.slangManagement')}
          </DashboardFolderTab>
          <DashboardFolderTab value="emoji" icon={<Smile />} description="图片与描述">
            {t('sidebar.menu.emojiManagement')}
          </DashboardFolderTab>
        </DashboardFolderTabBar>
      )

  return (
    <Tabs
      value={tab}
      onValueChange={(value) => {
        if (value !== 'expression' && value !== 'jargon' && value !== 'emoji') {
          throw new Error(`未知的学习页签: ${value}`)
        }
        void navigate({
          to: '/resource/learning',
          search: { tab: value },
        })
      }}
      className="flex h-full min-h-0 flex-col overflow-hidden"
    >
      {/* 手机页签独立于各业务页头，审核子视图和表情包网格中也能切换。 */}
      {isMobile && (
        <div className="flex shrink-0 items-center gap-2 overflow-x-auto px-3 pt-3 sm:px-4">
          {mobileHeader}
          <div className="ml-auto shrink-0">{importButton}</div>
        </div>
      )}
      <TabsContent value="expression" className="mt-0 min-h-0 flex-1 overflow-hidden">
        <ExpressionManagementPage browserTabs={folderTabs} createHandle={expressionCreate} />
      </TabsContent>
      <TabsContent value="jargon" className="mt-0 min-h-0 flex-1 overflow-hidden">
        <JargonManagementPage browserTabs={folderTabs} createHandle={jargonCreate} />
      </TabsContent>
      <TabsContent
        value="emoji"
        className="mt-0 min-h-0 flex-1 overflow-hidden max-lg:overflow-y-auto"
      >
        <EmojiManagementPage browserTabs={folderTabs} />
      </TabsContent>
      <LearningImportDialog
        open={importOpen}
        onOpenChange={setImportOpen}
        createKind={createKind}
        onCreate={(mode) =>
          (createKind === 'expression' ? expressionCreate : jargonCreate).current?.open(mode)
        }
      />
    </Tabs>
  )
}
