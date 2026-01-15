import React, { useState } from 'react'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { X } from 'lucide-react'
import type { DreamConfig } from '../types'

interface DreamSectionProps {
  config: DreamConfig
  onChange: (config: DreamConfig) => void
}

interface TimeRange {
  startTime: string
  endTime: string
}

export const DreamSection = React.memo(function DreamSection({ config, onChange }: DreamSectionProps) {
  // 解析 dream_send 为 platform 和 userId
  const parseDreamSend = (dreamSend: string): { platform: string; userId: string } => {
    if (!dreamSend || !dreamSend.includes(':')) {
      return { platform: 'qq', userId: '' }
    }
    const [platform, userId] = dreamSend.split(':')
    return { platform, userId }
  }

  const { platform: initialPlatform, userId: initialUserId } = parseDreamSend(config.dream_send)
  const [platform, setPlatform] = useState(initialPlatform)
  const [userId, setUserId] = useState(initialUserId)

  // 解析时间段字符串为开始和结束时间
  const parseTimeRange = (range: string): TimeRange => {
    const [start, end] = range.split('-')
    return { startTime: start || '09:00', endTime: end || '22:00' }
  }

  // 更新 dream_send
  const updateDreamSend = (newPlatform: string, newUserId: string) => {
    const dreamSend = newUserId ? `${newPlatform}:${newUserId}` : ''
    onChange({ ...config, dream_send: dreamSend })
  }

  const handlePlatformChange = (value: string) => {
    setPlatform(value)
    updateDreamSend(value, userId)
  }

  const handleUserIdChange = (value: string) => {
    setUserId(value)
    updateDreamSend(platform, value)
  }

  const handleAddTimeRange = () => {
    onChange({
      ...config,
      dream_time_ranges: [...config.dream_time_ranges, '09:00-22:00']
    })
  }

  const handleRemoveTimeRange = (index: number) => {
    onChange({
      ...config,
      dream_time_ranges: config.dream_time_ranges.filter((_, i) => i !== index)
    })
  }

  const handleTimeRangeChange = (index: number, field: 'startTime' | 'endTime', value: string) => {
    const newRanges = [...config.dream_time_ranges]
    const currentRange = parseTimeRange(newRanges[index])
    
    if (field === 'startTime') {
      currentRange.startTime = value
    } else {
      currentRange.endTime = value
    }
    
    newRanges[index] = `${currentRange.startTime}-${currentRange.endTime}`
    onChange({
      ...config,
      dream_time_ranges: newRanges
    })
  }

  return (
    <div className="rounded-lg border bg-card p-4 sm:p-6 space-y-6">
      <h3 className="text-lg font-semibold">做梦配置</h3>
      
      <div className="space-y-2">
        <Label htmlFor="interval_minutes">做梦时间间隔（分钟）</Label>
        <Input
          id="interval_minutes"
          type="number"
          min="1"
          value={config.interval_minutes}
          onChange={(e) => onChange({ ...config, interval_minutes: Number(e.target.value) })}
        />
        <p className="text-xs text-muted-foreground">默认30分钟</p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="max_iterations">做梦最大轮次</Label>
        <Input
          id="max_iterations"
          type="number"
          min="1"
          value={config.max_iterations}
          onChange={(e) => onChange({ ...config, max_iterations: Number(e.target.value) })}
        />
        <p className="text-xs text-muted-foreground">默认20轮</p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="first_delay_seconds">首次做梦延迟（秒）</Label>
        <Input
          id="first_delay_seconds"
          type="number"
          min="0"
          value={config.first_delay_seconds}
          onChange={(e) => onChange({ ...config, first_delay_seconds: Number(e.target.value) })}
        />
        <p className="text-xs text-muted-foreground">程序启动后首次做梦前的延迟时间，默认60秒</p>
      </div>

      <div className="space-y-2">
        <Label>做梦结果推送目标</Label>
        <div className="flex gap-2">
          <Select value={platform} onValueChange={handlePlatformChange}>
            <SelectTrigger className="w-[120px]">
              <SelectValue placeholder="选择平台" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="qq">QQ</SelectItem>
              <SelectItem value="wx">微信</SelectItem>
              <SelectItem value="webui">WebUI</SelectItem>
            </SelectContent>
          </Select>
          <Input
            type="text"
            placeholder="输入用户ID (例如: 123456)"
            value={userId}
            onChange={(e) => handleUserIdChange(e.target.value)}
            className="flex-1"
          />
        </div>
        <p className="text-xs text-muted-foreground">
          选择平台并输入用户ID，做梦结束后将梦境发送给该用户。用户ID为空则不推送
        </p>
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label>做梦时间段配置</Label>
          <Button type="button" size="sm" onClick={handleAddTimeRange}>
            添加时间段
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          设置允许做梦的时间段，支持跨夜区间（如 23:00 到次日 02:00）。列表为空则全天允许做梦
        </p>
        <div className="space-y-2">
          {config.dream_time_ranges.map((range, index) => {
            const { startTime, endTime } = parseTimeRange(range)
            return (
              <div key={index} className="flex items-center gap-2">
                <Input
                  type="time"
                  value={startTime}
                  onChange={(e) => handleTimeRangeChange(index, 'startTime', e.target.value)}
                  className="w-[140px]"
                />
                <span className="text-muted-foreground">至</span>
                <Input
                  type="time"
                  value={endTime}
                  onChange={(e) => handleTimeRangeChange(index, 'endTime', e.target.value)}
                  className="w-[140px]"
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  onClick={() => handleRemoveTimeRange(index)}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            )
          })}
          {config.dream_time_ranges.length === 0 && (
            <p className="text-sm text-muted-foreground">当前配置为全天允许做梦</p>
          )}
        </div>
      </div>

      <div className="space-y-2">
        <div className="flex items-center space-x-2">
          <Switch
            id="dream_visible"
            checked={config.dream_visible}
            onCheckedChange={(checked) => onChange({ ...config, dream_visible: checked })}
          />
          <Label htmlFor="dream_visible" className="cursor-pointer">
            梦境结果存储到上下文
          </Label>
        </div>
        <p className="text-xs text-muted-foreground">
          开启后，梦境发送给配置的用户后，也会存储到聊天上下文中，在后续对话中可见
        </p>
      </div>
    </div>
  )
})
