import { useState } from 'react'
import { FormRow, FormSection } from '@/components/ui/form-section'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ProviderForm } from '../../modelProvider/ProviderForm'
import type { APIProvider } from '../../modelProvider/types'
import { findTemplateByBaseUrl, PROVIDER_TEMPLATES } from '../../providerTemplates'
import { usePluginProviderFormats } from '../../usePluginProviderFormats'
import type { FormErrors, ModelInfo } from '../types'
import { SpeechPreviewButton } from './SpeechPreviewButton'

interface SpeechTemplate {
  id: string
  label: string
  providerId: string
  clientType?: string
  voices?: { id: string; label: string }[]
}

const groupKey = (item: SpeechTemplate) => item.clientType ? `plugin:${item.clientType}` : `builtin:${item.providerId}`
const isOpenAI = (type: string) => type === 'openai' || type === 'openai_responses'

const asrTemplates: SpeechTemplate[] = [
  { id: 'deepseek-asr', label: 'DeepSeek ASR', providerId: 'deepseek' },
  { id: 'gpt-transcribe', label: 'OpenAI GPT Transcribe', providerId: 'openai' },
  { id: 'FunAudioLLM/SenseVoiceSmall', label: '硅基流动 SenseVoiceSmall', providerId: 'siliconflow' },
]

const stepVoices = [
  { id: 'zixinnansheng', label: '自信男声' },
  { id: 'elegantgentle-female', label: '气质温婉' },
  { id: 'livelybreezy-female', label: '活力轻快' },
]
const stepLegacyVoices = [
  { id: 'cixingnansheng', label: '磁性男声' },
  { id: 'wenrounvsheng', label: '温柔女声' },
  { id: 'jilingshaonv', label: '机灵少女' },
  { id: 'boyinnansheng', label: '播音男声' },
]
const ttsTemplates: SpeechTemplate[] = [
  { id: 'deepseek-tts', label: 'DeepSeek TTS', providerId: 'deepseek', voices: [
    { id: 'stella', label: '海星（女声）' }, { id: 'tide', label: '暗潮（男声）' },
  ] },
  { id: 'stepaudio-3-tts', label: 'StepAudio 3 TTS', providerId: 'stepfun', voices: stepVoices },
  { id: 'stepaudio-2.5-tts', label: 'StepAudio 2.5 TTS', providerId: 'stepfun', voices: [...stepVoices, ...stepLegacyVoices] },
  { id: 'step-tts-2', label: 'Step TTS 2', providerId: 'stepfun', voices: [...stepVoices, ...stepLegacyVoices] },
  { id: 'step-tts-mini', label: 'Step TTS Mini', providerId: 'stepfun', voices: stepLegacyVoices },
]

function providerTemplateId(provider: APIProvider) {
  const id = findTemplateByBaseUrl(provider.base_url)?.id
    ?? findTemplateByBaseUrl(provider.base_url.replace(/\/v1\/?$/, ''))?.id
  return id === 'stepfun_plan' ? 'stepfun' : id
}

export function SpeechTemplateForm({ model, providers, draftProvider, errors, onChange, onCreateProvider }: {
  model: ModelInfo
  providers: APIProvider[]
  draftProvider: APIProvider | null
  errors: FormErrors
  onChange: (model: ModelInfo, draftProvider: APIProvider | null) => void
  onCreateProvider: (provider: APIProvider) => Promise<APIProvider | undefined>
}) {
  const synthesis = model.model_type === 'tts'
  const { plugins, error: pluginError } = usePluginProviderFormats()
  const [chosenGroup, setChosenGroup] = useState('')
  const [customConnection, setCustomConnection] = useState<APIProvider | null>(null)
  const pluginTemplates: SpeechTemplate[] = plugins.flatMap((plugin) =>
    Object.entries(plugin.formats[synthesis ? 'tts' : 'asr']).map(([id, format]) => ({
      id, label: format.label, providerId: plugin.client_type, clientType: plugin.client_type,
      voices: Object.entries(format.voices).map(([id, label]) => ({ id, label })),
    }))
  )
  const templates = [...(synthesis ? ttsTemplates : asrTemplates), ...pluginTemplates]
  const groups = new Map(templates.map((item) => [
    groupKey(item), item.clientType ?? PROVIDER_TEMPLATES.find((p) => p.id === item.providerId)!.display_name,
  ]))
  const savedConnection = providers.find((p) => p.name === model.api_provider)
  const currentType = (draftProvider ?? savedConnection)?.client_type
  const savedTemplate = templates.find((item) => item.id === model.model_identifier &&
    (item.clientType ? item.clientType === currentType : !currentType || isOpenAI(currentType)))
  const group = chosenGroup || (savedTemplate ? groupKey(savedTemplate) : model.model_identifier ? 'saved' : '')
  const candidates = templates.filter((item) => groupKey(item) === group)
  const connectionType = candidates[0]
  const connectionReady = draftProvider ? Boolean(draftProvider.api_key.trim()) : Boolean(savedConnection)
  const preset = candidates.find((item) => item.id === model.model_identifier)
  const voices = preset?.voices ?? []
  const voice = String(model.extra_params?.voice_id ?? voices[0]?.id ?? '')
  const choices = providers.filter((p) => {
    if (p.name === model.api_provider) return true
    if (!connectionType) return !currentType || isOpenAI(currentType) ? isOpenAI(p.client_type) : p.client_type === currentType
    if (connectionType.clientType) return p.client_type === connectionType.clientType
    const id = providerTemplateId(p)
    return isOpenAI(p.client_type) && (!id || id === connectionType.providerId)
  })

  function newProvider(item: SpeechTemplate): APIProvider {
    const defaults = item.clientType
      ? { name: item.clientType, base_url: '', client_type: item.clientType }
      : PROVIDER_TEMPLATES.find((p) => p.id === item.providerId)!
    let name = defaults.name
    let suffix = 2
    while (providers.some((p) => p.name.toLowerCase() === name.toLowerCase())) {
      name = `${defaults.name} (${suffix++})`
    }
    return { name, base_url: defaults.base_url, client_type: defaults.client_type, api_key: '' }
  }

  function selectGroup(next: string) {
    const source = templates.find((item) => groupKey(item) === next)
    if (!source || next === group) return
    const matches = providers.filter((provider) => source.clientType
      ? provider.client_type === source.clientType
      : isOpenAI(provider.client_type) && providerTemplateId(provider) === source.providerId)
    const existing = matches.find((p) => p.name === model.api_provider) ?? matches[0]
    const draft = !existing && !source.clientType ? newProvider(source) : null
    setChosenGroup(next)
    onChange({ ...model, model_identifier: '', api_provider: existing?.name ?? draft?.name ?? '', extra_params: {},
      name: model.name === preset?.label ? '' : model.name }, draft)
  }

  function selectModel(id: string) {
    const next = candidates.find((item) => item.id === id)
    if (!next) return
    onChange({
      ...model, model_identifier: id,
      ...(synthesis && { extra_params: { ...model.extra_params,
        voice_id: next.voices?.some((item) => item.id === voice) ? voice : next.voices?.[0]?.id ?? '',
      } }),
      name: !model.name || model.name === preset?.label ? next.label : model.name,
    }, draftProvider)
  }

  function selectConnection(value: string) {
    if (value === 'custom' && connectionType) {
      setCustomConnection(newProvider(connectionType))
      return
    }
    const draft = value === 'new' && connectionType ? newProvider(connectionType) : null
    onChange({ ...model, api_provider: draft?.name ?? value.slice(6) }, draft)
  }

  return (
    <FormSection title={synthesis ? '语音合成' : '语音识别'}>
      <FormRow label="语音类型" htmlFor="speech-group" required>
        <Select value={group} onValueChange={selectGroup}>
          <SelectTrigger id="speech-group"><SelectValue placeholder="选择语音类型" /></SelectTrigger>
          <SelectContent>
            {[...groups].map(([id, label]) => <SelectItem key={id} value={id}>{label}</SelectItem>)}
            {group === 'saved' && <SelectItem value="saved">{currentType ?? '已保存的类型'}</SelectItem>}
          </SelectContent>
        </Select>
      </FormRow>
      {pluginError && <p role="alert" className="text-xs text-destructive">无法加载插件模板：{pluginError}</p>}
      {group && <FormRow label="连接方式" htmlFor="speech-provider" required error={draftProvider ? undefined : errors.api_provider}>
        <Select value={draftProvider ? 'new' : savedConnection ? `saved:${savedConnection.name}` : ''} onValueChange={selectConnection}>
          <SelectTrigger id="speech-provider"><SelectValue placeholder="选择连接方式" /></SelectTrigger>
          <SelectContent>
            {choices.map((p) => <SelectItem key={p.name} value={`saved:${p.name}`}>复用 · {p.name}</SelectItem>)}
            {connectionType && !connectionType.clientType && <SelectItem value="new">填写官方 API Key</SelectItem>}
            {connectionType && <SelectItem value="custom">填写其他连接信息…</SelectItem>}
          </SelectContent>
        </Select>
      </FormRow>}
      {draftProvider && (
        <FormRow label="API Key" htmlFor="speech-key" required error={errors.api_provider}>
          <Input id="speech-key" type="password" autoComplete="off" value={draftProvider.api_key}
            onChange={(e) => onChange(model, { ...draftProvider, api_key: e.target.value })} />
          <p className="text-xs text-muted-foreground">{draftProvider.base_url}</p>
        </FormRow>
      )}
      {group && connectionReady && <FormRow label={synthesis ? '合成模型' : '识别模型'} htmlFor="speech-preset" required error={errors.model_identifier}>
        <Select value={model.model_identifier} onValueChange={selectModel}>
          <SelectTrigger id="speech-preset"><SelectValue placeholder="选择具体模型" /></SelectTrigger>
          <SelectContent>
            {candidates.map((item) => <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>)}
            {model.model_identifier && !preset && <SelectItem value={model.model_identifier}>{model.model_identifier}</SelectItem>}
          </SelectContent>
        </Select>
      </FormRow>}
      {synthesis && preset && connectionReady && (
        <FormRow label={voices.length ? '音色' : '试听'} htmlFor={voices.length ? 'speech-voice' : undefined} required={voices.length > 0}>
          <div className="flex items-start gap-2">
            {voices.length > 0 && <Select value={voice}
              onValueChange={(voice_id) => onChange({ ...model, extra_params: { ...model.extra_params, voice_id } }, draftProvider)}>
              <SelectTrigger id="speech-voice" className="min-w-0 flex-1"><SelectValue /></SelectTrigger>
              <SelectContent>
                {voices.map((item) => <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>)}
                {voice && !voices.some((item) => item.id === voice) && <SelectItem value={voice}>{voice}</SelectItem>}
              </SelectContent>
            </Select>}
            <SpeechPreviewButton model={model.model_identifier} voice={voice} provider={model.api_provider}
              draftKey={draftProvider?.api_key} />
          </div>
        </FormRow>
      )}
      {model.model_identifier && connectionReady && <FormRow label="名称" htmlFor="speech-name" required error={errors.name}>
        <Input id="speech-name" value={model.name} onChange={(e) => onChange({ ...model, name: e.target.value }, draftProvider)} />
        <p className="text-xs text-muted-foreground">保存后可在「分工 → {synthesis ? '语音合成' : '语音识别'}」中选用。</p>
      </FormRow>}
      {customConnection && <ProviderForm open fixedProtocol editingProvider={customConnection} editingIndex={null}
        providers={providers} tourState={{ isRunning: false }} onOpenChange={(open) => { if (!open) setCustomConnection(null) }}
        onSave={async (provider) => {
          const saved = await onCreateProvider(provider)
          if (!saved) return
          onChange({ ...model, api_provider: saved.name }, null)
          setCustomConnection(null)
        }} />}
    </FormSection>
  )
}
