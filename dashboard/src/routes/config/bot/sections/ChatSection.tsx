import React, { useState, useEffect, useMemo } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Slider } from '@/components/ui/slider'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
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
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { Plus, Trash2, Eye, Clock } from 'lucide-react'
import type { ChatConfig } from '../types'

interface ChatSectionProps {
  config: ChatConfig
  onChange: (config: ChatConfig) => void
}

// 时间选择组件
const TimeRangePicker = React.memo(function TimeRangePicker({
  value,
  onChange,
}: {
  value: string
  onChange: (value: string) => void
}) {
  // 解析初始值
  const parsedValue = useMemo(() => {
    const parts = value.split('-')
    if (parts.length === 2) {
      const [start, end] = parts
      const [sh, sm] = start.split(':')
      const [eh, em] = end.split(':')
      return {
        startHour: sh ? sh.padStart(2, '0') : '00',
        startMinute: sm ? sm.padStart(2, '0') : '00',
        endHour: eh ? eh.padStart(2, '0') : '23',
        endMinute: em ? em.padStart(2, '0') : '59',
      }
    }
    return {
      startHour: '00',
      startMinute: '00',
      endHour: '23',
      endMinute: '59',
    }
  }, [value])

  const [startHour, setStartHour] = useState(parsedValue.startHour)
  const [startMinute, setStartMinute] = useState(parsedValue.startMinute)
  const [endHour, setEndHour] = useState(parsedValue.endHour)
  const [endMinute, setEndMinute] = useState(parsedValue.endMinute)

  // 当value变化时同步状态
  useEffect(() => {
    setStartHour(parsedValue.startHour)
    setStartMinute(parsedValue.startMinute)
    setEndHour(parsedValue.endHour)
    setEndMinute(parsedValue.endMinute)
  }, [parsedValue])

  const updateTime = (
    newStartHour: string,
    newStartMinute: string,
    newEndHour: string,
    newEndMinute: string
  ) => {
    const newValue = `${newStartHour}:${newStartMinute}-${newEndHour}:${newEndMinute}`
    onChange(newValue)
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" className="w-full justify-start font-mono text-sm">
          <Clock className="h-4 w-4 mr-2" />
          {value || '选择时间段'}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-72 sm:w-80">
        <div className="space-y-4">
          <div>
            <h4 className="font-medium text-sm mb-3">开始时间</h4>
            <div className="grid grid-cols-2 gap-2 sm:gap-3">
              <div>
                <Label className="text-xs">小时</Label>
                <Select
                  value={startHour}
                  onValueChange={(v) => {
                    setStartHour(v)
                    updateTime(v, startMinute, endHour, endMinute)
                  }}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Array.from({ length: 24 }, (_, i) => i).map((h) => (
                      <SelectItem key={h} value={h.toString().padStart(2, '0')}>
                        {h.toString().padStart(2, '0')}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label className="text-xs">分钟</Label>
                <Select
                  value={startMinute}
                  onValueChange={(v) => {
                    setStartMinute(v)
                    updateTime(startHour, v, endHour, endMinute)
                  }}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Array.from({ length: 60 }, (_, i) => i).map((m) => (
                      <SelectItem key={m} value={m.toString().padStart(2, '0')}>
                        {m.toString().padStart(2, '0')}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>
          <div>
            <h4 className="font-medium text-sm mb-3">结束时间</h4>
            <div className="grid grid-cols-2 gap-2 sm:gap-3">
              <div>
                <Label className="text-xs">小时</Label>
                <Select
                  value={endHour}
                  onValueChange={(v) => {
                    setEndHour(v)
                    updateTime(startHour, startMinute, v, endMinute)
                  }}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Array.from({ length: 24 }, (_, i) => i).map((h) => (
                      <SelectItem key={h} value={h.toString().padStart(2, '0')}>
                        {h.toString().padStart(2, '0')}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label className="text-xs">分钟</Label>
                <Select
                  value={endMinute}
                  onValueChange={(v) => {
                    setEndMinute(v)
                    updateTime(startHour, startMinute, endHour, v)
                  }}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Array.from({ length: 60 }, (_, i) => i).map((m) => (
                      <SelectItem key={m} value={m.toString().padStart(2, '0')}>
                        {m.toString().padStart(2, '0')}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
})

// 预览窗口组件
const RulePreview = React.memo(function RulePreview({ rule }: { rule: { target: string; time: string; value: number } }) {
  const previewText = `{ target = "${rule.target}", time = "${rule.time}", value = ${rule.value.toFixed(1)} }`
  
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm">
          <Eye className="h-4 w-4 mr-1" />
          预览
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 sm:w-96">
        <div className="space-y-2">
          <h4 className="font-medium text-sm">配置预览</h4>
          <div className="rounded-md bg-muted p-3 font-mono text-xs break-all">
            {previewText}
          </div>
          <p className="text-xs text-muted-foreground">
            这是保存到 bot_config.toml 文件中的格式
          </p>
        </div>
      </PopoverContent>
    </Popover>
  )
})

export const ChatSection = React.memo(function ChatSection({ config, onChange }: ChatSectionProps) {
  // 添加发言频率规则
  const addTalkValueRule = () => {
    onChange({
      ...config,
      talk_value_rules: [
        ...config.talk_value_rules,
        { target: '', time: '00:00-23:59', value: 1.0 },
      ],
    })
  }

  // 删除发言频率规则
  const removeTalkValueRule = (index: number) => {
    onChange({
      ...config,
      talk_value_rules: config.talk_value_rules.filter((_, i) => i !== index),
    })
  }

  // 更新发言频率规则
  const updateTalkValueRule = (
    index: number,
    field: 'target' | 'time' | 'value',
    value: string | number
  ) => {
    const newRules = [...config.talk_value_rules]
    newRules[index] = {
      ...newRules[index],
      [field]: value,
    }
    onChange({
      ...config,
      talk_value_rules: newRules,
    })
  }

  return (
    <div className="rounded-lg border bg-card p-4 sm:p-6 space-y-6">
      <div>
        <h3 className="text-lg font-semibold mb-4">聊天设置</h3>
        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="talk_value">聊天频率（基础值）</Label>
            <Input
              id="talk_value"
              type="number"
              step="0.1"
              min="0"
              max="1"
              value={config.talk_value}
              onChange={(e) => onChange({ ...config, talk_value: parseFloat(e.target.value) })}
            />
            <p className="text-xs text-muted-foreground">越小越沉默，范围 0-1</p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="think_mode">思考模式</Label>
            <Select
              value={config.think_mode || 'classic'}
              onValueChange={(value) => onChange({ ...config, think_mode: value as 'classic' | 'deep' | 'dynamic' })}
            >
              <SelectTrigger id="think_mode">
                <SelectValue placeholder="选择思考模式" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="classic">经典模式 - 浅度思考和回复</SelectItem>
                <SelectItem value="deep">深度模式 - 进行深度思考和回复</SelectItem>
                <SelectItem value="dynamic">动态模式 - 自动选择思考深度</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              控制麦麦的思考深度。经典模式回复快但简单；深度模式更深入但较慢；动态模式根据情况自动选择
            </p>
          </div>

          <div className="flex items-center space-x-2">
            <Switch
              id="mentioned_bot_reply"
              checked={config.mentioned_bot_reply}
              onCheckedChange={(checked) =>
                onChange({ ...config, mentioned_bot_reply: checked })
              }
            />
            <Label htmlFor="mentioned_bot_reply" className="cursor-pointer">
              启用提及必回复
            </Label>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="max_context_size">上下文长度</Label>
            <Input
              id="max_context_size"
              type="number"
              min="1"
              value={config.max_context_size}
              onChange={(e) =>
                onChange({ ...config, max_context_size: parseInt(e.target.value) })
              }
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="planner_smooth">规划器平滑</Label>
            <Input
              id="planner_smooth"
              type="number"
              step="1"
              min="0"
              value={config.planner_smooth}
              onChange={(e) =>
                onChange({ ...config, planner_smooth: parseFloat(e.target.value) })
              }
            />
            <p className="text-xs text-muted-foreground">
              增大数值会减小 planner 负荷，推荐 1-5，0 为关闭
            </p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="plan_reply_log_max_per_chat">每个聊天流最大日志数量</Label>
            <Input
              id="plan_reply_log_max_per_chat"
              type="number"
              step="1"
              min="100"
              value={config.plan_reply_log_max_per_chat ?? 1024}
              onChange={(e) =>
                onChange({ ...config, plan_reply_log_max_per_chat: parseInt(e.target.value) })
              }
            />
            <p className="text-xs text-muted-foreground">
              每个聊天流保存的 Plan/Reply 日志最大数量，超过此数量时会自动删除最老的日志
            </p>
          </div>

          <div className="flex items-center space-x-2">
            <Switch
              id="llm_quote"
              checked={config.llm_quote ?? false}
              onCheckedChange={(checked) =>
                onChange({ ...config, llm_quote: checked })
              }
            />
            <Label htmlFor="llm_quote" className="cursor-pointer">
              启用 LLM 控制引用
            </Label>
          </div>
          <p className="text-xs text-muted-foreground -mt-2 ml-10">
            启用后，LLM 可以决定是否在回复时引用消息
          </p>

          <div className="flex items-center space-x-2">
            <Switch
              id="enable_talk_value_rules"
              checked={config.enable_talk_value_rules}
              onCheckedChange={(checked) =>
                onChange({ ...config, enable_talk_value_rules: checked })
              }
            />
            <Label htmlFor="enable_talk_value_rules" className="cursor-pointer">
              启用动态发言频率规则
            </Label>
          </div>
        </div>
      </div>

      {/* 动态发言频率规则配置 */}
      {config.enable_talk_value_rules && (
        <div className="border-t pt-6">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h4 className="text-base font-semibold">动态发言频率规则</h4>
              <p className="text-xs text-muted-foreground mt-1">
                按时段或聊天流ID调整发言频率，优先匹配具体聊天，再匹配全局规则
              </p>
            </div>
            <Button onClick={addTalkValueRule} size="sm">
              <Plus className="h-4 w-4 mr-1" />
              添加规则
            </Button>
          </div>

          {config.talk_value_rules && config.talk_value_rules.length > 0 ? (
            <div className="space-y-4">
              {config.talk_value_rules.map((rule, index) => (
                <div key={index} className="rounded-lg border p-4 bg-muted/50 space-y-4">
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
                            <AlertDialogAction onClick={() => removeTalkValueRule(index)}>
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
                            updateTalkValueRule(index, 'target', '')
                          } else {
                            updateTalkValueRule(index, 'target', 'qq::group')
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
                                  updateTalkValueRule(index, 'target', `${value}:${chatId}:${chatType}`)
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
                                  updateTalkValueRule(index, 'target', `${platform}:${e.target.value}:${chatType}`)
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
                                  updateTalkValueRule(index, 'target', `${platform}:${chatId}:${value}`)
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
                        onChange={(v) => updateTalkValueRule(index, 'time', v)}
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
                              updateTalkValueRule(index, 'value', Math.max(0.01, Math.min(1, val)))
                            }
                          }}
                          className="w-20 h-8 text-xs"
                        />
                      </div>
                      <Slider
                        value={[rule.value]}
                        onValueChange={(values) =>
                          updateTalkValueRule(index, 'value', values[0])
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
              ))}
            </div>
          ) : (
            <div className="text-center py-8 text-muted-foreground">
              <p className="text-sm">暂无规则，点击"添加规则"按钮创建</p>
            </div>
          )}

          <div className="mt-4 p-4 bg-blue-50 dark:bg-blue-950/20 border border-blue-200 dark:border-blue-800 rounded-lg">
            <h5 className="text-sm font-semibold text-blue-900 dark:text-blue-100 mb-2">
              📝 规则说明
            </h5>
            <ul className="text-xs text-blue-800 dark:text-blue-200 space-y-1">
              <li>• <strong>Target 为空</strong>：全局规则，对所有聊天生效</li>
              <li>• <strong>Target 指定</strong>：仅对特定聊天流生效（格式：platform:id:type）</li>
              <li>• <strong>优先级</strong>：先匹配具体聊天流规则，再匹配全局规则</li>
              <li>• <strong>时间支持跨夜</strong>：例如 23:00-02:00 表示晚上11点到次日凌晨2点</li>
              <li>• <strong>数值范围</strong>：建议 0-1，0 表示完全沉默，1 表示正常发言</li>
            </ul>
          </div>
        </div>
      )}
    </div>
  )
})
