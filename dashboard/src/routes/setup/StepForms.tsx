// 设置向导各步骤表单组件

import { ExternalLink, Eye, EyeOff, X } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'

import type {
  BotBasicConfig,
  EmojiConfig,
  OtherBasicConfig,
  PersonalityConfig,
  SiliconFlowConfig,
} from './types'

// ====== 步骤1：Bot基础配置 ======

const KNOWN_PLATFORMS: Record<string, string> = {
  qq: 'qq',
  telegram: 'telegram',
  tg: 'telegram',
  discord: 'discord',
  kook: 'kook',
}

const PLATFORM_OPTIONS = ['qq', 'telegram', 'discord', 'kook', 'custom'] as const

function normalizePlatform(raw: string): string {
  const key = raw.trim().toLowerCase()
  return KNOWN_PLATFORMS[key] || key
}

function deriveSelectedPlatform(config: BotBasicConfig): { selected: string; customName: string } {
  const platform = config.platform
  // Legacy: no platform set but has QQ account
  if (!platform && config.qq_account > 0) {
    return { selected: 'qq', customName: '' }
  }
  if (!platform) {
    return { selected: '', customName: '' }
  }
  const known = PLATFORM_OPTIONS.find((value) => value === platform && value !== 'custom')
  if (known) {
    return { selected: platform, customName: '' }
  }
  return { selected: 'custom', customName: platform }
}

function upsertPlatformAccount(
  platforms: string[],
  platformName: string,
  accountId: string
): string[] {
  const normalized = normalizePlatform(platformName)
  const filtered = platforms.filter((platform) => {
    const prefix = platform.split(':')[0]
    return normalizePlatform(prefix) !== normalized
  })
  if (accountId.trim()) {
    filtered.push(`${normalized}:${accountId.trim()}`)
  }
  return filtered
}

function getPrimaryAccount(platforms: string[], platformName: string): string {
  const normalized = normalizePlatform(platformName)
  const entry = platforms.find((platform) => {
    const prefix = platform.split(':')[0]
    return normalizePlatform(prefix) === normalized
  })
  return entry ? entry.split(':').slice(1).join(':') : ''
}

interface BotBasicFormProps {
  config: BotBasicConfig
  onChange: (config: BotBasicConfig) => void
}

export function BotBasicForm({ config, onChange }: BotBasicFormProps) {
  const { t } = useTranslation()
  const derived = deriveSelectedPlatform(config)
  const [selectedPlatformOverride, setSelectedPlatformOverride] = useState<string | null>(null)
  const [customPlatformNameOverride, setCustomPlatformNameOverride] = useState<string | null>(null)
  const selectedPlatform = selectedPlatformOverride ?? derived.selected
  const customPlatformName = customPlatformNameOverride ?? derived.customName
  const primaryAccount =
    selectedPlatform === 'qq'
      ? config.qq_account > 0
        ? String(config.qq_account)
        : ''
      : config.platform
        ? getPrimaryAccount(config.platforms, config.platform)
        : ''

  const platformOptions = [
    { value: 'qq', label: 'QQ' },
    { value: 'telegram', label: 'Telegram' },
    { value: 'discord', label: 'Discord' },
    { value: 'kook', label: 'Kook' },
    { value: 'custom', label: t('setupPage.forms.botBasic.platform.options.custom') },
  ]

  const handlePlatformChange = (value: string) => {
    setSelectedPlatformOverride(value)
    const realPlatform = value === 'custom' ? customPlatformName : value
    onChange({
      ...config,
      platform: normalizePlatform(realPlatform),
      qq_account: value === 'qq' ? config.qq_account : config.qq_account,
    })
  }

  const handleCustomNameChange = (name: string) => {
    setCustomPlatformNameOverride(name)
    const normalized = normalizePlatform(name)
    const nextPlatforms = primaryAccount
      ? upsertPlatformAccount(config.platforms, normalized, primaryAccount)
      : config.platforms
    onChange({
      ...config,
      platform: normalized,
      platforms: nextPlatforms,
    })
  }

  const handleAccountChange = (accountId: string) => {
    const realPlatform = selectedPlatform === 'custom' ? customPlatformName : selectedPlatform
    const normalized = normalizePlatform(realPlatform)

    if (normalized === 'qq') {
      onChange({
        ...config,
        qq_account: Number(accountId) || 0,
        platform: 'qq',
      })
    } else {
      onChange({
        ...config,
        platform: normalized,
        platforms: upsertPlatformAccount(config.platforms, normalized, accountId),
      })
    }
  }

  const handleAddAlias = (alias: string) => {
    if (alias.trim() && !config.alias_names.includes(alias.trim())) {
      onChange({
        ...config,
        alias_names: [...config.alias_names, alias.trim()],
      })
    }
  }

  const handleRemoveAlias = (index: number) => {
    onChange({
      ...config,
      alias_names: config.alias_names.filter((_, aliasIndex) => aliasIndex !== index),
    })
  }

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <Label htmlFor="platform">{t('setupPage.forms.botBasic.platform.label')}</Label>
        <Select value={selectedPlatform} onValueChange={handlePlatformChange}>
          <SelectTrigger id="platform">
            <SelectValue placeholder={t('setupPage.forms.botBasic.platform.placeholder')} />
          </SelectTrigger>
          <SelectContent>
            {platformOptions.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-muted-foreground text-xs">
          {t('setupPage.forms.botBasic.platform.description')}
        </p>
      </div>

      {selectedPlatform === 'custom' && (
        <div className="space-y-3">
          <Label htmlFor="custom_platform_name">
            {t('setupPage.forms.botBasic.customPlatform.label')}
          </Label>
          <Input
            id="custom_platform_name"
            placeholder={t('setupPage.forms.botBasic.customPlatform.placeholder')}
            value={customPlatformName}
            onChange={(e) => handleCustomNameChange(e.target.value)}
          />
        </div>
      )}

      {selectedPlatform === 'qq' && (
        <div className="space-y-3">
          <Label htmlFor="qq_account">{t('setupPage.forms.botBasic.qqAccount.label')}</Label>
          <Input
            id="qq_account"
            type="number"
            placeholder={t('setupPage.forms.botBasic.qqAccount.placeholder')}
            value={primaryAccount}
            onChange={(e) => handleAccountChange(e.target.value)}
          />
          <p className="text-muted-foreground text-xs">
            {t('setupPage.forms.botBasic.qqAccount.description')}
          </p>
        </div>
      )}

      {selectedPlatform &&
        selectedPlatform !== 'qq' &&
        (selectedPlatform !== 'custom' || customPlatformName) && (
          <div className="space-y-3">
            <Label htmlFor="primary_account">
              {t('setupPage.forms.botBasic.primaryAccount.label')}
            </Label>
            <Input
              id="primary_account"
              placeholder={t('setupPage.forms.botBasic.primaryAccount.placeholder')}
              value={primaryAccount}
              onChange={(e) => handleAccountChange(e.target.value)}
            />
            <p className="text-muted-foreground text-xs">
              {t('setupPage.forms.botBasic.primaryAccount.description')}
            </p>
          </div>
        )}

      <div className="space-y-3">
        <Label htmlFor="nickname">{t('setupPage.forms.botBasic.nickname.label')}</Label>
        <Input
          id="nickname"
          placeholder={t('setupPage.forms.botBasic.nickname.placeholder')}
          value={config.nickname}
          onChange={(e) => onChange({ ...config, nickname: e.target.value })}
        />
        <p className="text-muted-foreground text-xs">
          {t('setupPage.forms.botBasic.nickname.description')}
        </p>
      </div>

      <div className="space-y-3">
        <Label>{t('setupPage.forms.botBasic.alias.label')}</Label>
        <div className="mb-2 flex flex-wrap gap-2">
          {config.alias_names.map((alias, index) => (
            <Badge key={index} variant="secondary" className="gap-1">
              {alias}
              <button
                type="button"
                onClick={() => handleRemoveAlias(index)}
                className="hover:text-destructive ml-1"
                aria-label={t('setupPage.forms.botBasic.alias.remove', { alias })}
              >
                <X className="h-3 w-3" />
              </button>
            </Badge>
          ))}
        </div>
        <div className="flex gap-2">
          <Input
            id="alias_input"
            placeholder={t('setupPage.forms.botBasic.alias.placeholder')}
            onKeyPress={(e) => {
              if (e.key === 'Enter') {
                handleAddAlias((e.target as HTMLInputElement).value)
                ;(e.target as HTMLInputElement).value = ''
              }
            }}
          />
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              const input = document.getElementById('alias_input') as HTMLInputElement
              if (input) {
                handleAddAlias(input.value)
                input.value = ''
              }
            }}
          >
            {t('setupPage.forms.botBasic.alias.add')}
          </Button>
        </div>
        <p className="text-muted-foreground text-xs">
          {t('setupPage.forms.botBasic.alias.description')}
        </p>
      </div>
    </div>
  )
}

// ====== 步骤2：人格配置 ======
interface PersonalityFormProps {
  config: PersonalityConfig
  onChange: (config: PersonalityConfig) => void
}

export function PersonalityForm({ config, onChange }: PersonalityFormProps) {
  const { t } = useTranslation()

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <Label htmlFor="personality">{t('setupPage.forms.personality.personality.label')}</Label>
        <Textarea
          id="personality"
          placeholder={t('setupPage.forms.personality.personality.placeholder')}
          value={config.personality}
          onChange={(e) => onChange({ ...config, personality: e.target.value })}
          rows={3}
        />
        <p className="text-muted-foreground text-xs">
          {t('setupPage.forms.personality.personality.description')}
        </p>
      </div>

      <div className="space-y-3">
        <Label htmlFor="reply_style">{t('setupPage.forms.personality.replyStyle.label')}</Label>
        <Textarea
          id="reply_style"
          placeholder={t('setupPage.forms.personality.replyStyle.placeholder')}
          value={config.reply_style}
          onChange={(e) => onChange({ ...config, reply_style: e.target.value })}
          rows={3}
        />
        <p className="text-muted-foreground text-xs">
          {t('setupPage.forms.personality.replyStyle.description')}
        </p>
      </div>

      <div className="space-y-3">
        <Label htmlFor="interest">{t('setupPage.forms.personality.interest.label')}</Label>
        <Textarea
          id="interest"
          placeholder={t('setupPage.forms.personality.interest.placeholder')}
          value={config.interest}
          onChange={(e) => onChange({ ...config, interest: e.target.value })}
          rows={2}
        />
        <p className="text-muted-foreground text-xs">
          {t('setupPage.forms.personality.interest.description')}
        </p>
      </div>

      <Separator />

      <div className="space-y-3">
        <Label htmlFor="plan_style">{t('setupPage.forms.personality.planStyle.label')}</Label>
        <Textarea
          id="plan_style"
          placeholder={t('setupPage.forms.personality.planStyle.placeholder')}
          value={config.plan_style}
          onChange={(e) => onChange({ ...config, plan_style: e.target.value })}
          rows={4}
        />
        <p className="text-muted-foreground text-xs">
          {t('setupPage.forms.personality.planStyle.description')}
        </p>
      </div>

      <div className="space-y-3">
        <Label htmlFor="private_plan_style">
          {t('setupPage.forms.personality.privatePlanStyle.label')}
        </Label>
        <Textarea
          id="private_plan_style"
          placeholder={t('setupPage.forms.personality.privatePlanStyle.placeholder')}
          value={config.private_plan_style}
          onChange={(e) => onChange({ ...config, private_plan_style: e.target.value })}
          rows={3}
        />
        <p className="text-muted-foreground text-xs">
          {t('setupPage.forms.personality.privatePlanStyle.description')}
        </p>
      </div>
    </div>
  )
}

// ====== 步骤3：表情包配置 ======
interface EmojiFormProps {
  config: EmojiConfig
  onChange: (config: EmojiConfig) => void
}

export function EmojiForm({ config, onChange }: EmojiFormProps) {
  const { t } = useTranslation()

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <Label htmlFor="emoji_chance">{t('setupPage.forms.emoji.emojiChance.label')}</Label>
          <span className="text-muted-foreground text-sm">
            {(config.emoji_chance * 100).toFixed(0)}%
          </span>
        </div>
        <Input
          id="emoji_chance"
          type="range"
          min="0"
          max="1"
          step="0.1"
          value={config.emoji_chance}
          onChange={(e) => onChange({ ...config, emoji_chance: Number(e.target.value) })}
        />
        <p className="text-muted-foreground text-xs">
          {t('setupPage.forms.emoji.emojiChance.description')}
        </p>
      </div>

      <div className="space-y-3">
        <Label htmlFor="max_reg_num">{t('setupPage.forms.emoji.maxRegNum.label')}</Label>
        <Input
          id="max_reg_num"
          type="number"
          min="1"
          max="200"
          value={config.max_reg_num}
          onChange={(e) => onChange({ ...config, max_reg_num: Number(e.target.value) })}
        />
        <p className="text-muted-foreground text-xs">
          {t('setupPage.forms.emoji.maxRegNum.description')}
        </p>
      </div>

      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <Label htmlFor="do_replace">{t('setupPage.forms.emoji.doReplace.label')}</Label>
          <p className="text-muted-foreground text-xs">
            {t('setupPage.forms.emoji.doReplace.description')}
          </p>
        </div>
        <Switch
          id="do_replace"
          checked={config.do_replace}
          onCheckedChange={(checked) => onChange({ ...config, do_replace: checked })}
        />
      </div>

      <div className="space-y-3">
        <Label htmlFor="check_interval">{t('setupPage.forms.emoji.checkInterval.label')}</Label>
        <Input
          id="check_interval"
          type="number"
          min="1"
          max="120"
          value={config.check_interval}
          onChange={(e) => onChange({ ...config, check_interval: Number(e.target.value) })}
        />
        <p className="text-muted-foreground text-xs">
          {t('setupPage.forms.emoji.checkInterval.description')}
        </p>
      </div>

      <Separator />

      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <Label htmlFor="steal_emoji">{t('setupPage.forms.emoji.stealEmoji.label')}</Label>
          <p className="text-muted-foreground text-xs">
            {t('setupPage.forms.emoji.stealEmoji.description')}
          </p>
        </div>
        <Switch
          id="steal_emoji"
          checked={config.steal_emoji}
          onCheckedChange={(checked) => onChange({ ...config, steal_emoji: checked })}
        />
      </div>

      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <Label htmlFor="content_filtration">
            {t('setupPage.forms.emoji.contentFiltration.label')}
          </Label>
          <p className="text-muted-foreground text-xs">
            {t('setupPage.forms.emoji.contentFiltration.description')}
          </p>
        </div>
        <Switch
          id="content_filtration"
          checked={config.content_filtration}
          onCheckedChange={(checked) => onChange({ ...config, content_filtration: checked })}
        />
      </div>

      {config.content_filtration && (
        <div className="space-y-3">
          <Label htmlFor="filtration_prompt">
            {t('setupPage.forms.emoji.filtrationPrompt.label')}
          </Label>
          <Input
            id="filtration_prompt"
            placeholder={t('setupPage.forms.emoji.filtrationPrompt.placeholder')}
            value={config.filtration_prompt}
            onChange={(e) => onChange({ ...config, filtration_prompt: e.target.value })}
          />
          <p className="text-muted-foreground text-xs">
            {t('setupPage.forms.emoji.filtrationPrompt.description')}
          </p>
        </div>
      )}
    </div>
  )
}

// ====== 步骤4：其他基础配置 ======
interface OtherBasicFormProps {
  config: OtherBasicConfig
  onChange: (config: OtherBasicConfig) => void
}

export function OtherBasicForm({ config, onChange }: OtherBasicFormProps) {
  const { t } = useTranslation()

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <Label htmlFor="enable_tool">{t('setupPage.forms.other.enableTool.label')}</Label>
          <p className="text-muted-foreground text-xs">
            {t('setupPage.forms.other.enableTool.description')}
          </p>
        </div>
        <Switch
          id="enable_tool"
          checked={config.enable_tool}
          onCheckedChange={(checked) => onChange({ ...config, enable_tool: checked })}
        />
      </div>

      <Separator />

      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <Label htmlFor="all_global">{t('setupPage.forms.other.allGlobal.label')}</Label>
          <p className="text-muted-foreground text-xs">
            {t('setupPage.forms.other.allGlobal.description')}
          </p>
        </div>
        <Switch
          id="all_global"
          checked={config.all_global}
          onCheckedChange={(checked) => onChange({ ...config, all_global: checked })}
        />
      </div>
    </div>
  )
}

// ====== 步骤5：硅基流动API配置 ======
interface SiliconFlowFormProps {
  config: SiliconFlowConfig
  onChange: (config: SiliconFlowConfig) => void
}

export function SiliconFlowForm({ config, onChange }: SiliconFlowFormProps) {
  const { t } = useTranslation()
  const [showApiKey, setShowApiKey] = useState(false)
  const apiKeyToggleLabel = showApiKey
    ? t('setupPage.forms.siliconFlow.apiKey.hide')
    : t('setupPage.forms.siliconFlow.apiKey.show')
  const autoConfigItems = [
    t('setupPage.forms.siliconFlow.autoConfig.items.deepseek'),
    t('setupPage.forms.siliconFlow.autoConfig.items.qwen3'),
    t('setupPage.forms.siliconFlow.autoConfig.items.qwen3Vl'),
    t('setupPage.forms.siliconFlow.autoConfig.items.senseVoice'),
    t('setupPage.forms.siliconFlow.autoConfig.items.bgeM3'),
    t('setupPage.forms.siliconFlow.autoConfig.items.lpmm'),
  ]

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 dark:border-blue-800 dark:bg-blue-950/30">
        <div className="flex items-start gap-3">
          <div className="mt-0.5">
            <svg
              className="h-5 w-5 text-blue-600 dark:text-blue-400"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
          </div>
          <div className="flex-1 text-sm">
            <p className="mb-1 font-medium text-blue-900 dark:text-blue-100">
              {t('setupPage.forms.siliconFlow.about.title')}
            </p>
            <p className="mb-2 text-blue-700 dark:text-blue-300">
              {t('setupPage.forms.siliconFlow.about.description')}
            </p>
            <a
              href="https://cloud.siliconflow.cn"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 font-medium text-blue-600 hover:underline dark:text-blue-400"
            >
              {t('setupPage.forms.siliconFlow.about.link')}
              <ExternalLink className="h-3 w-3" />
            </a>
          </div>
        </div>
      </div>

      <div className="space-y-3">
        <Label htmlFor="siliconflow_api_key">{t('setupPage.forms.siliconFlow.apiKey.label')}</Label>
        <div className="relative">
          <Input
            id="siliconflow_api_key"
            type={showApiKey ? 'text' : 'password'}
            placeholder="sk-..."
            value={config.api_key}
            onChange={(e) => onChange({ api_key: e.target.value })}
            className="pr-10 font-mono"
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="absolute top-0 right-0 h-full px-3 hover:bg-transparent"
            onClick={() => setShowApiKey(!showApiKey)}
            aria-label={apiKeyToggleLabel}
            title={apiKeyToggleLabel}
          >
            {showApiKey ? (
              <EyeOff className="text-muted-foreground h-4 w-4" />
            ) : (
              <Eye className="text-muted-foreground h-4 w-4" />
            )}
          </Button>
        </div>
        <p className="text-muted-foreground text-xs">
          {t('setupPage.forms.siliconFlow.apiKey.description')}
        </p>
      </div>

      <div className="bg-muted/50 space-y-2 rounded-lg p-4 text-sm">
        <p className="font-medium">{t('setupPage.forms.siliconFlow.autoConfig.title')}</p>
        <ul className="text-muted-foreground ml-2 list-inside list-disc space-y-1">
          {autoConfigItems.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </div>

      <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950/30">
        <p className="text-sm text-amber-900 dark:text-amber-100">
          <span className="font-medium">{t('setupPage.forms.siliconFlow.hint.title')}</span>
          {t('setupPage.forms.siliconFlow.hint.description')}
        </p>
      </div>
    </div>
  )
}
