import { Eye, EyeOff, Maximize2 } from 'lucide-react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { PasswordInput } from '@/components/ui/password-input'
import { StatusLight } from '@/components/ui/status-light'
import { Label } from '@/components/ui/label'
import { HelpTooltip } from '@/components/ui/help-tooltip'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Slider } from '@/components/ui/slider'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { InfoTooltip } from '@/components/ui/info-tooltip'
import { validateToken } from '@/lib/token-validator'
import { cn } from '@/lib/utils'
import type { ModelListItem } from '@/lib/config-api'
import {
  findTemplateByBaseUrl,
  PROVIDER_TEMPLATES,
  resolveThinkingFormatForModel,
} from '../config/providerTemplates'

import { PERSONALITY_PRESETS } from './personalityPresets'
import type {
  ApiProviderSetupConfig,
  BotBasicConfig,
  ModelSetupConfig,
  PersonalityConfig,
} from './types'

interface CustomTokenFormProps {
  token: string
  onChange: (token: string) => void
}

export function CustomTokenForm({ token, onChange }: CustomTokenFormProps) {
  const { t } = useTranslation()
  const tokenValidation = useMemo(() => validateToken(token), [token])

  return (
    <div className="space-y-6">
      <Label htmlFor="custom-token" className="sr-only">
        {t('setupPage.forms.customToken.label')}
      </Label>
      <PasswordInput
        id="custom-token"
        controlSize="lg"
        placeholder={t('setupPage.forms.customToken.placeholder')}
        value={token}
        onChange={(event) => onChange(event.target.value)}
        showLabel={t('setupPage.forms.customToken.show')}
        hideLabel={t('setupPage.forms.customToken.hide')}
        autoComplete="new-password"
        aria-describedby="custom-token-requirements"
        className="text-base sm:text-lg md:text-xl"
      />
      <ul
        id="custom-token-requirements"
        aria-label={t('setupPage.forms.customToken.requirements')}
        className="grid grid-cols-2 gap-2.5 sm:grid-cols-4"
      >
        {tokenValidation.rules.map((rule) => (
          <li
            key={rule.id}
            className={cn(
              'border-border flex min-h-11 items-center justify-center gap-2 rounded-[var(--surface-radius)] border-2 px-2 py-2 text-sm font-semibold',
              rule.passed ? 'text-foreground' : 'text-muted-foreground'
            )}
          >
            <StatusLight
              status={rule.passed ? 'on' : 'off'}
              glow={false}
              label={t(
                rule.passed
                  ? 'setupPage.forms.customToken.met'
                  : 'setupPage.forms.customToken.unmet'
              )}
            />
            <span>{t(`setupPage.forms.customToken.rules.${rule.id}`)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

interface BotBasicFormProps {
  config: BotBasicConfig
  onChange: (config: BotBasicConfig) => void
}

export function BotBasicForm({ config, onChange }: BotBasicFormProps) {
  const { t } = useTranslation()

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <Label htmlFor="nickname">{t('setupPage.forms.botBasic.nickname.label')}</Label>
        <Input
          id="nickname"
          placeholder={t('setupPage.forms.botBasic.nickname.placeholder')}
          value={config.nickname}
          onChange={(e) => onChange({ ...config, nickname: e.target.value })}
        />
      </div>
    </div>
  )
}

// ====== 步骤2：人格配置 ======
interface PersonalityFormProps {
  config: PersonalityConfig
  onChange: (config: PersonalityConfig) => void
}

export function PersonalityPresetSelect({ config, onChange }: PersonalityFormProps) {
  const { t } = useTranslation()
  const presets = [
    {
      id: 'default',
      name: t('setupPage.forms.personality.presets.default'),
      personality: t('setupPage.defaults.personality.personality'),
      behavior_style: t('setupPage.defaults.personality.behaviorStyle'),
      reply_style: t('setupPage.defaults.personality.replyStyle'),
    },
    ...PERSONALITY_PRESETS,
  ]

  const selectedPreset = presets.find((preset) =>
    config.personality === preset.personality
    && config.behavior_style === preset.behavior_style
    && config.reply_style === preset.reply_style
  )

  return (
    <div className="flex items-center justify-end gap-3">
      <Label htmlFor="personality-preset" className="shrink-0">
        {t('setupPage.forms.personality.presets.label')}
      </Label>
      <Select
        value={selectedPreset?.id ?? ''}
        onValueChange={(id) => {
          const preset = presets.find((item) => item.id === id)
          if (!preset) return
          onChange({
            ...config,
            personality: preset.personality,
            behavior_style: preset.behavior_style,
            reply_style: preset.reply_style,
          })
        }}
      >
        <SelectTrigger id="personality-preset" className="w-48">
          <SelectValue placeholder={t('setupPage.forms.personality.presets.default')} />
        </SelectTrigger>
        <SelectContent>
          {presets.map((preset) => (
            <SelectItem key={preset.id} value={preset.id}>{preset.name}</SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

export function PersonalityForm({ config, onChange }: PersonalityFormProps) {
  const { t } = useTranslation()
  const fields = [
    { key: 'personality', translation: 'personality' },
    { key: 'behavior_style', translation: 'behaviorStyle' },
    { key: 'reply_style', translation: 'replyStyle' },
  ] as const

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      {fields.map(({ key, translation }) => {
        const label = t(`setupPage.forms.personality.${translation}.label`)
        const placeholder = t(`setupPage.forms.personality.${translation}.placeholder`)
        const description = t(`setupPage.forms.personality.${translation}.description`)
        return (
          <div key={key} className={cn('space-y-1', key === 'reply_style' && 'md:col-span-2')}>
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Label htmlFor={key}>{label}</Label>
                <InfoTooltip content={description} label={description} />
              </div>
              <Dialog>
                <DialogTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-7"
                    aria-label={t('setupPage.forms.personality.expand', { field: label })}
                    title={t('setupPage.forms.personality.expand', { field: label })}
                  >
                    <Maximize2 className="size-4" />
                  </Button>
                </DialogTrigger>
                <DialogContent className="[--dialog-width:48rem]">
                  <DialogHeader>
                    <DialogTitle>{label}</DialogTitle>
                    <DialogDescription>{description}</DialogDescription>
                  </DialogHeader>
                  <Textarea
                    hideScrollbarWhenIdle
                    autoResize={false}
                    className="h-[min(50dvh,28rem)] min-h-32 resize-none overflow-y-auto"
                    aria-label={label}
                    placeholder={placeholder}
                    value={config[key]}
                    onChange={(e) => onChange({ ...config, [key]: e.target.value })}
                  />
                </DialogContent>
              </Dialog>
            </div>
            <Textarea
                    hideScrollbarWhenIdle
              id={key}
              autoResize={false}
              className="h-[104px] resize-none overflow-y-auto"
              placeholder={placeholder}
              value={config[key]}
              onChange={(e) => onChange({ ...config, [key]: e.target.value })}
              rows={3}
            />
          </div>
        )
      })}
    </div>
  )
}

// ====== 步骤3：API 提供商配置 ======
interface ApiProviderSetupFormProps {
  config: ApiProviderSetupConfig
  onChange: (config: ApiProviderSetupConfig) => void
}

export function ApiProviderSetupForm({ config, onChange }: ApiProviderSetupFormProps) {
  const { t } = useTranslation()
  const reduceMotion = useReducedMotion()
  const [showApiKey, setShowApiKey] = useState(false)
  const [customMode, setCustomMode] = useState(false)
  const [selectedPreset, setSelectedPreset] = useState('deepseek')
  const apiKeyToggleLabel = showApiKey
    ? t('setupPage.forms.apiProvider.apiKey.hide')
    : t('setupPage.forms.apiProvider.apiKey.show')
  const providerPresets = PROVIDER_TEMPLATES.filter(
    (template) => template.id !== 'custom' && template.id !== 'mai_replier'
  )

  useEffect(() => {
    if (!config.provider_name && !config.base_url) return
    const matched = providerPresets.find(
      (template) => template.name === config.provider_name && template.base_url === config.base_url
    )
    if (matched) {
      setSelectedPreset(matched.id)
      setCustomMode(false)
    } else {
      setCustomMode(true)
    }
  }, [config.base_url, config.provider_name])

  const selectPreset = (presetId: string) => {
    const preset = providerPresets.find((template) => template.id === presetId)
    if (!preset) return
    setSelectedPreset(preset.id)
    onChange({
      ...config,
      provider_name: preset.name,
      base_url: preset.base_url,
      api_key: '',
    })
  }

  const switchToCustom = () => {
    onChange({ provider_name: '', base_url: '', api_key: '' })
    setCustomMode(true)
  }

  const switchToPreset = () => {
    selectPreset(selectedPreset)
    setCustomMode(false)
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>{t('setupPage.forms.apiProvider.providerName.label')}</Label>
        {/* 表单纵向切换，按钮留在原位，仅线性调整宽度。 */}
        <div className="flex items-center gap-2">
          <div className="min-w-0 flex-1">
            <AnimatePresence initial={false} mode="wait">
              <motion.div
                key={customMode ? 'custom' : 'preset'}
                initial={{ opacity: 0, y: reduceMotion ? 0 : customMode ? 16 : -16 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: reduceMotion ? 0 : customMode ? 16 : -16 }}
                transition={{ duration: reduceMotion ? 0 : 0.16, ease: 'linear' }}
                className="flex gap-2"
              >
                {!customMode ? (
                  <Select value={selectedPreset} onValueChange={selectPreset}>
                    <SelectTrigger aria-label="选择预置提供商" className="h-11 min-w-0 flex-1">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent position="popper" collisionPadding={12}>
                      {providerPresets.map((preset) => (
                        <SelectItem key={preset.id} value={preset.id}>
                          {preset.display_name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <>
                    <Input
                      id="provider_name"
                      className="h-11 min-w-0 flex-1"
                      placeholder={t('setupPage.forms.apiProvider.providerName.placeholder')}
                      value={config.provider_name}
                      onChange={(e) => onChange({ ...config, provider_name: e.target.value })}
                    />
                    <Input
                      aria-label={t('setupPage.forms.apiProvider.baseUrl.label')}
                      className="h-11 min-w-0 flex-1 font-mono"
                      placeholder="https://api.example.com/v1"
                      value={config.base_url}
                      onChange={(e) => onChange({ ...config, base_url: e.target.value })}
                    />
                  </>
                )}
              </motion.div>
            </AnimatePresence>
          </div>
          <motion.div
            initial={false}
            animate={{ width: customMode ? 64 : 80 }}
            transition={{ duration: reduceMotion ? 0 : 0.2, ease: 'linear' }}
            className="shrink-0"
          >
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-11 w-full"
              onClick={customMode ? switchToPreset : switchToCustom}
            >
              {customMode ? '预置' : '自定义'}
            </Button>
          </motion.div>
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="api_key">{t('setupPage.forms.apiProvider.apiKey.label')}</Label>
        <div className="relative">
          <Input
            id="api_key"
            type={showApiKey ? 'text' : 'password'}
            placeholder="sk-..."
            value={config.api_key}
            onChange={(e) => onChange({ ...config, api_key: e.target.value })}
            className="pr-10 font-mono"
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="absolute top-1/2 right-2 h-7 w-7 -translate-y-1/2 rounded-md border-0 p-0 hover:bg-transparent"
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
      </div>
    </div>
  )
}

// ====== 步骤4：基础模型配置 ======
interface ModelSetupFormProps {
  config: ModelSetupConfig
  onChange: (config: ModelSetupConfig) => void
  models?: ModelListItem[]
  fetchStatus?: 'idle' | 'loading' | 'success' | 'error'
  fetchError?: string
  providerBaseUrl?: string
}

export function ModelSetupForm({
  config,
  onChange,
  models = [],
  fetchStatus = 'idle',
  fetchError = '',
  providerBaseUrl = '',
}: ModelSetupFormProps) {
  const { t } = useTranslation()

  const reduceMotion = useReducedMotion()
  const providerTemplate = findTemplateByBaseUrl(providerBaseUrl)
  const supportsThinkingSwitch = (identifier: string): boolean => {
    if (!identifier.trim() || (fetchStatus !== 'success' && fetchStatus !== 'error')) return false
    if (providerTemplate?.id === 'deepseek') return true
    const format = resolveThinkingFormatForModel(providerTemplate, identifier)
    return format?.kind === 'enable_thinking' ||
      (format?.kind === 'thinking_type' && format.canDisable === true)
  }

  const inferThinkingEnabled = (modelIdentifier: string) => {
    const normalizedIdentifier = modelIdentifier.trim().toLowerCase()
    return normalizedIdentifier.includes('deepseek-v4-pro')
  }

  const renderThinkingEffort = (role: 'planner' | 'replyer') => {
    const efforts = ['low', 'high', 'max'] as const
    const labels = ['低', '高', '最高']
    const selected = efforts.indexOf(config[`${role}_reasoning_effort`] ?? 'high')
    return (
      <AnimatePresence initial={false}>
        {providerTemplate?.id === 'deepseek' && config[`${role}_thinking`] && (
          <motion.div
            key={`${role}-effort`}
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: reduceMotion ? 0 : 0.2, ease: 'easeInOut' }}
            className="overflow-hidden"
          >
            <div className="space-y-3 px-2 pt-1 pb-2">
              <Label htmlFor={`${role}_reasoning_effort`} className="flex justify-between text-sm">
                <span>思考强度</span><span>{labels[selected]}</span>
              </Label>
              <Slider
                id={`${role}_reasoning_effort`}
                aria-label="思考强度"
                min={0}
                max={2}
                step={1}
                value={[selected]}
                onValueChange={([value]) => onChange({
                  ...config,
                  [`${role}_reasoning_effort`]: efforts[value],
                })}
              />
              <div className="text-muted-foreground flex justify-between text-xs" aria-hidden="true">
                {labels.map((label) => <span key={label}>{label}</span>)}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    )
  }

  const renderModelInput = (role: 'planner' | 'replyer') => {
    const identifier = config[`${role}_model_identifier`]
    const changeModel = (value: string) =>
      onChange({
        ...config,
        [`${role}_model_identifier`]: value,
        [`${role}_model_name`]: value,
        [`${role}_thinking`]: providerTemplate?.id === 'deepseek'
          ? role === 'replyer' : inferThinkingEnabled(value),
        [`${role}_reasoning_effort`]: 'high',
      })
    if (fetchStatus === 'success') {
      return (
        <Select value={identifier} onValueChange={changeModel}>
          <SelectTrigger id={`${role}_model_identifier`} className="min-w-0 font-mono">
            <SelectValue placeholder="请选择模型">{identifier || undefined}</SelectValue>
          </SelectTrigger>
          <SelectContent position="popper" collisionPadding={12}>
            {models.map((model) => (
              <SelectItem key={model.id} value={model.id}>{model.id}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      )
    }
    return (
      <Input
        id={`${role}_model_identifier`}
        placeholder={fetchStatus === 'error' ? '手动输入模型标识符' : '填写 API Key 后自动获取'}
        value={identifier}
        disabled={fetchStatus !== 'error'}
        onChange={(event) => changeModel(event.target.value)}
        className="font-mono"
      />
    )
  }

  return (
    <div className="space-y-4">
      {fetchStatus === 'loading' && (
        <p role="status" className="text-muted-foreground text-sm">正在获取模型列表…</p>
      )}
      {fetchStatus === 'error' && (
        <p role="alert" className="text-destructive text-sm">
          {fetchError}，请手动填写模型标识符。
        </p>
      )}
      <div className="grid min-w-0 md:grid-cols-2">
        <div className="min-w-0 space-y-3 pb-4 md:pr-4 md:pb-0">
          <div className="space-y-2">
            <Label htmlFor="planner_model_identifier" className="flex items-center gap-1">
              {t('setupPage.forms.modelSetup.planner.identifier.label')}
              <HelpTooltip
                content={t('setupPage.forms.modelSetup.planner.identifier.description')}
                side="right"
              />
            </Label>
            {renderModelInput('planner')}
          </div>

          {supportsThinkingSwitch(config.planner_model_identifier) && (
            <div>
              <div className="flex items-center justify-between gap-4 rounded-md bg-muted/40 p-2">
                <Label htmlFor="planner_thinking" className="text-sm font-medium">
                  启用思考
                </Label>
                <Switch
                  id="planner_thinking"
                  checked={config.planner_thinking}
                  onCheckedChange={(checked) =>
                    onChange({ ...config, planner_thinking: checked })
                  }
                />
              </div>
              {renderThinkingEffort('planner')}
            </div>
          )}
        </div>

        <div className="border-border min-w-0 space-y-3 border-t-2 pt-4 md:border-t-0 md:border-l-2 md:pt-0 md:pl-4">
          <div className="space-y-2">
            <Label htmlFor="replyer_model_identifier" className="flex items-center gap-1">
              {t('setupPage.forms.modelSetup.replyer.identifier.label')}
              <HelpTooltip
                content={t('setupPage.forms.modelSetup.replyer.identifier.description')}
                side="right"
              />
            </Label>
            {renderModelInput('replyer')}
          </div>

          {supportsThinkingSwitch(config.replyer_model_identifier) && (
            <div>
              <div className="flex items-center justify-between gap-4 rounded-md bg-muted/40 p-2">
                <Label htmlFor="replyer_thinking" className="text-sm font-medium">
                  启用思考
                </Label>
                <Switch
                  id="replyer_thinking"
                  checked={config.replyer_thinking}
                  onCheckedChange={(checked) =>
                    onChange({ ...config, replyer_thinking: checked })
                  }
                />
              </div>
              {renderThinkingEffort('replyer')}
            </div>
          )}
        </div>
      </div>

    </div>
  )
}
