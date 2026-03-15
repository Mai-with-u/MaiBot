import { useNavigate } from '@tanstack/react-router'
import {
  ArrowRight,
  Bot,
  CheckCircle2,
  Globe,
  Key,
  Settings,
  SkipForward,
  Smile,
  Sparkles,
  User,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { ScrollArea } from '@/components/ui/scroll-area'
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
import { APP_NAME } from '@/lib/version'
import { useToast } from '@/hooks/use-toast'
import type {
  SetupStep,
  BotBasicConfig,
  PersonalityConfig,
  EmojiConfig,
  OtherBasicConfig,
  SiliconFlowConfig,
} from './types'
import {
  BotBasicForm,
  PersonalityForm,
  EmojiForm,
  OtherBasicForm,
  SiliconFlowForm,
} from './StepForms'
import {
  loadBotBasicConfig,
  loadPersonalityConfig,
  loadEmojiConfig,
  loadOtherBasicConfig,
  loadSiliconFlowConfig,
  saveBotBasicConfig,
  savePersonalityConfig,
  saveEmojiConfig,
  saveOtherBasicConfig,
  saveSiliconFlowConfig,
  completeSetup,
} from './api'
import { RestartProvider, useRestart } from '@/lib/restart-context'
import { RestartOverlay } from '@/components/restart-overlay'

const LANGUAGE_CODES = ['zh', 'en', 'ja', 'ko'] as const
const LANGUAGE_NAMES: Record<(typeof LANGUAGE_CODES)[number], string> = {
  zh: '中文',
  en: 'English',
  ja: '日本語',
  ko: '한국어',
}

// 主导出组件：包装 RestartProvider
export function SetupPage() {
  return (
    <RestartProvider>
      <SetupPageContent />
    </RestartProvider>
  )
}

// 内部实现组件
function SetupPageContent() {
  const navigate = useNavigate()
  const { t, i18n: i18nInstance } = useTranslation()
  const { toast } = useToast()
  const { triggerRestart } = useRestart()
  const currentLang = i18nInstance.resolvedLanguage || i18nInstance.language || 'zh'
  const createDefaultPersonalityConfig = (): PersonalityConfig => ({
    personality: t('setupPage.defaults.personality.personality'),
    reply_style: t('setupPage.defaults.personality.replyStyle'),
    interest: t('setupPage.defaults.personality.interest'),
    plan_style: t('setupPage.defaults.personality.planStyle'),
    private_plan_style: t('setupPage.defaults.personality.privatePlanStyle'),
  })
  const createDefaultEmojiConfig = (): EmojiConfig => ({
    emoji_chance: 0.4,
    max_reg_num: 40,
    do_replace: true,
    check_interval: 10,
    steal_emoji: true,
    content_filtration: false,
    filtration_prompt: t('setupPage.defaults.emoji.filtrationPrompt'),
  })
  const [currentStep, setCurrentStep] = useState(0)
  const [isCompleting, setIsCompleting] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [isLoading, setIsLoading] = useState(true)

  // 步骤1：Bot基础信息
  const [botBasic, setBotBasic] = useState<BotBasicConfig>({
    platform: '',
    qq_account: 0,
    platforms: [],
    nickname: '',
    alias_names: [],
  })

  // 步骤2：人格配置
  const [personality, setPersonality] = useState<PersonalityConfig>(() =>
    createDefaultPersonalityConfig()
  )

  // 步骤3：表情包配置
  const [emoji, setEmoji] = useState<EmojiConfig>(() => createDefaultEmojiConfig())

  // 步骤4：其他基础配置
  const [otherBasic, setOtherBasic] = useState<OtherBasicConfig>({
    enable_tool: true,
    all_global: true,
  })

  // 步骤5：硅基流动API配置
  const [siliconFlow, setSiliconFlow] = useState<SiliconFlowConfig>({
    api_key: '',
  })

  const steps: SetupStep[] = [
    {
      id: 'bot-basic',
      title: t('setupPage.steps.botBasic.title'),
      description: t('setupPage.steps.botBasic.description'),
      icon: Bot,
    },
    {
      id: 'personality',
      title: t('setupPage.steps.personality.title'),
      description: t('setupPage.steps.personality.description'),
      icon: User,
    },
    {
      id: 'emoji',
      title: t('setupPage.steps.emoji.title'),
      description: t('setupPage.steps.emoji.description'),
      icon: Smile,
    },
    {
      id: 'other',
      title: t('setupPage.steps.other.title'),
      description: t('setupPage.steps.other.description'),
      icon: Settings,
    },
    {
      id: 'siliconflow',
      title: t('setupPage.steps.siliconFlow.title'),
      description: t('setupPage.steps.siliconFlow.description'),
      icon: Key,
    },
  ]

  const progress = ((currentStep + 1) / steps.length) * 100

  // 加载现有配置
  useEffect(() => {
    const loadConfigs = async () => {
      try {
        setIsLoading(true)

        // 并行加载所有配置
        const [bot, personality, emoji, other, silicon] = await Promise.all([
          loadBotBasicConfig(),
          loadPersonalityConfig(),
          loadEmojiConfig(),
          loadOtherBasicConfig(),
          loadSiliconFlowConfig(),
        ])

        setBotBasic(bot)
        setPersonality(personality)
        setEmoji(emoji)
        setOtherBasic(other)
        setSiliconFlow(silicon)
      } catch (error) {
        toast({
          title: t('setupPage.toast.loadFailedTitle'),
          description:
            error instanceof Error ? error.message : t('setupPage.toast.loadFailedDescription'),
          variant: 'destructive',
        })
      } finally {
        setIsLoading(false)
      }
    }

    loadConfigs()
  }, [t, toast])

  // 保存当前步骤配置
  const saveCurrentStep = async () => {
    setIsSaving(true)
    try {
      switch (currentStep) {
        case 0: // Bot基础
          await saveBotBasicConfig(botBasic)
          break
        case 1: // 人格配置
          await savePersonalityConfig(personality)
          break
        case 2: // 表情包
          await saveEmojiConfig(emoji)
          break
        case 3: // 其他设置
          await saveOtherBasicConfig(otherBasic)
          break
        case 4: // 硅基流动API
          await saveSiliconFlowConfig(siliconFlow)
          break
      }

      toast({
        title: t('setupPage.toast.saveSuccessTitle'),
        description: t('setupPage.toast.saveSuccessDescription', {
          step: steps[currentStep].title,
        }),
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

  // Step 1 验证
  function validateBotBasic(config: BotBasicConfig): string | null {
    if (!config.platform) return t('setupPage.validation.selectPlatform')
    if (!config.nickname.trim()) return t('setupPage.validation.enterNickname')
    if (config.platform === 'qq') {
      if (!config.qq_account || config.qq_account <= 0) {
        return t('setupPage.validation.enterQqAccount')
      }
    } else {
      const hasAccount = config.platforms.some(
        (p) => p.startsWith(config.platform + ':') && p.split(':')[1]?.trim()
      )
      if (!hasAccount) return t('setupPage.validation.enterAccountId')
    }
    return null
  }

  const handleNext = async () => {
    // Step 1 验证
    if (currentStep === 0) {
      const error = validateBotBasic(botBasic)
      if (error) {
        toast({
          title: t('setupPage.toast.validationFailedTitle'),
          description: error,
          variant: 'destructive',
        })
        return
      }
    }

    // 保存当前步骤
    const saved = await saveCurrentStep()
    if (!saved) return

    // 进入下一步
    if (currentStep < steps.length - 1) {
      setCurrentStep(currentStep + 1)
    }
  }

  const handlePrevious = () => {
    if (currentStep > 0) {
      setCurrentStep(currentStep - 1)
    }
  }

  const handleComplete = async () => {
    setIsCompleting(true)

    try {
      // 1. 保存最后一步的配置(硅基流动API Key)
      const saved = await saveCurrentStep()
      if (!saved) {
        setIsCompleting(false)
        return
      }

      // 2. 标记设置完成
      await completeSetup()

      toast({
        title: t('setupPage.toast.completeSuccessTitle'),
        description: t('setupPage.toast.completeSuccessDescription', {
          appName: APP_NAME,
        }),
      })

      // 3. 触发麦麦重启（使用新的重启组件）
      await triggerRestart()
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
    switch (currentStep) {
      case 0:
        return <BotBasicForm config={botBasic} onChange={setBotBasic} />
      case 1:
        return <PersonalityForm config={personality} onChange={setPersonality} />
      case 2:
        return <EmojiForm config={emoji} onChange={setEmoji} />
      case 3:
        return <OtherBasicForm config={otherBasic} onChange={setOtherBasic} />
      case 4:
        return <SiliconFlowForm config={siliconFlow} onChange={setSiliconFlow} />
      default:
        return null
    }
  }

  return (
    <div className="from-primary/5 via-background to-secondary/5 relative flex min-h-screen flex-col items-center justify-center overflow-hidden bg-gradient-to-br p-4 md:p-6">
      {/* 重启遮罩层 */}
      <RestartOverlay />

      {/* 语言切换 */}
      <div className="absolute top-4 right-4 z-20">
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

      {/* 背景装饰 */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="bg-primary/5 absolute top-1/4 left-1/4 h-64 w-64 rounded-full blur-3xl md:h-96 md:w-96" />
        <div className="bg-secondary/5 absolute right-1/4 bottom-1/4 h-64 w-64 rounded-full blur-3xl md:h-96 md:w-96" />
      </div>

      {/* 加载状态 */}
      {isLoading ? (
        <div className="relative z-10 text-center">
          <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center">
            <div className="border-primary h-12 w-12 animate-spin rounded-full border-4 border-t-transparent" />
          </div>
          <p className="text-lg font-medium">{t('setupPage.loading.title')}</p>
          <p className="text-muted-foreground mt-2 text-sm">{t('setupPage.loading.description')}</p>
        </div>
      ) : (
        <>
          {/* 主要内容 */}
          <div className="relative z-10 w-full max-w-4xl">
            {/* 头部 */}
            <div className="mb-6 text-center md:mb-8">
              <div className="bg-primary/10 mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl md:h-16 md:w-16">
                <Sparkles
                  className="text-primary h-6 w-6 md:h-8 md:w-8"
                  strokeWidth={2}
                  fill="none"
                />
              </div>
              <h1 className="mb-2 text-2xl font-bold md:text-3xl">{t('setupPage.header.title')}</h1>
              <p className="text-muted-foreground text-sm md:text-base">
                {t('setupPage.header.description', { appName: APP_NAME })}
              </p>
            </div>

            {/* 进度条 */}
            <div className="mb-6 md:mb-8">
              <div className="mb-2 flex items-center justify-between text-xs md:text-sm">
                <span className="text-muted-foreground">
                  {t('setupPage.progress.stepCounter', {
                    current: currentStep + 1,
                    total: steps.length,
                  })}
                </span>
                <span className="text-primary font-medium">{Math.round(progress)}%</span>
              </div>
              <Progress value={progress} className="h-2" />
            </div>

            {/* 步骤指示器 */}
            <div className="mb-6 flex justify-between md:mb-8">
              {steps.map((step, index) => {
                const Icon = step.icon
                return (
                  <div
                    key={step.id}
                    className={cn(
                      'flex flex-1 flex-col items-center gap-1 md:gap-2',
                      index < steps.length - 1 && 'relative'
                    )}
                  >
                    {/* 连接线 */}
                    {index < steps.length - 1 && (
                      <div
                        className={cn(
                          'absolute top-3 left-1/2 h-0.5 w-full md:top-4',
                          index < currentStep ? 'bg-primary' : 'bg-border'
                        )}
                      />
                    )}

                    {/* 步骤圆圈 */}
                    <div
                      className={cn(
                        'relative z-10 flex h-6 w-6 items-center justify-center rounded-full border-2 transition-all md:h-8 md:w-8',
                        index === currentStep
                          ? 'border-primary bg-primary text-primary-foreground'
                          : index < currentStep
                            ? 'border-primary bg-primary text-primary-foreground'
                            : 'border-border bg-background text-muted-foreground'
                      )}
                    >
                      {index < currentStep ? (
                        <CheckCircle2
                          className="h-3 w-3 md:h-4 md:w-4"
                          strokeWidth={2.5}
                          fill="none"
                        />
                      ) : (
                        <Icon className="h-3 w-3 md:h-4 md:w-4" />
                      )}
                    </div>

                    {/* 步骤标题 */}
                    <span
                      className={cn(
                        'max-w-[60px] truncate text-center text-[10px] md:max-w-none md:text-xs md:whitespace-normal',
                        index === currentStep
                          ? 'text-foreground font-medium'
                          : 'text-muted-foreground'
                      )}
                      title={step.title}
                    >
                      {step.title}
                    </span>
                  </div>
                )
              })}
            </div>

            {/* 步骤内容卡片 */}
            <Card className="mb-6 shadow-lg md:mb-8">
              <CardContent className="p-4 md:p-8">
                <div className="min-h-[300px] md:min-h-[400px]">
                  <div className="mb-4 md:mb-6">
                    <h2 className="mb-2 text-xl font-semibold md:text-2xl">
                      {steps[currentStep].title}
                    </h2>
                    <p className="text-muted-foreground text-sm md:text-base">
                      {steps[currentStep].description}
                    </p>
                  </div>

                  {/* 表单内容 */}
                  <ScrollArea className="h-[400px] md:h-[500px]">
                    <div className="pr-2">{renderStepForm()}</div>
                  </ScrollArea>
                </div>
              </CardContent>
            </Card>

            {/* 操作按钮 */}
            <div className="flex flex-col items-center justify-between gap-3 sm:flex-row sm:gap-0">
              <Button
                variant="outline"
                onClick={handlePrevious}
                disabled={currentStep === 0 || isSaving}
                className="order-2 w-full sm:order-1 sm:w-auto"
              >
                {t('setupPage.actions.previous')}
              </Button>

              <div className="order-1 flex w-full gap-2 sm:order-2 sm:w-auto">
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button
                      variant="ghost"
                      className="flex-1 gap-2 sm:flex-none"
                      disabled={isSaving || isCompleting}
                    >
                      <SkipForward className="h-4 w-4" strokeWidth={2} fill="none" />
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

                {currentStep === steps.length - 1 ? (
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
                ) : (
                  <Button onClick={handleNext} disabled={isSaving} className="flex-1 sm:flex-none">
                    {isSaving ? (
                      <>
                        <div className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                        {t('setupPage.actions.saving')}
                      </>
                    ) : (
                      <>
                        {t('setupPage.actions.next')}
                        <ArrowRight className="ml-2 h-4 w-4" strokeWidth={2} fill="none" />
                      </>
                    )}
                  </Button>
                )}
              </div>
            </div>
          </div>

          {/* 页脚提示 */}
          <div className="text-muted-foreground relative z-10 mt-6 text-center text-xs md:mt-8">
            <p>{t('setupPage.footer')}</p>
          </div>
        </>
      )}
    </div>
  )
}
