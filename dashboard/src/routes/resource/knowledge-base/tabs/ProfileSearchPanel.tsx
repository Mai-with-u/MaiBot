/**
 * ProfileSearchPanel —— 记忆查询 → 「人物画像」子视图。
 *
 * 只负责检索与查看：查询卡与人物列表占据整行，精确/模糊两种查询方式合成一个切换按钮，
 * 切换时输入区做纵向滚动过渡；点击候选项弹出画像详情弹窗（快照文本、覆写与自动画像切换、
 * 原始响应），与文字记录「点列表项开详情」的交互一致，详情不再常驻占位。
 * 支撑证据的纠错、别名维护、画像覆写属于检修动作，在记忆检修的 ProfileMaintenancePanel。
 */
import { ArrowLeftRight, ChevronDown, Loader2, RefreshCw, Search } from 'lucide-react'
import { motion, AnimatePresence, useReducedMotion } from 'motion/react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { cn } from '@/lib/utils'

import { describeProfileIdentity, formatMemoryTime } from '../profile-utils'
import type { UseMemoryProfileConsoleResult } from '../hooks/useMemoryProfileConsole'

export interface ProfileSearchPanelProps {
  profile: UseMemoryProfileConsoleResult
}

export function ProfileSearchPanel({ profile }: ProfileSearchPanelProps) {
  const {
    queryMode,
    setQueryMode,
    queryPlatform,
    setQueryPlatform,
    queryUserId,
    setQueryUserId,
    queryKeyword,
    setQueryKeyword,
    submitQuery,
    loadProfiles,
    loading,
    querying,
    profileListMode,
    profiles,
    selectProfile,
    selectedPersonId,
    selectedProfile,
    queryResult,
    currentProfileEvidence,
    displayedProfileText,
    showAutoProfile,
    setShowAutoProfile,
    showRawProfilePayload,
    setShowRawProfilePayload,
    detailOpen,
    setDetailOpen,
  } = profile
  // 尊重系统「减少动态效果」：开启时跳过滚动过渡，直接切换
  const reduceMotion = useReducedMotion()

  const detailPersonLabel =
    selectedProfile?.person_name || selectedPersonId || String(queryResult?.person_id ?? '')

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Search className="h-4 w-4" />
            人物画像查询
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* 精确/模糊合成一个无文字切换按钮，放在输入框同一行末尾；
              点一次换成另一种，再点一次换回来。
              切换效果是纵向滚动：输入区在 overflow-hidden 的窗口里滚动，不做淡入淡出。
              popLayout 让退场内容立刻脱离文档流，容器高度由新内容决定，不会先塌再长。 */}
          {/* 精确/模糊合成一个无文字切换按钮：按钮固定在行尾不参与滚动，
              只有左侧输入区在 overflow-hidden 的窗口里纵向滚动。
              popLayout 让退场内容脱离文档流，容器高度始终由新内容决定，不会先塌再长。 */}
          <div className="flex items-end gap-3">
            <div className="relative min-w-0 flex-1 overflow-hidden">
              <AnimatePresence initial={false} mode="popLayout">
                <motion.div
                  key={queryMode}
                  className="flex w-full items-end gap-3"
                  initial={reduceMotion ? false : { y: '100%' }}
                  animate={{ y: 0 }}
                  exit={reduceMotion ? undefined : { y: '-100%' }}
                  transition={{ duration: reduceMotion ? 0 : 0.24, ease: 'easeInOut' }}
                >
                  {queryMode === 'exact' ? (
                    <>
                      {/* 平台 + 账号两个定位条件；匹配到的人物直接看下方结果列表 */}
                      <div className="min-w-0 flex-1 space-y-2">
                        <Label htmlFor="profile-platform">平台</Label>
                        <Input
                          id="profile-platform"
                          value={queryPlatform}
                          onChange={(event) => setQueryPlatform(event.target.value)}
                          placeholder="例如 qq、telegram、webui"
                        />
                      </div>
                      <div className="min-w-0 flex-1 space-y-2">
                        <Label htmlFor="profile-user-id">用户账号</Label>
                        <Input
                          id="profile-user-id"
                          value={queryUserId}
                          onChange={(event) => setQueryUserId(event.target.value)}
                          placeholder="输入平台侧 user_id"
                        />
                      </div>
                    </>
                  ) : (
                    <div className="min-w-0 flex-1 space-y-2">
                      {/* 关键词由后端解析：姓名、平台昵称、群名片与 person_id 都走这一个入口 */}
                      <Label htmlFor="profile-keyword">人物关键词</Label>
                      <Input
                        id="profile-keyword"
                        value={queryKeyword}
                        onChange={(event) => setQueryKeyword(event.target.value)}
                        placeholder="人物名称、群名片或 person_id"
                      />
                    </div>
                  )}
                </motion.div>
              </AnimatePresence>
            </div>
            <Button
              type="button"
              variant="outline"
              size="icon"
              className="shrink-0"
              aria-label={queryMode === 'exact' ? '切换为模糊查询' : '切换为精确查询'}
              title={
                queryMode === 'exact'
                  ? '当前精确查询（按平台与用户账号定位），点击切换为模糊查询'
                  : '当前模糊查询（按关键词检索），点击切换为精确查询'
              }
              onClick={() => setQueryMode(queryMode === 'exact' ? 'fuzzy' : 'exact')}
            >
              <ArrowLeftRight className="h-4 w-4" />
            </Button>
          </div>

          <div className="flex flex-wrap gap-2">
            <Button onClick={() => void submitQuery()} disabled={querying}>
              <Search className="mr-2 h-4 w-4" />
              查询人物画像
            </Button>
            <Button variant="outline" onClick={() => void loadProfiles()} disabled={loading}>
              <RefreshCw className={cn('mr-2 h-4 w-4', loading && 'animate-spin')} />
              查看画像库
            </Button>
          </div>

          {querying ? (
            <div className="text-muted-foreground flex items-center gap-2 text-sm">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在查询人物画像
            </div>
          ) : null}

          <ScrollArea
            aria-label="人物画像列表"
            className="max-h-[clamp(32.5rem,70vh,52rem)]"
            viewportClassName="max-h-[clamp(32.5rem,70vh,52rem)]"
          >
            <Table>
              <TableHeader className="bg-background sticky top-0">
                <TableRow>
                  <TableHead>人物</TableHead>
                  <TableHead>版本</TableHead>
                  <TableHead>更新时间</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {profiles.length > 0 ? (
                  profiles.map((item) => (
                    <TableRow
                      key={item.person_id}
                      className={cn(
                        'cursor-pointer',
                        selectedPersonId === item.person_id && 'bg-muted/60'
                      )}
                      onClick={() => selectProfile(item.person_id)}
                    >
                      <TableCell>
                        <div className="font-medium break-all">
                          {item.person_name || item.person_id}
                        </div>
                        {item.person_name ? (
                          <div className="text-muted-foreground mt-0.5 font-mono text-xs break-all">
                            {item.person_id}
                          </div>
                        ) : null}
                        {describeProfileIdentity(item).length > 0 ? (
                          <div className="text-muted-foreground mt-1 text-xs break-all">
                            {describeProfileIdentity(item).join(' · ')}
                          </div>
                        ) : null}
                        <div className="mt-1 flex flex-wrap gap-1">
                          {item.has_manual_override ? (
                            <Badge variant="secondary">画像覆写</Badge>
                          ) : null}
                          {item.source_note &&
                          item.source_note !== 'sdk_memory_kernel.memory_profile_admin.query' ? (
                            <Badge variant="outline">{item.source_note}</Badge>
                          ) : null}
                        </div>
                      </TableCell>
                      <TableCell>{Number(item.profile_version ?? 0)}</TableCell>
                      <TableCell>{formatMemoryTime(item.updated_at)}</TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell colSpan={3} className="text-muted-foreground text-center">
                      {loading ? (
                        <ThinkingIllustration size="sm" className="mx-auto" />
                      ) : profileListMode === 'search' ? (
                        '没有匹配的人物画像'
                      ) : (
                        '还没有人物画像快照'
                      )}
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </ScrollArea>
        </CardContent>
      </Card>

      {/* 画像详情改为弹窗：与文字记录点列表项开详情一致，不再常驻占右侧栏 */}
      <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
        <DialogContent className="max-h-[85vh] [--dialog-width:48rem]">
          <DialogHeader className="space-y-1 text-left">
            <DialogTitle className="text-sm">画像详情</DialogTitle>
            <DialogDescription>
              {detailPersonLabel
                ? `当前展示人物：${detailPersonLabel}`
                : '展示当前快照、查询结果、支撑证据和原始响应。'}
            </DialogDescription>
          </DialogHeader>
          <DialogBody contentClassName="space-y-4">
            {querying ? (
              <div className="text-muted-foreground flex items-center gap-2 text-sm">
                <Loader2 className="h-4 w-4 animate-spin" />
                正在查询人物画像
              </div>
            ) : null}
            {selectedProfile || queryResult ? (
              <>
                <div className="flex flex-wrap gap-2">
                  <Badge variant="outline">
                    {selectedPersonId || String(queryResult?.person_id ?? '未选择')}
                  </Badge>
                  {selectedProfile?.expires_at ? (
                    <Badge variant="secondary">
                      过期时间 {formatMemoryTime(selectedProfile.expires_at)}
                    </Badge>
                  ) : null}
                  {currentProfileEvidence?.has_manual_override ? (
                    <Badge variant="secondary">当前展示使用画像覆写</Badge>
                  ) : null}
                </div>
                {currentProfileEvidence?.has_manual_override ? (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      variant={!showAutoProfile ? 'default' : 'outline'}
                      size="sm"
                      onClick={() => setShowAutoProfile(false)}
                    >
                      画像覆写
                    </Button>
                    <Button
                      type="button"
                      variant={showAutoProfile ? 'default' : 'outline'}
                      size="sm"
                      onClick={() => setShowAutoProfile(true)}
                    >
                      自动画像
                    </Button>
                  </div>
                ) : null}
                <Textarea
                  value={displayedProfileText}
                  readOnly
                  className="min-h-[180px]"
                  placeholder="当前没有画像文本"
                />

                {/* 证据纠错与刷新属于检修动作，已移到记忆检修 → 画像维护 */}
                <div className="text-muted-foreground rounded-lg border border-dashed px-3 py-2 text-xs">
                  支撑证据 {currentProfileEvidence?.evidence_count ?? 0} 条；纠错与刷新入口在
                  「记忆检修 → 画像维护」。
                </div>

                <Collapsible
                  open={showRawProfilePayload}
                  onOpenChange={setShowRawProfilePayload}
                  className="bg-muted/10 rounded-lg border"
                >
                  <CollapsibleTrigger asChild>
                    <Button variant="ghost" className="flex h-10 w-full justify-between px-3">
                      <span>原始响应 JSON</span>
                      <ChevronDown
                        className={cn(
                          'h-4 w-4 transition-transform',
                          showRawProfilePayload && 'rotate-180'
                        )}
                      />
                    </Button>
                  </CollapsibleTrigger>
                  <CollapsibleContent className="border-t">
                    <pre className="max-h-72 overflow-auto p-3 text-xs break-words whitespace-pre-wrap">
                      {JSON.stringify(
                        currentProfileEvidence ?? queryResult ?? selectedProfile ?? {},
                        null,
                        2
                      )}
                    </pre>
                  </CollapsibleContent>
                </Collapsible>
              </>
            ) : (
              <div className="bg-muted/20 text-muted-foreground rounded-lg border border-dashed p-6 text-center text-sm">
                选择一个人物或执行查询后查看详情。
              </div>
            )}
          </DialogBody>
        </DialogContent>
      </Dialog>
    </div>
  )
}
