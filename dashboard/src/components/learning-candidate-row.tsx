import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import type { LearnedCandidate, LearningKind } from '@/lib/text-learning-api'

export function LearningCandidateRow({
  candidate,
  index,
  kind,
  selected,
  imported,
  importing,
  onSelectedChange,
  onChange,
}: {
  candidate: LearnedCandidate
  index: number
  kind: LearningKind
  selected: boolean
  imported: boolean
  importing: boolean
  onSelectedChange: (selected: boolean) => void
  onChange: (change: Partial<LearnedCandidate>) => void
}) {
  const [editing, setEditing] = useState(false)
  const noun = kind === 'expression' ? '表达方式' : '黑话'
  const titleLabel = kind === 'expression' ? '情境' : '黑话'
  const descriptionLabel = kind === 'expression' ? '表达方式' : '含义'
  const prefix = `${kind}-learning-candidate-${index}`
  const invalid = !candidate.title.trim() || !candidate.description.trim()

  return (
    <div className="flex items-start gap-3 rounded-md border p-3">
      <Checkbox
        id={prefix}
        className="mt-1 shrink-0"
        checked={selected}
        disabled={importing || imported}
        aria-label={`导入${noun}：${candidate.title}`}
        onCheckedChange={(checked) => onSelectedChange(checked === true)}
      />
      <div className="min-w-0 flex-1 space-y-2 text-sm break-words">
        <div className="flex items-start justify-between gap-2">
          <Label htmlFor={prefix} className="min-w-0 cursor-pointer font-medium">
            {candidate.title || `第 ${index + 1} 条${noun}`}
          </Label>
          <Button
            variant="ghost"
            size="xs"
            disabled={importing || imported || (editing && invalid)}
            aria-label={`${editing ? '完成编辑' : '编辑'}第 ${index + 1} 条${noun}`}
            onClick={() => setEditing(!editing)}
          >
            {editing ? '完成' : '编辑'}
          </Button>
        </div>
        {imported && <p className="text-muted-foreground text-xs">当前目标已导入或已存在</p>}
        {editing ? (
          <div className="space-y-2">
            <div className="space-y-1">
              <Label htmlFor={`${prefix}-title`}>{titleLabel}</Label>
              <Input
                id={`${prefix}-title`}
                aria-label={`第 ${index + 1} 条${titleLabel}`}
                maxLength={200}
                value={candidate.title}
                disabled={importing || imported}
                onChange={(event) => onChange({ title: event.target.value })}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor={`${prefix}-description`}>{descriptionLabel}</Label>
              <Textarea
                id={`${prefix}-description`}
                aria-label={`第 ${index + 1} 条${descriptionLabel}`}
                maxLength={1000}
                value={candidate.description}
                disabled={importing || imported}
                rows={2}
                onChange={(event) => onChange({ description: event.target.value })}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor={`${prefix}-evidence`}>原文依据（可选）</Label>
              <Textarea
                id={`${prefix}-evidence`}
                aria-label={`第 ${index + 1} 条原文依据（可选）`}
                maxLength={500}
                value={candidate.evidence ?? ''}
                disabled={importing || imported}
                rows={2}
                placeholder="可留空，确认内容后即可导入"
                onChange={(event) => onChange({ evidence: event.target.value })}
              />
            </div>
            {invalid && (
              <p className="text-destructive text-xs">
                请填写{titleLabel}和{descriptionLabel}。
              </p>
            )}
          </div>
        ) : (
          <>
            <p>{candidate.description}</p>
            <p className="text-muted-foreground text-xs whitespace-pre-wrap">
              {candidate.evidence?.trim()
                ? `原文：${candidate.evidence}`
                : '未提供原文依据，可确认后导入'}
            </p>
          </>
        )}
      </div>
    </div>
  )
}
