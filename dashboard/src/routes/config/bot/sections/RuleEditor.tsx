
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Slider } from '@/components/ui/slider'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'

import { Trash2 } from 'lucide-react'

import { RulePreview } from './RulePreview'
import { TimeRangePicker } from './TimeRangePicker'

interface TalkValueRule {
  target: string
  time: string
  value: number
}

interface RuleEditorProps {
  rule: TalkValueRule
  index: number
  onUpdate: (index: number, field: 'target' | 'time' | 'value', value: string | number) => void
  onRemove: (index: number) => void
}

// 规则编辑器组件
export function RuleEditor({ rule, index, onUpdate, onRemove }: RuleEditorProps) {
  return (
    <div className="rounded-lg border p-4 bg-muted/50 space-y-4">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-muted-foreground">
          规则 #{index + 1}
        </span>
        <div className="flex items-center gap-2">
          <RulePreview rule={rule} />
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button variant="ghost" size="sm">
                <Trash2 className="h-4 w-4 text-destructive" />
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>确认删除</AlertDialogTitle>
                <AlertDialogDescription>
                  确定要删除规则 #{index + 1} 吗？此操作无法撤销。
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>取消</AlertDialogCancel>
                <AlertDialogAction onClick={() => onRemove(index)}>
                  删除
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </div>

      <div className="space-y-4">
        {/* 配置类型选择 */}
        <div className="grid gap-2">
          <Label className="text-xs font-medium">配置类型</Label>
          <Select
            value={rule.target === '' ? 'global' : 'specific'}
            onValueChange={(value) => {
              if (value === 'global') {
                onUpdate(index, 'target', '')
              } else {
                onUpdate(index, 'target', 'qq::group')
              }
            }}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="global">全局配置</SelectItem>
              <SelectItem value="specific">详细配置</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {/* 详细配置选项 - 只在非全局时显示 */}
        {rule.target !== '' && (() => {
          const parts = rule.target.split(':')
          const platform = parts[0] || 'qq'
          const chatId = parts[1] || ''
          const chatType = parts[2] || 'group'
          
          return (
            <div className="grid gap-4 p-3 sm:p-4 rounded-lg bg-muted/50">
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="grid gap-2">
                  <Label className="text-xs font-medium">平台</Label>
                  <Select
                    value={platform}
                    onValueChange={(value) => {
                      onUpdate(index, 'target', `${value}:${chatId}:${chatType}`)
                    }}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="qq">QQ</SelectItem>
                      <SelectItem value="wx">微信</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="grid gap-2">
                  <Label className="text-xs font-medium">群 ID</Label>
                  <Input
                    value={chatId}
                    onChange={(e) => {
                      onUpdate(index, 'target', `${platform}:${e.target.value}:${chatType}`)
                    }}
                    placeholder="输入群 ID"
                    className="font-mono text-sm"
                  />
                </div>

                <div className="grid gap-2">
                  <Label className="text-xs font-medium">类型</Label>
                  <Select
                    value={chatType}
                    onValueChange={(value) => {
                      onUpdate(index, 'target', `${platform}:${chatId}:${value}`)
                    }}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="group">群组（group）</SelectItem>
                      <SelectItem value="private">私聊（private）</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                当前聊天流 ID：{rule.target || '（未设置）'}
              </p>
            </div>
          )
        })()}

        {/* 时间段选择器 */}
        <div className="grid gap-2">
          <Label className="text-xs font-medium">时间段 (Time)</Label>
          <TimeRangePicker
            value={rule.time}
            onChange={(v) => onUpdate(index, 'time', v)}
          />
          <p className="text-xs text-muted-foreground">
            支持跨夜区间，例如 23:00-02:00
          </p>
        </div>

        {/* 发言频率滑块 */}
        <div className="grid gap-3">
          <div className="flex items-center justify-between">
            <Label htmlFor={`rule-value-${index}`} className="text-xs font-medium">
              发言频率值 (Value)
            </Label>
            <Input
              id={`rule-value-${index}`}
              type="number"
              step="0.01"
              min="0.01"
              max="1"
              value={rule.value}
              onChange={(e) => {
                const val = parseFloat(e.target.value)
                if (!isNaN(val)) {
                  onUpdate(index, 'value', Math.max(0.01, Math.min(1, val)))
                }
              }}
              className="w-20 h-8 text-xs"
            />
          </div>
          <Slider
            value={[rule.value]}
            onValueChange={(values) =>
              onUpdate(index, 'value', values[0])
            }
            min={0.01}
            max={1}
            step={0.01}
            className="w-full"
          />
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>0.01 (极少发言)</span>
            <span>0.5</span>
            <span>1.0 (正常)</span>
          </div>
        </div>
      </div>
    </div>
  )
}
