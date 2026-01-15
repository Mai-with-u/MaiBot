/**
 * 监控页面主入口
 * 整合规划器监控和回复器监控
 */
import { Activity, RefreshCw, MessageSquareText } from 'lucide-react'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { useState, useCallback } from 'react'
import { PlannerMonitor } from './planner-monitor'
import { ReplierMonitor } from './replier-monitor'

export function PlannerMonitorPage() {
  const [activeTab, setActiveTab] = useState<'planner' | 'replier'>('planner')
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)

  const handleManualRefresh = useCallback(() => {
    setRefreshKey(k => k + 1)
  }, [])

  return (
    <div className="space-y-4 sm:space-y-6 p-4 sm:p-6">
      {/* 页面标题 */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold">计划器 &amp; 回复器监控</h1>
          <p className="text-muted-foreground mt-1 sm:mt-2 text-sm sm:text-base">
            实时监控麦麦的任务计划器和回复生成器运行状态
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button 
            variant={autoRefresh ? "default" : "outline"} 
            size="sm"
            onClick={() => setAutoRefresh(!autoRefresh)}
          >
            <RefreshCw className={`h-4 w-4 mr-2 ${autoRefresh ? 'animate-spin' : ''}`} />
            {autoRefresh ? '自动刷新中' : '自动刷新'}
          </Button>
          <Button 
            variant="outline" 
            size="sm"
            onClick={handleManualRefresh}
          >
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* 标签页 */}
      <Tabs 
        value={activeTab}
        onValueChange={(v) => setActiveTab(v as 'planner' | 'replier')}
        className="w-full"
      >
        <TabsList className="grid w-full grid-cols-2 gap-0.5 sm:gap-1 h-auto p-1">
          <TabsTrigger value="planner" className="gap-1 sm:gap-2 text-xs sm:text-sm px-2 sm:px-3 py-2">
            <Activity className="h-3.5 w-3.5 sm:h-4 sm:w-4" strokeWidth={2} fill="none" />
            <span>计划器监控</span>
          </TabsTrigger>
          <TabsTrigger value="replier" className="gap-1 sm:gap-2 text-xs sm:text-sm px-2 sm:px-3 py-2">
            <MessageSquareText className="h-3.5 w-3.5 sm:h-4 sm:w-4" strokeWidth={2} fill="none" />
            <span>回复器监控</span>
          </TabsTrigger>
        </TabsList>

        <ScrollArea className="h-[calc(100vh-240px)] sm:h-[calc(100vh-280px)] mt-4 sm:mt-6">
          <TabsContent value="planner" className="mt-0">
            <PlannerMonitor 
              autoRefresh={autoRefresh} 
              refreshKey={refreshKey}
            />
          </TabsContent>

          <TabsContent value="replier" className="mt-0">
            <ReplierMonitor 
              autoRefresh={autoRefresh}
              refreshKey={refreshKey}
            />
          </TabsContent>
        </ScrollArea>
      </Tabs>
    </div>
  )
}
