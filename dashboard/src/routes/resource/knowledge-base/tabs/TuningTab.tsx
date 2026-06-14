import { Sparkles } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { CodeEditor } from '@/components/CodeEditor'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { TabsContent } from '@/components/ui/tabs'

import type { UseMemoryTuningResult } from '../hooks/useMemoryTuning'
import { getImportStatusVariant } from '../utils'

export interface TuningTabProps {
  tuning: UseMemoryTuningResult
}

export function TuningTab({ tuning }: TuningTabProps) {
  const {
    tuningObjective,
    setTuningObjective,
    tuningIntensity,
    setTuningIntensity,
    tuningSampleSize,
    setTuningSampleSize,
    tuningTopKEval,
    setTuningTopKEval,
    submitTuningTask,
    creatingTuning,
    tuningProfile,
    tuningProfileToml,
    tuningTasks,
    applyBestTask,
  } = tuning

  return (
    <TabsContent value="tuning" className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="h-4 w-4" />
              调优任务
            </CardTitle>
            <CardDescription>创建一次检索参数评估任务，完成后可在右侧列表中查看并应用最佳结果。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-3 rounded-lg border bg-muted/20 p-4">
              <div className="space-y-1">
                <div className="text-sm font-medium">调优策略</div>
                <div className="text-xs text-muted-foreground">先选择优化方向和搜索强度。默认的 balanced / standard 适合大多数情况。</div>
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label>优化目标</Label>
                  <div className="text-xs text-muted-foreground">决定本次调优更偏向准确率、召回率，还是两者平衡。</div>
                  <Select value={tuningObjective} onValueChange={setTuningObjective}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="precision_priority">precision_priority</SelectItem>
                      <SelectItem value="balanced">balanced</SelectItem>
                      <SelectItem value="recall_priority">recall_priority</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>评估强度</Label>
                  <div className="text-xs text-muted-foreground">强度越高，评估更充分，但任务耗时也更长。</div>
                  <Select value={tuningIntensity} onValueChange={setTuningIntensity}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="quick">quick</SelectItem>
                      <SelectItem value="standard">standard</SelectItem>
                      <SelectItem value="deep">deep</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </div>
            <div className="space-y-3 rounded-lg border bg-muted/20 p-4">
              <div className="space-y-1">
                <div className="text-sm font-medium">评估范围</div>
                <div className="text-xs text-muted-foreground">控制本次任务使用多少样本，以及每次检索评估多少候选结果。</div>
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label>样本量</Label>
                  <div className="text-xs text-muted-foreground">用于评估的样本数量。数量越大，结果越稳定。</div>
                  <Input type="number" value={tuningSampleSize} onChange={(event) => setTuningSampleSize(event.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label>评估 Top-K</Label>
                  <div className="text-xs text-muted-foreground">每次检索时用于评估的候选结果数量。</div>
                  <Input type="number" value={tuningTopKEval} onChange={(event) => setTuningTopKEval(event.target.value)} />
                </div>
              </div>
            </div>
            <Button onClick={() => void submitTuningTask()} disabled={creatingTuning}>
              <Sparkles className="mr-2 h-4 w-4" />
              创建调优任务
            </Button>
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>当前调优配置快照</CardTitle>
              <CardDescription>展示当前生效的检索调优参数，便于在应用新结果前做对照。</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <CodeEditor
                value={JSON.stringify(tuningProfile, null, 2)}
                language="json"
                readOnly
                height="220px"
              />
              <CodeEditor
                value={tuningProfileToml}
                language="toml"
                readOnly
                height="180px"
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>最近调优任务</CardTitle>
              <CardDescription>任务完成后，可以把最佳结果应用到当前调优配置。</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>任务</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead>动作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tuningTasks.length > 0 ? tuningTasks.map((task) => (
                    <TableRow key={String(task.task_id ?? Math.random())}>
                      <TableCell className="font-mono text-xs">{String(task.task_id ?? '-')}</TableCell>
                      <TableCell>
                        <Badge variant={getImportStatusVariant(String(task.status ?? ''))}>
                          {String(task.status ?? '-')}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => void applyBestTask(String(task.task_id ?? ''))}
                          disabled={!task.task_id}
                        >
                          应用最佳
                        </Button>
                      </TableCell>
                    </TableRow>
                  )) : (
                    <TableRow>
                      <TableCell colSpan={3} className="text-center text-muted-foreground">
                        还没有调优任务。可以先使用默认参数创建一次评估任务。
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </div>
      </div>
    </TabsContent>
  )
}
