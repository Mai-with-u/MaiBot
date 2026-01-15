import React from 'react'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import type { VoiceConfig } from '../types'

interface VoiceSectionProps {
  config: VoiceConfig
  onChange: (config: VoiceConfig) => void
}

export const VoiceSection = React.memo(function VoiceSection({ config, onChange }: VoiceSectionProps) {
  return (
    <div className="rounded-lg border bg-card p-4 sm:p-6 space-y-4">
      <h3 className="text-lg font-semibold">语音设置</h3>
      <div className="flex items-center space-x-2">
        <Switch
          checked={config.enable_asr}
          onCheckedChange={(checked) => onChange({ ...config, enable_asr: checked })}
        />
        <Label className="cursor-pointer">启用语音识别</Label>
      </div>
      <p className="text-xs text-muted-foreground">
        启用后麦麦可以识别语音消息，需要配置语音识别模型
      </p>
    </div>
  )
})
