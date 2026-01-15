import React from 'react'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import type { DebugConfig } from '../types'

interface DebugSectionProps {
  config: DebugConfig
  onChange: (config: DebugConfig) => void
}

export const DebugSection = React.memo(function DebugSection({ config, onChange }: DebugSectionProps) {
  return (
    <div className="rounded-lg border bg-card p-4 sm:p-6 space-y-4">
      <h3 className="text-lg font-semibold">调试配置</h3>
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="space-y-0.5">
            <Label>显示 Prompt</Label>
            <p className="text-sm text-muted-foreground">是否在日志中显示提示词</p>
          </div>
          <Switch
            checked={config.show_prompt}
            onCheckedChange={(checked) => onChange({ ...config, show_prompt: checked })}
          />
        </div>

        <div className="flex items-center justify-between">
          <div className="space-y-0.5">
            <Label>显示回复器 Prompt</Label>
            <p className="text-sm text-muted-foreground">是否显示回复器的提示词</p>
          </div>
          <Switch
            checked={config.show_replyer_prompt}
            onCheckedChange={(checked) => onChange({ ...config, show_replyer_prompt: checked })}
          />
        </div>

        <div className="flex items-center justify-between">
          <div className="space-y-0.5">
            <Label>显示回复器推理</Label>
            <p className="text-sm text-muted-foreground">是否显示回复器的推理过程</p>
          </div>
          <Switch
            checked={config.show_replyer_reasoning}
            onCheckedChange={(checked) =>
              onChange({ ...config, show_replyer_reasoning: checked })
            }
          />
        </div>

        <div className="flex items-center justify-between">
          <div className="space-y-0.5">
            <Label>显示 Jargon Prompt</Label>
            <p className="text-sm text-muted-foreground">是否显示术语相关的提示词</p>
          </div>
          <Switch
            checked={config.show_jargon_prompt}
            onCheckedChange={(checked) => onChange({ ...config, show_jargon_prompt: checked })}
          />
        </div>

        <div className="flex items-center justify-between">
          <div className="space-y-0.5">
            <Label>显示记忆检索 Prompt</Label>
            <p className="text-sm text-muted-foreground">是否显示记忆检索相关的提示词</p>
          </div>
          <Switch
            checked={config.show_memory_prompt}
            onCheckedChange={(checked) => onChange({ ...config, show_memory_prompt: checked })}
          />
        </div>

        <div className="flex items-center justify-between">
          <div className="space-y-0.5">
            <Label>显示 Planner Prompt</Label>
            <p className="text-sm text-muted-foreground">是否显示 Planner 的提示词和原始返回结果</p>
          </div>
          <Switch
            checked={config.show_planner_prompt}
            onCheckedChange={(checked) => onChange({ ...config, show_planner_prompt: checked })}
          />
        </div>

        <div className="flex items-center justify-between">
          <div className="space-y-0.5">
            <Label>显示 LPMM 相关文段</Label>
            <p className="text-sm text-muted-foreground">是否显示 LPMM 知识库找到的相关文段日志</p>
          </div>
          <Switch
            checked={config.show_lpmm_paragraph}
            onCheckedChange={(checked) => onChange({ ...config, show_lpmm_paragraph: checked })}
          />
        </div>
      </div>
    </div>
  )
})
