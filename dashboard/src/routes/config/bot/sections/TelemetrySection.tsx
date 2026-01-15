import React from 'react'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import type { TelemetryConfig } from '../types'

interface TelemetrySectionProps {
  config: TelemetryConfig
  onChange: (config: TelemetryConfig) => void
}

export const TelemetrySection = React.memo(function TelemetrySection({ config, onChange }: TelemetrySectionProps) {
  return (
    <div className="rounded-lg border bg-card p-4 sm:p-6 space-y-4">
      <h3 className="text-lg font-semibold">统计信息</h3>
      <div className="flex items-center justify-between">
        <div className="space-y-0.5">
          <Label>启用统计信息发送</Label>
          <p className="text-sm text-muted-foreground">
            发送匿名统计信息，帮助我们了解全球有多少只麦麦在运行
          </p>
        </div>
        <Switch
          checked={config.enable}
          onCheckedChange={(checked) => onChange({ ...config, enable: checked })}
        />
      </div>
    </div>
  )
})
