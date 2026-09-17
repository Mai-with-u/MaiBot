/**
 * ProfileMaintenancePanel —— 记忆检修 → 「画像维护」子模式。
 *
 * 收编三块人工治理动作：
 * - 支撑证据：查看证据明细、强制刷新、逐条纠错（纠错后会刷新自动画像）；
 * - 别名维护：人工别名生效 / 回退到可信自动别名、确认共同出现候选；
 * - 画像覆写：写入人工画像固定展示结果，或删除覆写记录。
 *
 * 这些动作都依赖「当前定位人物」，因此与 ProfileSearchPanel 共享同一份
 * useMemoryProfileConsole 状态：在记忆查询里选中的人物会直接带到本面板。
 */
import { Check, Loader2, Plus, RefreshCw, RotateCcw, Save, Trash2 } from 'lucide-react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
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
import { cn } from '@/lib/utils'

import { evidenceTypeLabel, formatEvidenceScore, parseAliasText } from '../profile-utils'
import type { UseMemoryProfileConsoleResult } from '../hooks/useMemoryProfileConsole'

export interface ProfileMaintenancePanelProps {
  profile: UseMemoryProfileConsoleResult
}

export function ProfileMaintenancePanel({ profile }: ProfileMaintenancePanelProps) {
  const {
    activePersonId,
    selectedPersonId,
    selectedDisplayName,
    currentProfileEvidence,
    evidenceLoading,
    evidenceLimit,
    setEvidenceLimit,
    loadProfileEvidence,
    correctEvidence,
    correctingEvidenceKey,
    profileAliases,
    aliasText,
    setAliasText,
    aliasLoading,
    aliasSaving,
    saveAliases,
    addSuggestedAlias,
    restoreDerivedAliases,
    overrideText,
    setOverrideText,
    saving,
    saveOverride,
    deleteOverride,
  } = profile

  const hasPersonTarget = Boolean(activePersonId.trim())
  const parsedAliases = parseAliasText(aliasText)

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>支撑证据</CardTitle>
          <CardDescription>当前画像引用的证据明细；停用证据后会立即刷新自动画像。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {!hasPersonTarget ? (
            <Alert>
              <AlertDescription>
                请先在「记忆查询 → 人物画像」中选择或查询一个 person_id。
              </AlertDescription>
            </Alert>
          ) : null}
          {/* 展示条数上限：只决定这张表列多少条证据，不影响画像本身 */}
          <div className="flex flex-wrap items-center gap-2">
            <Label htmlFor="profile-evidence-limit" className="text-sm font-normal">
              证据数量
            </Label>
            <Input
              id="profile-evidence-limit"
              type="number"
              className="h-8 w-20"
              value={evidenceLimit}
              onChange={(event) => setEvidenceLimit(event.target.value)}
            />
            <span className="text-muted-foreground text-xs">
              列表显示上限；改变后点「刷新证据」生效
            </span>
          </div>
          <div className="rounded-lg border">
            <div className="flex items-center justify-between gap-3 border-b px-3 py-2">
              <div>
                <div className="text-sm font-medium">证据明细</div>
                <div className="text-muted-foreground text-xs">
                  {currentProfileEvidence?.evidence_count ?? 0} 条证据；纠错后会自动刷新自动画像。
                </div>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={!hasPersonTarget || evidenceLoading}
                onClick={() => void loadProfileEvidence(activePersonId, { forceRefresh: true })}
              >
                <RefreshCw className={cn('mr-2 h-4 w-4', evidenceLoading && 'animate-spin')} />
                刷新证据
              </Button>
            </div>
            <ScrollArea className="h-[300px]">
              <Table>
                <TableHeader className="bg-background sticky top-0">
                  <TableRow>
                    <TableHead>类型</TableHead>
                    <TableHead>内容</TableHead>
                    <TableHead>来源</TableHead>
                    <TableHead>分数</TableHead>
                    <TableHead>操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(currentProfileEvidence?.evidence ?? []).length > 0 ? (
                    (currentProfileEvidence?.evidence ?? []).map((item) => {
                      const evidenceKey = String(item.evidence_key ?? item.hash ?? '')
                      const isCorrecting = correctingEvidenceKey === evidenceKey
                      return (
                        <TableRow key={evidenceKey}>
                          <TableCell>
                            <Badge variant="outline">{evidenceTypeLabel(item.evidence_type)}</Badge>
                          </TableCell>
                          <TableCell className="max-w-[320px]">
                            <div className="line-clamp-3 text-sm">{item.content || '-'}</div>
                            {item.hash ? (
                              <div className="text-muted-foreground mt-1 font-mono text-xs break-all">
                                {item.hash}
                              </div>
                            ) : null}
                          </TableCell>
                          <TableCell className="max-w-[220px]">
                            <div className="text-muted-foreground line-clamp-2 text-xs">
                              {item.source || item.source_type || '-'}
                            </div>
                          </TableCell>
                          <TableCell className="text-xs whitespace-nowrap">
                            {formatEvidenceScore(item)}
                          </TableCell>
                          <TableCell>
                            {item.deletable ? (
                              <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                disabled={Boolean(correctingEvidenceKey)}
                                onClick={() => void correctEvidence(item)}
                              >
                                {isCorrecting ? (
                                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                ) : (
                                  <Trash2 className="mr-2 h-4 w-4" />
                                )}
                                纠错并刷新
                              </Button>
                            ) : (
                              <span className="text-muted-foreground text-xs">
                                {item.not_deletable_reason || '不可操作'}
                              </span>
                            )}
                          </TableCell>
                        </TableRow>
                      )
                    })
                  ) : (
                    <TableRow>
                      <TableCell colSpan={5} className="text-muted-foreground text-center">
                        {evidenceLoading ? '正在加载画像证据' : '当前没有可展示的支撑证据'}
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </ScrollArea>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>别名维护</CardTitle>
          <CardDescription>
            可信身份字段会自动生效；共同出现实体必须人工确认后才能加入别名。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {!hasPersonTarget ? (
            <Alert>
              <AlertDescription>请先在「记忆查询 → 人物画像」中选择一个人物。</AlertDescription>
            </Alert>
          ) : null}
          <div className="text-muted-foreground flex flex-wrap items-center gap-2 text-xs">
            <Badge variant={profileAliases?.has_override ? 'secondary' : 'outline'}>
              {profileAliases?.has_override ? '人工别名生效中' : '可信自动别名生效中'}
            </Badge>
            {aliasLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          </div>
          {(profileAliases?.derived_aliases ?? []).length > 0 ? (
            <div className="space-y-2">
              <Label>可信自动别名</Label>
              <div className="flex flex-wrap gap-1.5">
                {(profileAliases?.derived_aliases ?? []).map((alias) => (
                  <Badge key={alias} variant="outline">
                    {alias}
                  </Badge>
                ))}
              </div>
            </div>
          ) : null}
          {(profileAliases?.suggested_aliases ?? []).length > 0 ? (
            <div className="space-y-2">
              <div>
                <Label>待确认候选</Label>
                <p className="text-muted-foreground mt-1 text-xs">
                  来自共同出现实体，不会自动参与检索。
                </p>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {(profileAliases?.suggested_aliases ?? []).map((alias) => {
                  const included = parsedAliases.some(
                    (item) => item.toLocaleLowerCase() === alias.toLocaleLowerCase()
                  )
                  return (
                    <Button
                      key={alias}
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => addSuggestedAlias(alias)}
                      disabled={included || aliasLoading || aliasSaving}
                    >
                      {included ? (
                        <Check className="mr-1.5 h-3.5 w-3.5" />
                      ) : (
                        <Plus className="mr-1.5 h-3.5 w-3.5" />
                      )}
                      {included ? '已加入' : '加入'} {alias}
                    </Button>
                  )
                })}
              </div>
            </div>
          ) : null}
          <div className="space-y-2">
            <Label htmlFor="profile-aliases">当前有效别名</Label>
            <Textarea
              id="profile-aliases"
              value={aliasText}
              onChange={(event) => setAliasText(event.target.value)}
              className="min-h-[120px]"
              placeholder="每行填写一个别名"
              disabled={!hasPersonTarget || aliasLoading}
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              onClick={() => void saveAliases()}
              disabled={!hasPersonTarget || aliasLoading || aliasSaving}
            >
              {aliasSaving ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Save className="mr-2 h-4 w-4" />
              )}
              保存别名
            </Button>
            <Button
              variant="outline"
              onClick={() => void restoreDerivedAliases()}
              disabled={!profileAliases?.has_override || aliasSaving}
            >
              <RotateCcw className="mr-2 h-4 w-4" />
              恢复可信自动别名
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>画像覆写</CardTitle>
          <CardDescription>
            用人工画像固定展示结果；留空保存表示清空文本但保留画像覆写记录。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {!selectedPersonId ? (
            <Alert>
              <AlertDescription>请先在「记忆查询 → 人物画像」中选择一个人物。</AlertDescription>
            </Alert>
          ) : null}
          {selectedDisplayName ? (
            <div className="text-muted-foreground text-sm">当前编辑对象：{selectedDisplayName}</div>
          ) : null}
          <Textarea
            value={overrideText}
            onChange={(event) => setOverrideText(event.target.value)}
            className="min-h-[180px]"
            placeholder="输入希望固定使用的人物画像文本"
          />
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => void saveOverride()} disabled={saving}>
              <Save className="mr-2 h-4 w-4" />
              保存画像覆写
            </Button>
            <Button
              variant="outline"
              onClick={() => void deleteOverride()}
              disabled={saving || !selectedPersonId}
            >
              <Trash2 className="mr-2 h-4 w-4" />
              删除画像覆写
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
