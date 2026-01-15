import React from 'react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import type { EmojiConfig, MemoryConfig, ToolConfig, VoiceConfig } from '../types'

interface FeaturesSectionProps {
  emojiConfig: EmojiConfig
  memoryConfig: MemoryConfig
  toolConfig: ToolConfig
  voiceConfig: VoiceConfig
  onEmojiChange: (config: EmojiConfig) => void
  onMemoryChange: (config: MemoryConfig) => void
  onToolChange: (config: ToolConfig) => void
  onVoiceChange: (config: VoiceConfig) => void
}

export const FeaturesSection = React.memo(function FeaturesSection({
  emojiConfig,
  memoryConfig,
  toolConfig,
  voiceConfig,
  onEmojiChange,
  onMemoryChange,
  onToolChange,
  onVoiceChange,
}: FeaturesSectionProps) {
  return (
    <div className="space-y-6">
      {/* 工具设置 */}
      <div className="rounded-lg border bg-card p-4 sm:p-6 space-y-4">
        <div>
          <h3 className="text-lg font-semibold mb-4">工具设置</h3>
          <div className="space-y-4">
            <div className="flex items-center space-x-2">
              <Switch
                id="enable_tool"
                checked={toolConfig.enable_tool}
                onCheckedChange={(checked) => onToolChange({ ...toolConfig, enable_tool: checked })}
              />
              <Label htmlFor="enable_tool" className="cursor-pointer">
                启用工具系统
              </Label>
            </div>
            <p className="text-xs text-muted-foreground -mt-2">
              允许麦麦使用各种工具来增强功能
            </p>

            <div className="flex items-center space-x-2 pt-2">
              <Switch
                id="enable_asr"
                checked={voiceConfig.enable_asr}
                onCheckedChange={(checked) => onVoiceChange({ ...voiceConfig, enable_asr: checked })}
              />
              <Label htmlFor="enable_asr" className="cursor-pointer">
                启用语音识别
              </Label>
            </div>
            <p className="text-xs text-muted-foreground -mt-2">
              启用后麦麦可以识别语音消息，需要配置语音识别模型
            </p>
          </div>
        </div>
      </div>

      {/* 记忆设置 */}
      <div className="rounded-lg border bg-card p-4 sm:p-6 space-y-4">
        <div>
          <h3 className="text-lg font-semibold mb-4">记忆设置</h3>
          <div className="grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="max_agent_iterations">记忆思考深度</Label>
              <Input
                id="max_agent_iterations"
                type="number"
                min="1"
                value={memoryConfig.max_agent_iterations}
                onChange={(e) =>
                  onMemoryChange({ ...memoryConfig, max_agent_iterations: parseInt(e.target.value) })
                }
              />
              <p className="text-xs text-muted-foreground">最低为 1（不深入思考）</p>
            </div>
            
            <div className="grid gap-2">
              <Label htmlFor="agent_timeout_seconds">最长回忆时间（秒）</Label>
              <Input
                id="agent_timeout_seconds"
                type="number"
                min="1"
                step="0.1"
                value={memoryConfig.agent_timeout_seconds ?? 120}
                onChange={(e) =>
                  onMemoryChange({ ...memoryConfig, agent_timeout_seconds: parseFloat(e.target.value) })
                }
              />
              <p className="text-xs text-muted-foreground">记忆检索的超时时间，避免过长的等待</p>
            </div>

            <div className="flex items-center space-x-2">
              <Switch
                id="enable_jargon_detection"
                checked={memoryConfig.enable_jargon_detection ?? true}
                onCheckedChange={(checked) =>
                  onMemoryChange({ ...memoryConfig, enable_jargon_detection: checked })
                }
              />
              <Label htmlFor="enable_jargon_detection" className="cursor-pointer">
                启用黑话识别
              </Label>
            </div>
            <p className="text-xs text-muted-foreground -mt-2">
              记忆检索过程中是否启用黑话识别
            </p>

            <div className="flex items-center space-x-2">
              <Switch
                id="global_memory"
                checked={memoryConfig.global_memory ?? false}
                onCheckedChange={(checked) =>
                  onMemoryChange({ ...memoryConfig, global_memory: checked })
                }
              />
              <Label htmlFor="global_memory" className="cursor-pointer">
                全局记忆查询
              </Label>
            </div>
            <p className="text-xs text-muted-foreground -mt-2">
              允许记忆检索在所有聊天记录中进行全局查询（忽略当前聊天流）
            </p>

            {/* 聊天历史总结配置 */}
            <div className="border-t pt-4 mt-4">
              <h4 className="text-sm font-semibold mb-3">聊天历史总结配置</h4>
              <div className="space-y-4">
                <div className="grid gap-2">
                  <Label htmlFor="chat_history_topic_check_message_threshold">话题检查消息数阈值</Label>
                  <Input
                    id="chat_history_topic_check_message_threshold"
                    type="number"
                    min="1"
                    value={memoryConfig.chat_history_topic_check_message_threshold ?? 80}
                    onChange={(e) =>
                      onMemoryChange({ ...memoryConfig, chat_history_topic_check_message_threshold: parseInt(e.target.value) })
                    }
                  />
                  <p className="text-xs text-muted-foreground">
                    当累积消息数达到此值时触发话题检查
                  </p>
                </div>

                <div className="grid gap-2">
                  <Label htmlFor="chat_history_topic_check_time_hours">话题检查时间阈值（小时）</Label>
                  <Input
                    id="chat_history_topic_check_time_hours"
                    type="number"
                    min="0.1"
                    step="0.1"
                    value={memoryConfig.chat_history_topic_check_time_hours ?? 8.0}
                    onChange={(e) =>
                      onMemoryChange({ ...memoryConfig, chat_history_topic_check_time_hours: parseFloat(e.target.value) })
                    }
                  />
                  <p className="text-xs text-muted-foreground">
                    当距离上次检查超过此时间且消息数达到最小阈值时触发话题检查
                  </p>
                </div>

                <div className="grid gap-2">
                  <Label htmlFor="chat_history_topic_check_min_messages">时间触发最小消息数</Label>
                  <Input
                    id="chat_history_topic_check_min_messages"
                    type="number"
                    min="1"
                    value={memoryConfig.chat_history_topic_check_min_messages ?? 20}
                    onChange={(e) =>
                      onMemoryChange({ ...memoryConfig, chat_history_topic_check_min_messages: parseInt(e.target.value) })
                    }
                  />
                  <p className="text-xs text-muted-foreground">
                    时间触发模式下的最小消息数阈值
                  </p>
                </div>

                <div className="grid gap-2">
                  <Label htmlFor="chat_history_finalize_no_update_checks">打包存储连续无更新次数</Label>
                  <Input
                    id="chat_history_finalize_no_update_checks"
                    type="number"
                    min="1"
                    value={memoryConfig.chat_history_finalize_no_update_checks ?? 3}
                    onChange={(e) =>
                      onMemoryChange({ ...memoryConfig, chat_history_finalize_no_update_checks: parseInt(e.target.value) })
                    }
                  />
                  <p className="text-xs text-muted-foreground">
                    当话题连续N次检查无新增内容时触发打包存储
                  </p>
                </div>

                <div className="grid gap-2">
                  <Label htmlFor="chat_history_finalize_message_count">打包存储消息条数阈值</Label>
                  <Input
                    id="chat_history_finalize_message_count"
                    type="number"
                    min="1"
                    value={memoryConfig.chat_history_finalize_message_count ?? 5}
                    onChange={(e) =>
                      onMemoryChange({ ...memoryConfig, chat_history_finalize_message_count: parseInt(e.target.value) })
                    }
                  />
                  <p className="text-xs text-muted-foreground">
                    当话题的消息条数超过此值时触发打包存储
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 表情包设置 */}
      <div className="rounded-lg border bg-card p-4 sm:p-6 space-y-4">
        <div>
          <h3 className="text-lg font-semibold mb-4">表情包设置</h3>
          <div className="grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="emoji_chance">表情包激活概率</Label>
              <Input
                id="emoji_chance"
                type="number"
                step="0.1"
                min="0"
                max="1"
                value={emojiConfig.emoji_chance}
                onChange={(e) =>
                  onEmojiChange({ ...emojiConfig, emoji_chance: parseFloat(e.target.value) })
                }
              />
              <p className="text-xs text-muted-foreground">范围 0-1，越大越容易发送表情包</p>
            </div>

            <div className="grid gap-2">
              <Label htmlFor="max_reg_num">最大注册数量</Label>
              <Input
                id="max_reg_num"
                type="number"
                min="1"
                value={emojiConfig.max_reg_num}
                onChange={(e) =>
                  onEmojiChange({ ...emojiConfig, max_reg_num: parseInt(e.target.value) })
                }
              />
              <p className="text-xs text-muted-foreground">麦麦最多可以注册的表情包数量</p>
            </div>

            <div className="grid gap-2">
              <Label htmlFor="check_interval">检查间隔（分钟）</Label>
              <Input
                id="check_interval"
                type="number"
                min="1"
                value={emojiConfig.check_interval}
                onChange={(e) =>
                  onEmojiChange({ ...emojiConfig, check_interval: parseInt(e.target.value) })
                }
              />
              <p className="text-xs text-muted-foreground">
                检查表情包（注册、破损、删除）的时间间隔
              </p>
            </div>

            <div className="flex items-center space-x-2">
              <Switch
                id="do_replace"
                checked={emojiConfig.do_replace}
                onCheckedChange={(checked) =>
                  onEmojiChange({ ...emojiConfig, do_replace: checked })
                }
              />
              <Label htmlFor="do_replace" className="cursor-pointer">
                达到最大数量时替换表情包
              </Label>
            </div>

            <div className="flex items-center space-x-2">
              <Switch
                id="steal_emoji"
                checked={emojiConfig.steal_emoji}
                onCheckedChange={(checked) =>
                  onEmojiChange({ ...emojiConfig, steal_emoji: checked })
                }
              />
              <Label htmlFor="steal_emoji" className="cursor-pointer">
                偷取表情包
              </Label>
            </div>
            <p className="text-xs text-muted-foreground -mt-2">
              允许麦麦将看到的表情包据为己有
            </p>

            <div className="flex items-center space-x-2">
              <Switch
                id="content_filtration"
                checked={emojiConfig.content_filtration}
                onCheckedChange={(checked) =>
                  onEmojiChange({ ...emojiConfig, content_filtration: checked })
                }
              />
              <Label htmlFor="content_filtration" className="cursor-pointer">
                启用表情包过滤
              </Label>
            </div>

            {emojiConfig.content_filtration && (
              <div className="grid gap-2 pl-6 border-l-2 border-primary/20">
                <Label htmlFor="filtration_prompt">过滤要求</Label>
                <Input
                  id="filtration_prompt"
                  value={emojiConfig.filtration_prompt}
                  onChange={(e) =>
                    onEmojiChange({ ...emojiConfig, filtration_prompt: e.target.value })
                  }
                  placeholder="符合公序良俗"
                />
                <p className="text-xs text-muted-foreground">
                  只有符合此要求的表情包才会被保存
                </p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
})
