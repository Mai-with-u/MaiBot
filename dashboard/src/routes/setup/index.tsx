import { useNavigate } from '@tanstack/react-router'
import {
  ArrowRight,
  CheckCircle2,
  Globe,
} from 'lucide-react'
import { useContext, useEffect, useEffectEvent, useLayoutEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HostSetup } from '@/host/HostSetup'
import { useHostRuntime } from '@/host/state'
import { DashboardStylePicker } from '@/components/dashboard-style-picker'
import { useTheme } from '@/components/use-theme'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { SetupWizardLayout } from './SetupWizardLayout'
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'
import { findTemplateByBaseUrl } from '@/routes/config/providerTemplates'
import { APP_NAME } from '@/lib/version'
import { useToast } from '@/hooks/use-toast'
import { validateToken } from '@/lib/token-validator'
import type { ModelListItem } from '@/lib/config-api'
import type { DashboardStyle } from '@/lib/theme/tokens'
import { DashboardStylePreviewContext } from '@/lib/theme-context'
import type {
  ApiProviderSetupConfig,
  BasicSetupConfig,
  SetupStep,
  BotBasicConfig,
  ModelSetupConfig,
  PersonalityConfig,
  SetupModelRole,
} from './types'
import {
  ApiProviderSetupForm,
  BotBasicForm,
  CustomTokenForm,
  ModelSetupForm,
  PersonalityForm,
  PersonalityPresetSelect,
} from './StepForms'
import {
  loadBotBasicConfig,
  loadBasicSetupConfig,
  loadSetupStatus,
  loadPersonalityConfig,
  loadApiProviderSetupConfig,
  loadModelSetupConfig,
  saveBotBasicConfig,
  saveBasicSetupConfig,
  saveDesktopPetEnabled,
  savePersonalityConfig,
  saveApiProviderSetupConfig,
  saveModelSetupConfig,
  completeSetup,
  fetchSetupModels,
  testSetupModel,
  updateAccessToken,
} from './api'
import { ConnectionTestPanel } from './ConnectionTestPanel'
import type { ConnectionTestState } from './ConnectionTestPanel'
import { PERSONALITY_MODES, readSetupDraft, writeSetupDraft } from './draft'
import type { PersonalityMode } from './draft'

const MODEL_FETCH_MAX_ATTEMPTS = 3
const LANGUAGE_CODES = ['zh', 'en', 'ja', 'ko'] as const
const LANGUAGE_NAMES: Record<(typeof LANGUAGE_CODES)[number], string> = {
  zh: '中文',
  en: 'English',
  ja: '日本語',
  ko: '한국어',
}

export function SetupPage() {
  const { host, selected_bot_id } = useHostRuntime()
  const draftKey = `maibot-setup-draft-v1:${host ? `bot:${selected_bot_id}` : 'standalone'}`
  const content = <SetupPageContent key={draftKey} draftKey={draftKey} />
  return host ? <HostSetup>{content}</HostSetup> : content
}

function applyDeepSeekDefaults(config: ModelSetupConfig, baseUrl: string): ModelSetupConfig {
  if (findTemplateByBaseUrl(baseUrl)?.id !== 'deepseek') return config
  return {
    ...config,
    planner_model_identifier: 'deepseek-flash',
    planner_model_name: 'deepseek-flash',
    planner_thinking: false,
    planner_reasoning_effort: 'high',
    replyer_model_identifier: 'deepseek-flash',
    replyer_model_name: 'deepseek-flash',
    replyer_thinking: false,
    replyer_reasoning_effort: 'high',
  }
}

// 内部实现组件
function SetupPageContent({ draftKey }: { draftKey: string }) {
  const navigate = useNavigate()
  const { t, i18n: i18nInstance } = useTranslation()
  const { toast } = useToast()
  const { themeConfig, updateThemeConfig } = useTheme()
  const setDashboardStylePreview = useContext(DashboardStylePreviewContext)
  const currentLang = i18nInstance.resolvedLanguage || i18nInstance.language || 'zh'
  const createDefaultPersonalityConfig = (): PersonalityConfig => ({
    personality: t('setupPage.defaults.personality.personality'),
    behavior_style: t('setupPage.defaults.personality.behaviorStyle'),
    reply_style: t('setupPage.defaults.personality.replyStyle'),
    multiple_reply_style: [
      t('setupPage.defaults.personality.multipleReplyStyles.plain'),
      t('setupPage.defaults.personality.multipleReplyStyles.shortText'),
      t('setupPage.defaults.personality.multipleReplyStyles.shortSymbol'),
      t('setupPage.defaults.personality.multipleReplyStyles.translation'),
    ],
    multiple_probability: 0.2,
  })
  const [activeStep, setActiveStep] = useState('nickname')
  const [transitionDirection, setTransitionDirection] = useState<1 | -1>(1)
  const [isCompleting, setIsCompleting] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const [draftReady, setDraftReady] = useState(false)
  const [requiresCustomToken, setRequiresCustomToken] = useState(false)
  const [customToken, setCustomToken] = useState('')
  const [customTokenConfirmation, setCustomTokenConfirmation] = useState('')
  const [basicSettings, setBasicSettings] = useState<BasicSetupConfig | null>(null)
  const [desktopPetDraft, setDesktopPetDraft] = useState<boolean | null>(null)
  const [dashboardStyleDraft, setDashboardStyleDraft] = useState<DashboardStyle | null>(null)
  const [needsApiKey, setNeedsApiKey] = useState(false)
  const [resumeModelStep, setResumeModelStep] = useState<SetupModelRole | null>(null)

  // 步骤1：Bot基础信息
  const [botBasic, setBotBasic] = useState<BotBasicConfig>({
    platform: '',
    qq_account: '',
    platforms: [],
    nickname: '',
    alias_names: [],
  })

  // 步骤2：人格配置
  const [personality, setPersonality] = useState<PersonalityConfig>(() =>
    createDefaultPersonalityConfig()
  )
  const [personalityMode, setPersonalityMode] = useState<PersonalityMode | null>(null)
  const [personalityDrafts, setPersonalityDrafts] = useState<Partial<Record<PersonalityMode, PersonalityConfig>>>({})
  const [personalityTemplate, setPersonalityTemplate] = useState<PersonalityConfig>()
  const [personalityPresetId, setPersonalityPresetId] = useState('default')
  const personalityDraft = personalityMode === 'template'
    ? personalityDrafts.template ?? personalityTemplate
    : personalityDrafts.manual

  const choosePersonalityMode = (mode: PersonalityMode) => {
    if (mode === 'template') {
      const { personality: traits, behavior_style, reply_style } = createDefaultPersonalityConfig()
      setPersonalityTemplate((template) => template ?? { ...personality, personality: traits, behavior_style, reply_style })
    } else {
      setPersonalityDrafts((drafts) => drafts.manual ? drafts : {
        ...drafts,
        manual: { ...personality, personality: '', behavior_style: '', reply_style: '' },
      })
    }
    setTransitionDirection(1)
    setPersonalityMode(mode)
    setActiveStep(mode === 'template' ? 'personality-template' : 'personality-edit')
  }

  // 步骤3：API 提供商配置
  const [apiProviderSetup, setApiProviderSetup] = useState<ApiProviderSetupConfig>({
    provider_name: '',
    base_url: '',
    api_key: '',
  })

  // 步骤4：基础模型配置
  const [modelSetup, setModelSetup] = useState<ModelSetupConfig>({
    planner_model_name: '',
    planner_model_identifier: '',
    planner_visual: false,
    planner_thinking: false,
    replyer_model_name: '',
    replyer_model_identifier: '',
    replyer_visual: false,
    replyer_thinking: false,
  })

  const profile = { id: 'profile', label: t('setupPage.steps.botProfile.title') }
  const models = { id: 'models', label: t('setupPage.steps.modelSetup.title') }
  const basics = { id: 'basics', label: t('setupPage.steps.basicSettings.title') }
  const step = (id: string, key: string, group?: SetupStep['group']): SetupStep => ({
    id, group,
    title: t(`setupPage.steps.${key}.title`),
    description: t(`setupPage.steps.${key}.description`),
  })
  const steps = [
    ...(requiresCustomToken ? [step('custom-token', 'customToken')] : []),
    {
      ...step('nickname', 'nickname', profile),
      label: t('setupPage.steps.nickname.label'),
    },
    {
      id: 'personality',
      group: profile,
      title: t('setupPage.steps.personality.title'),
      description: t('setupPage.steps.personality.choose'),
    },
    ...(personalityMode === 'template' ? [{
      id: 'personality-template',
      group: profile,
      title: t('setupPage.steps.personality.templateTitle'),
      description: '',
    }] : []),
    ...(personalityMode ? [{
      id: 'personality-edit',
      group: profile,
      title: t('setupPage.steps.personality.editTitle'),
      description: '',
    }] : []),
    {
      ...step('define-model', 'defineModel', models),
      label: t('setupPage.steps.defineModel.label'),
    },
    step('replyer', 'replyer', models),
    step('planner', 'planner', models),
    step('group-replies', 'groupReplies', basics),
    step('desktop-pet', 'desktopPet', basics),
    step('dashboard-appearance', 'dashboardAppearance', basics),
  ]

  const currentStep = requiresCustomToken ? 0 : Math.max(0, steps.findIndex((item) => item.id === activeStep))
  const currentStepId = steps[currentStep].id
  useLayoutEffect(() => {
    if (currentStepId !== 'dashboard-appearance' || dashboardStyleDraft === null) return
    setDashboardStylePreview(dashboardStyleDraft)
    return () => setDashboardStylePreview(null)
  }, [currentStepId, dashboardStyleDraft, setDashboardStylePreview])

  const configuringModels = steps[currentStep]?.group?.id === models.id
  const [availableModels, setAvailableModels] = useState<ModelListItem[]>([])
  const [modelFetchStatus, setModelFetchStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [modelFetchError, setModelFetchError] = useState('')

  useEffect(() => {
    if (!configuringModels) return
    setAvailableModels([])
    setModelFetchError('')
    if (!apiProviderSetup.base_url.trim()) {
      setModelFetchStatus('idle')
      return
    }

    setModelFetchStatus('loading')
    let cancelled = false
    let timer: ReturnType<typeof window.setTimeout>
    let attempt = 0
    const fetchModels = async () => {
      attempt += 1
      try {
        const models = await fetchSetupModels({
          provider_name: '',
          base_url: apiProviderSetup.base_url,
          api_key: apiProviderSetup.api_key,
        })
        if (cancelled) return
        setAvailableModels(models)
        setModelFetchError('')
        setModelFetchStatus('success')
      } catch (error) {
        if (cancelled) return
        setModelFetchError(error instanceof Error ? error.message : t('setupPage.errors.fetchModels'))
        setModelFetchStatus('error')
        // 失败后按 1s、2s 退避重试，最多请求 MODEL_FETCH_MAX_ATTEMPTS 次；
        // Key 或地址错误时不再持续请求服务商，修改输入后会重新开始。
        if (attempt < MODEL_FETCH_MAX_ATTEMPTS) {
          timer = window.setTimeout(fetchModels, 1000 * 2 ** (attempt - 1))
        }
      }
    }
    // 等待用户停止输入；切换地址或 Key 后忽略旧请求，避免串用提供商的列表。
    timer = window.setTimeout(fetchModels, 600)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [configuringModels, apiProviderSetup.base_url, apiProviderSetup.api_key, t])

  // 分别保留两种角色的测试结果，输入改动后不显示旧结果。
  const [connectionTests, setConnectionTests] = useState<Partial<Record<SetupModelRole, {
    key: string
    provider: ApiProviderSetupConfig
    state: ConnectionTestState
  }>>>({})
  const testingConnection = Object.values(connectionTests).some((entry) => entry.state.status === 'testing')
  const connectionTestKey = (role: SetupModelRole) => JSON.stringify([
    modelSetup[`${role}_model_identifier`],
    modelSetup[`${role}_visual`],
    modelSetup[`${role}_thinking`],
    modelSetup[`${role}_reasoning_effort`],
  ])
  const connectionTestState = (role: SetupModelRole): ConnectionTestState => {
    const entry = connectionTests[role]
    return entry?.provider === apiProviderSetup && entry.key === connectionTestKey(role)
      ? entry.state
      : { status: 'idle' }
  }

  const handleTestConnection = async (role: SetupModelRole) => {
    const error = validateApiProviderSetup(apiProviderSetup) ?? validateRoleModel(role)
    if (error) {
      toast({ title: t('setupPage.toast.validationFailedTitle'), description: error, variant: 'destructive' })
      return
    }
    const key = connectionTestKey(role)
    const updateTest = (state: ConnectionTestState) => {
      setConnectionTests((tests) => ({ ...tests, [role]: { key, provider: apiProviderSetup, state } }))
    }
    updateTest({ status: 'testing' })
    let name: string
    try {
      await saveApiProviderSetupConfig(apiProviderSetup)
      const names = await saveModelSetupConfig(modelSetup, apiProviderSetup.provider_name, role)
      name = names[role]
    } catch (saveError) {
      updateTest({
        status: 'error',
        error: saveError instanceof Error ? saveError.message : t('setupPage.toast.unknownError'),
      })
      return
    }
    try {
      const result = await testSetupModel(name)
      updateTest({
        status: 'done',
        results: [{ role, success: result.success, latencyMs: result.latency_ms, error: result.error }],
      })
    } catch (testError) {
      updateTest({
        status: 'done',
        results: [{
          role,
          success: false,
          latencyMs: null,
          error: testError instanceof Error ? testError.message : t('setupPage.toast.unknownError'),
        }],
      })
    }
  }

  const showLoadError = useEffectEvent((error: unknown) => {
    toast({
      title: t('setupPage.toast.loadFailedTitle'),
      description: error instanceof Error ? error.message : t('setupPage.toast.loadFailedDescription'),
      variant: 'destructive',
    })
  })

  // 加载现有配置
  useEffect(() => {
    let cancelled = false
    const loadConfigs = async () => {
      try {
        // 并行加载所有配置
        const [setupStatus, bot, personality, apiProvider, model, basic] = await Promise.all([
          loadSetupStatus(),
          loadBotBasicConfig(),
          loadPersonalityConfig(),
          loadApiProviderSetupConfig(),
          loadModelSetupConfig(),
          loadBasicSetupConfig(),
        ])
        if (cancelled) return

        const draft = readSetupDraft(draftKey)

        setRequiresCustomToken(setupStatus.requires_custom_token)
        setBotBasic(draft?.botBasic ?? bot)
        setPersonality(personality)
        setApiProviderSetup(draft ? { ...draft.provider, api_key: '' } : apiProvider)
        setModelSetup(draft?.modelSetup ?? applyDeepSeekDefaults(model, apiProvider.base_url))
        setBasicSettings({
          ...basic,
          inevitable_at_reply: draft?.basicSettings?.inevitable_at_reply ?? basic.inevitable_at_reply,
        })
        if (draft) {
          const { stepId, needsApiKey } = draft
          if (needsApiKey && (stepId === 'replyer' || stepId === 'planner')) {
            setActiveStep('define-model')
            setResumeModelStep(stepId)
          } else {
            setActiveStep(stepId === 'private-replies' || stepId === 'desktop-and-appearance' ? 'desktop-pet' : stepId)
          }
          setNeedsApiKey(needsApiKey)
          setPersonalityMode(draft.personalityMode)
          setPersonalityDrafts(draft.personalityDrafts)
          setPersonalityTemplate(draft.personalityTemplate)
          setPersonalityPresetId(draft.personalityPresetId)
          setDesktopPetDraft(draft.desktopPetDraft)
          setDashboardStyleDraft(draft.dashboardStyleDraft)
        }
        setDraftReady(true)
      } catch (error) {
        if (!cancelled) showLoadError(error)
      } finally {
        if (!cancelled) setIsLoading(false)
      }
    }

    loadConfigs()
    return () => {
      cancelled = true
    }
  }, [draftKey])

  useEffect(() => {
    if (!draftReady) return
    writeSetupDraft(draftKey, {
      stepId: resumeModelStep ?? activeStep,
      botBasic,
      personalityMode,
      personalityDrafts,
      personalityTemplate,
      personalityPresetId,
      provider: { provider_name: apiProviderSetup.provider_name, base_url: apiProviderSetup.base_url },
      needsApiKey: needsApiKey || Boolean(apiProviderSetup.api_key.trim()),
      modelSetup,
      basicSettings,
      desktopPetDraft,
      dashboardStyleDraft,
    })
  }, [
    activeStep, apiProviderSetup, basicSettings, botBasic, dashboardStyleDraft, desktopPetDraft,
    draftKey, draftReady, modelSetup, needsApiKey, personalityDrafts, personalityMode,
    personalityPresetId, personalityTemplate, resumeModelStep,
  ])

  // 保存当前步骤配置
  const saveCurrentStep = async () => {
    setIsSaving(true)
    try {
      switch (currentStepId) {
        case 'nickname':
          await saveBotBasicConfig(botBasic)
          break
        case 'personality-edit':
          if (!personalityDraft) return false
          await savePersonalityConfig(personalityDraft)
          break
        case 'personality-template':
        case 'define-model':
        case 'replyer':
        case 'desktop-pet':
          return true
        case 'planner':
          await saveApiProviderSetupConfig(apiProviderSetup)
          await saveModelSetupConfig(modelSetup, apiProviderSetup.provider_name)
          break
        case 'group-replies': {
          if (!basicSettings) throw new Error(t('setupPage.errors.loadBot'))
          await saveBasicSetupConfig({ inevitable_at_reply: basicSettings.inevitable_at_reply })
          break
        }
        case 'dashboard-appearance':
          if (!basicSettings) throw new Error(t('setupPage.errors.loadBot'))
          if (desktopPetDraft !== null && desktopPetDraft !== basicSettings.desktop_pet_enabled) {
            await saveDesktopPetEnabled(desktopPetDraft)
            setBasicSettings({ ...basicSettings, desktop_pet_enabled: desktopPetDraft })
          }
          break
      }

      toast({
        title: t('setupPage.toast.saveSuccessTitle'),
        description: t('setupPage.toast.saveSuccessDescription', {
          step: steps[currentStep].title,
        }),
        duration: 1000,
      })
      return true
    } catch (error) {
      toast({
        title: t('setupPage.toast.saveFailedTitle'),
        description: error instanceof Error ? error.message : t('setupPage.toast.unknownError'),
        variant: 'destructive',
      })
      return false
    } finally {
      setIsSaving(false)
    }
  }

  const saveCustomToken = async () => {
    if (!customTokenConfirmation || customTokenConfirmation !== customToken) {
      toast({
        title: t('setupPage.toast.validationFailedTitle'),
        description: t('setupPage.forms.customToken.mismatch'),
        variant: 'destructive',
      })
      return false
    }
    const trimmedToken = customToken.trim()
    const tokenValidation = validateToken(trimmedToken)
    if (!tokenValidation.isValid) {
      const failedRules = tokenValidation.rules
        .filter((rule) => !rule.passed)
        .map((rule) => rule.label)
        .join(', ')
      toast({
        title: t('setupPage.toast.validationFailedTitle'),
        description: t('setupPage.validation.customTokenInvalid', { failedRules }),
        variant: 'destructive',
      })
      return false
    }

    setIsSaving(true)
    try {
      const result = await updateAccessToken(trimmedToken)
      if (!result.success) {
        toast({
          title: t('setupPage.toast.saveFailedTitle'),
          description: result.message,
          variant: 'destructive',
        })
        return false
      }

      toast({
        title: t('setupPage.toast.customTokenSuccessTitle'),
        description: t('setupPage.toast.customTokenSuccessDescription'),
      })
      setCustomToken('')
      setCustomTokenConfirmation('')
      setRequiresCustomToken(false)
      return true
    } catch (error) {
      toast({
        title: t('setupPage.toast.saveFailedTitle'),
        description: error instanceof Error ? error.message : t('setupPage.toast.unknownError'),
        variant: 'destructive',
      })
      return false
    } finally {
      setIsSaving(false)
    }
  }

  // Step 1 验证
  function validateBotBasic(config: BotBasicConfig): string | null {
    if (!config.nickname.trim()) return t('setupPage.validation.enterNickname')
    return null
  }

  function validateApiProviderSetup(config: ApiProviderSetupConfig): string | null {
    if (!config.provider_name.trim()) return t('setupPage.validation.enterProviderName')
    if (!config.base_url.trim()) return t('setupPage.validation.enterBaseUrl')
    if (needsApiKey && !config.api_key.trim()) return t('setupPage.validation.enterApiKey')
    return null
  }

  function validateRoleModel(role: SetupModelRole): string | null {
    if (modelSetup[`${role}_model_identifier`].trim()) return null
    return t(role === 'planner'
      ? 'setupPage.validation.enterPlannerModelIdentifier'
      : 'setupPage.validation.enterReplyerModelIdentifier')
  }

  function validateModelSetup(config: ModelSetupConfig): string | null {
    if (!config.planner_model_identifier.trim()) {
      return t('setupPage.validation.enterPlannerModelIdentifier')
    }
    if (!config.replyer_model_identifier.trim()) {
      return t('setupPage.validation.enterReplyerModelIdentifier')
    }
    if (!apiProviderSetup.provider_name.trim()) return t('setupPage.validation.enterProviderName')
    return null
  }

  const handleNext = async () => {
    if (currentStepId === 'custom-token') {
      await saveCustomToken()
      return
    }

    let error: string | null = null
    if (currentStepId === 'nickname') error = validateBotBasic(botBasic)
    if (currentStepId === 'define-model') {
      error = validateApiProviderSetup(apiProviderSetup)
    }
    if (currentStepId === 'replyer' && !modelSetup.replyer_model_identifier.trim()) {
      error = t('setupPage.validation.enterReplyerModelIdentifier')
    }
    if (currentStepId === 'planner') error = validateModelSetup(modelSetup)
      ?? validateApiProviderSetup(apiProviderSetup)
    if (error) {
      toast({
        title: t('setupPage.toast.validationFailedTitle'),
        description: error,
        variant: 'destructive',
      })
      return
    }

    // 保存当前步骤
    const saved = await saveCurrentStep()
    if (!saved) return

    // 进入下一步
    if (currentStep < steps.length - 1) {
      setTransitionDirection(1)
      setActiveStep(resumeModelStep ?? steps[currentStep + 1].id)
      setResumeModelStep(null)
    }
  }

  const handlePrevious = () => {
    if (currentStep === 0) return

    setTransitionDirection(-1)
    if (steps[currentStep - 1].id === 'personality') {
      setPersonalityMode(null)
    }
    setActiveStep(steps[currentStep - 1].id)
    setResumeModelStep(null)
  }

  const handleComplete = async () => {
    setIsCompleting(true)

    try {
      // 保存桌宠设置，完成引导后应用界面风格并进入试聊。
      const saved = await saveCurrentStep()
      if (!saved) {
        return
      }

      await completeSetup()
      setDraftReady(false)
      writeSetupDraft(draftKey, null)

      if (dashboardStyleDraft !== null) {
        updateThemeConfig({ dashboardStyle: dashboardStyleDraft })
      }

      toast({
        title: t('setupPage.toast.completeSuccessTitle'),
        description: t('setupPage.toast.completeSuccessDescription', {
          appName: APP_NAME,
        }),
      })

      // MaiBot 会热加载配置文件，完成后进入聊聊体验本地对话。
      navigate({ to: '/chat', search: { setupTrial: true } })
    } catch (error) {
      toast({
        title: t('setupPage.toast.completeFailedTitle'),
        description: error instanceof Error ? error.message : t('setupPage.toast.unknownError'),
        variant: 'destructive',
      })
    } finally {
      setIsCompleting(false)
    }
  }

  const handleSkip = async () => {
    try {
      await completeSetup()
      setDraftReady(false)
      writeSetupDraft(draftKey, null)
      navigate({ to: '/' })
    } catch (error) {
      toast({
        title: t('setupPage.toast.skipFailedTitle'),
        description: error instanceof Error ? error.message : t('setupPage.toast.unknownError'),
        variant: 'destructive',
      })
    }
  }

  // 渲染当前步骤的表单
  const renderStepForm = () => {
    switch (currentStepId) {
      case 'custom-token':
        return (
          <CustomTokenForm
            token={customToken}
            onChange={setCustomToken}
            confirmation={customTokenConfirmation}
            onConfirmationChange={setCustomTokenConfirmation}
          />
        )
      case 'nickname':
        return <BotBasicForm config={botBasic} onChange={setBotBasic} />
      case 'personality-template':
        return personalityTemplate && <PersonalityForm config={personalityTemplate} />
      case 'personality-edit':
        return personalityMode && personalityDraft && (
          <PersonalityForm
            config={personalityDraft}
            onChange={(config) => setPersonalityDrafts((drafts) => ({ ...drafts, [personalityMode]: config }))}
          />
        )
      case 'personality':
        return (
          <div className="grid gap-3 sm:grid-cols-2">
            {PERSONALITY_MODES.map((mode) => (
              <Button
                key={mode}
                type="button"
                variant="outline"
                className="h-auto min-h-16 justify-start whitespace-normal px-4 py-3 text-left"
                onClick={() => choosePersonalityMode(mode)}
              >
                {t(`setupPage.steps.personality.${mode}`)}
              </Button>
            ))}
          </div>
        )
      case 'define-model':
        return (
          <div className="space-y-4">
            <ApiProviderSetupForm
              config={apiProviderSetup}
              initialCustomMode={!apiProviderSetup.provider_name && !apiProviderSetup.base_url}
              onChange={(next) => {
                if (next.base_url !== apiProviderSetup.base_url) {
                  setNeedsApiKey(false)
                  setResumeModelStep(null)
                  setModelSetup((previous) => applyDeepSeekDefaults({
                    ...previous,
                    planner_model_identifier: '',
                    planner_model_name: '',
                    replyer_model_identifier: '',
                    replyer_model_name: '',
                  }, next.base_url))
                }
                setApiProviderSetup(next)
              }}
            />
            {needsApiKey && !apiProviderSetup.api_key.trim() && (
              <p className="text-muted-foreground text-sm">{t('setupPage.validation.enterApiKey')}</p>
            )}
          </div>
        )
      case 'replyer':
      case 'planner':
        return (
          <div className="space-y-5">
            <ModelSetupForm
              role={currentStepId}
              config={modelSetup}
              onChange={setModelSetup}
              models={availableModels}
              fetchStatus={modelFetchStatus}
              fetchError={modelFetchError}
              providerBaseUrl={apiProviderSetup.base_url}
            />
            {modelSetup[`${currentStepId}_model_identifier`].trim() && (
              <ConnectionTestPanel
                state={connectionTestState(currentStepId)}
                onTest={() => void handleTestConnection(currentStepId)}
                disabled={isSaving || isCompleting || testingConnection}
              />
            )}
          </div>
        )
      case 'group-replies': {
        if (!basicSettings) return <p role="alert">{t('setupPage.errors.loadBot')}</p>
        return (
          <div className="border-border flex items-center justify-between gap-4 border p-4">
            <Label htmlFor="inevitable_at_reply">{t('setupPage.steps.groupReplies.label')}</Label>
            <Switch
              id="inevitable_at_reply"
              checked={basicSettings.inevitable_at_reply}
              onCheckedChange={(checked) => setBasicSettings({ ...basicSettings, inevitable_at_reply: checked })}
              disabled={isSaving || isCompleting}
            />
          </div>
        )
      }
      case 'desktop-pet':
        if (!basicSettings) return <p role="alert">{t('setupPage.errors.loadBot')}</p>
        return (
          <div className="border-border flex items-center justify-between gap-4 border p-4">
            <div className="space-y-1">
              <Label htmlFor="setup-desktop-pet">{t('setupPage.steps.desktopPet.label')}</Label>
              <p id="setup-desktop-pet-hint" className="text-muted-foreground whitespace-pre-line text-sm">
                {t('setupPage.steps.desktopPet.hint')}
              </p>
            </div>
            <Switch
              id="setup-desktop-pet"
              aria-describedby="setup-desktop-pet-hint"
              checked={desktopPetDraft ?? basicSettings.desktop_pet_enabled}
              onCheckedChange={setDesktopPetDraft}
              disabled={isSaving || isCompleting}
            />
          </div>
        )
      case 'dashboard-appearance':
        return (
          <fieldset aria-label={t('setupPage.steps.dashboardAppearance.title')}>
            <DashboardStylePicker
              value={dashboardStyleDraft ?? themeConfig.dashboardStyle}
              onChange={setDashboardStyleDraft}
              disabled={isSaving || isCompleting}
              className="grid gap-3 sm:grid-cols-3"
            />
          </fieldset>
        )
      default:
        return null
    }
  }

  return (
    <div className="relative flex h-full min-h-screen flex-col overflow-x-hidden overflow-y-auto px-4">
      {/* 向导操作与语言切换 */}
      <div className="flex items-center justify-end gap-2 pt-3">
        {!isLoading && (
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                disabled={isSaving || isCompleting || testingConnection || currentStepId === 'custom-token'}
              >
                {t('setupPage.actions.skip')}
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>{t('setupPage.skipDialog.title')}</AlertDialogTitle>
                <AlertDialogDescription>
                  {t('setupPage.skipDialog.description')}
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
                <AlertDialogAction onClick={handleSkip}>
                  {t('setupPage.skipDialog.confirm')}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        )}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="gap-2">
              <Globe className="h-4 w-4" />
              <span className="hidden text-xs sm:inline">
                {LANGUAGE_NAMES[currentLang.split('-')[0] as (typeof LANGUAGE_CODES)[number]] ??
                  currentLang}
              </span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {LANGUAGE_CODES.map((code) => (
              <DropdownMenuItem
                key={code}
                onClick={() => i18nInstance.changeLanguage(code)}
                className={cn(
                  'cursor-pointer',
                  currentLang.split('-')[0] === code && 'text-primary font-semibold'
                )}
              >
                {currentLang.split('-')[0] === code && <span className="mr-2">✓</span>}
                {LANGUAGE_NAMES[code]}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* 加载状态 */}
      {isLoading ? (
        <div className="relative z-10 flex flex-1 flex-col items-center justify-center text-center">
          <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center">
            <div className="border-primary h-12 w-12 animate-spin rounded-full border-4 border-t-transparent" />
          </div>
          <p className="text-lg font-medium">{t('setupPage.loading.title')}</p>
          <p className="text-muted-foreground mt-2 text-sm">{t('setupPage.loading.description')}</p>
        </div>
      ) : (
        <SetupWizardLayout
          steps={steps}
          currentStep={currentStep}
          hideTitle={currentStepId === 'personality-template' || currentStepId === 'personality-edit'}
          animateContentHeight={currentStepId === 'define-model'}
          transitionDirection={transitionDirection}
          headerActions={currentStepId === 'personality-template' && personalityTemplate ? (
            <PersonalityPresetSelect
              config={personalityTemplate}
              presetId={personalityPresetId}
              onChange={(config, presetId) => {
                setPersonalityPresetId(presetId)
                setPersonalityTemplate(config)
                setPersonalityDrafts((drafts) => ({ ...drafts, template: config }))
              }}
            />
          ) : undefined}
          actions={
            <div className="ml-auto flex items-center gap-3">
              {currentStep > 0 && (
                <Button variant="outline" onClick={handlePrevious} disabled={isSaving || isCompleting || testingConnection}>
                  {t('setupPage.actions.previous')}
                </Button>
              )}
              {currentStep === steps.length - 1 && currentStepId !== 'custom-token' ? (
                <Button
                  onClick={handleComplete}
                  disabled={isCompleting || isSaving}
                  className="flex-1 sm:flex-none"
                >
                  {isCompleting || isSaving ? (
                    <>
                      <div className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                      {isSaving
                        ? t('setupPage.actions.saving')
                        : t('setupPage.actions.completing')}
                    </>
                  ) : (
                    <>
                      {t('setupPage.actions.complete')}
                      <CheckCircle2 className="ml-2 h-4 w-4" strokeWidth={2} fill="none" />
                    </>
                  )}
                </Button>
              ) : currentStepId !== 'personality' && (
                <Button
                  onClick={handleNext}
                  variant={currentStepId === 'custom-token' && !validateToken(customToken).isValid ? 'outline' : 'default'}
                  size="lg"
                  disabled={
                    isSaving || testingConnection ||
                    (currentStepId === 'custom-token' && !validateToken(customToken).isValid)
                  }
                  className="min-w-44"
                >
                  {isSaving ? (
                    <>
                      <div className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                      {t('setupPage.actions.saving')}
                    </>
                  ) : (
                    <>
                      {currentStepId === 'custom-token'
                        ? t('setupPage.actions.saveToken')
                        : t('setupPage.actions.next')}
                      <ArrowRight className="ml-2 h-4 w-4" strokeWidth={2} fill="none" />
                    </>
                  )}
                </Button>
              )}
            </div>
          }
        >
          {/* 表单内容 */}
          {renderStepForm()}
        </SetupWizardLayout>
      )}
    </div>
  )
}
