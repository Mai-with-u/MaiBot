import { useState } from 'react'
import { Loader2, Save } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import type { MemoryFactWritePayload, MemoryRecordPayload } from '@/lib/memory-api'

interface MemoryFactEditorDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  record: MemoryRecordPayload | null
  saving: boolean
  onSubmit: (payload: MemoryFactWritePayload) => void
}

const PROFILE_SECTIONS = [
  ['identity_settings', '身份设定'],
  ['relationship_settings', '关系设定'],
  ['stable_facts', '稳定了解'],
  ['interaction_preferences', '相处偏好'],
  ['recent_interactions', '近期互动'],
  ['uncertain_notes', '不确定信息'],
] as const

function initialPayload(record: MemoryRecordPayload | null): MemoryFactWritePayload {
  const metadata = record?.metadata ?? {}
  return {
    scope_type: metadata.scope_type === 'chat' ? 'chat' : 'person',
    scope_id: String(metadata.scope_id ?? ''),
    fact_key: String(metadata.fact_key ?? ''),
    value_text: String(metadata.value_text ?? ''),
    polarity: metadata.polarity === 'negative' ? 'negative' : 'positive',
    cardinality: metadata.cardinality === 'single' ? 'single' : 'set',
    stability:
      metadata.stability === 'temporal' || metadata.stability === 'uncertain'
        ? metadata.stability
        : 'stable',
    profile_section: String(metadata.profile_section ?? 'stable_facts'),
    authority:
      metadata.authority === 'direct_user' ||
      metadata.authority === 'imported' ||
      metadata.authority === 'summary_derived'
        ? metadata.authority
        : 'manual',
    confidence: Number(metadata.confidence ?? 1),
    reason: record ? 'knowledge_base_fact_update' : 'knowledge_base_fact_create',
    updated_by: 'knowledge_base',
  }
}

export function MemoryFactEditorDialog({
  open,
  onOpenChange,
  record,
  saving,
  onSubmit,
}: MemoryFactEditorDialogProps) {
  const [form, setForm] = useState<MemoryFactWritePayload>(() => initialPayload(record))

  const setValue = <K extends keyof MemoryFactWritePayload>(
    key: K,
    value: MemoryFactWritePayload[K]
  ) => setForm((current) => ({ ...current, [key]: value }))

  const canSubmit = Boolean(form.scope_id.trim() && form.fact_key.trim() && form.value_text.trim())

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{record ? '编辑结构化事实' : '新增结构化事实'}</DialogTitle>
          <DialogDescription>
            内容变化会生成新版本并保留旧事实的状态记录，分类变化会直接更新当前 claim。
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-2 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="fact-scope-type">归属类型</Label>
            <Select
              value={form.scope_type}
              onValueChange={(value) => setValue('scope_type', value as 'person' | 'chat')}
              disabled={Boolean(record)}
            >
              <SelectTrigger id="fact-scope-type"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="person">人物</SelectItem>
                <SelectItem value="chat">聊天流</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="fact-scope-id">归属 ID</Label>
            <Input
              id="fact-scope-id"
              value={form.scope_id}
              onChange={(event) => setValue('scope_id', event.target.value)}
              disabled={Boolean(record)}
            />
          </div>
          <div className="space-y-2 sm:col-span-2">
            <Label htmlFor="fact-key">事实键</Label>
            <Input id="fact-key" value={form.fact_key} onChange={(event) => setValue('fact_key', event.target.value)} />
          </div>
          <div className="space-y-2 sm:col-span-2">
            <Label htmlFor="fact-value">事实内容</Label>
            <Textarea
              id="fact-value"
              value={form.value_text}
              onChange={(event) => setValue('value_text', event.target.value)}
              className="min-h-24"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="fact-profile-section">画像分类</Label>
            <Select value={form.profile_section} onValueChange={(value) => setValue('profile_section', value)}>
              <SelectTrigger id="fact-profile-section"><SelectValue /></SelectTrigger>
              <SelectContent>
                {PROFILE_SECTIONS.map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="fact-stability">稳定性</Label>
            <Select
              value={form.stability}
              onValueChange={(value) => setValue('stability', value as MemoryFactWritePayload['stability'])}
            >
              <SelectTrigger id="fact-stability"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="stable">稳定</SelectItem>
                <SelectItem value="temporal">时效性</SelectItem>
                <SelectItem value="uncertain">不确定</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="fact-cardinality">取值方式</Label>
            <Select
              value={form.cardinality}
              onValueChange={(value) => setValue('cardinality', value as MemoryFactWritePayload['cardinality'])}
            >
              <SelectTrigger id="fact-cardinality"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="single">单值</SelectItem>
                <SelectItem value="set">多值集合</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="fact-polarity">极性</Label>
            <Select
              value={form.polarity}
              onValueChange={(value) => setValue('polarity', value as MemoryFactWritePayload['polarity'])}
            >
              <SelectTrigger id="fact-polarity"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="positive">肯定</SelectItem>
                <SelectItem value="negative">否定</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="fact-authority">信息来源级别</Label>
            <Select
              value={form.authority}
              onValueChange={(value) => setValue('authority', value as MemoryFactWritePayload['authority'])}
            >
              <SelectTrigger id="fact-authority"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="manual">人工维护</SelectItem>
                <SelectItem value="direct_user">用户直接陈述</SelectItem>
                <SelectItem value="imported">可信导入</SelectItem>
                <SelectItem value="summary_derived">摘要推导</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="fact-confidence">置信度</Label>
            <Input
              id="fact-confidence"
              type="number"
              min="0"
              max="1"
              step="0.05"
              value={form.confidence}
              onChange={(event) => setValue('confidence', Number(event.target.value))}
            />
          </div>
          <div className="space-y-2 sm:col-span-2">
            <Label htmlFor="fact-reason">修改原因</Label>
            <Input id="fact-reason" value={form.reason} onChange={(event) => setValue('reason', event.target.value)} />
          </div>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>取消</Button>
          <Button type="button" onClick={() => onSubmit(form)} disabled={!canSubmit || saving}>
            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
            保存事实
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
