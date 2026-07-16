import { Compass, MessageCircle, Sparkles, UserRound, type LucideIcon } from 'lucide-react'

import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'

type ConfigSectionData = Record<string, unknown>
type CoreSettingField = 'behavior_style' | 'personality' | 'reply_style'

interface CoreSettingsProps {
  botSection: ConfigSectionData | null
  onPersonalitySectionChange: (value: ConfigSectionData) => void
  personalitySection: ConfigSectionData | null
}

interface CoreSettingCardProps {
  accentClassName: string
  description: string
  eyebrow: string
  field: CoreSettingField
  icon: LucideIcon
  onChange: (field: CoreSettingField, value: string) => void
  placeholder: string
  title: string
  transformClassName?: string
  value: string
}

function requireSection(section: ConfigSectionData | null, sectionName: string): ConfigSectionData {
  if (!section) {
    throw new Error(`核心设置缺少 ${sectionName} 配置节`)
  }

  return section
}

function requireStringField(section: ConfigSectionData, field: string): string {
  const value = section[field]
  if (typeof value !== 'string') {
    throw new TypeError(`核心设置字段 ${field} 必须是字符串`)
  }

  return value
}

function CoreSettingCard({
  accentClassName,
  description,
  eyebrow,
  field,
  icon: Icon,
  onChange,
  placeholder,
  title,
  transformClassName,
  value,
}: CoreSettingCardProps) {
  const descriptionId = `core-setting-${field}-description`

  return (
    <article
      className={cn(
        'bg-card relative flex h-full min-w-0 flex-col rounded-[1.45rem] border p-4 shadow-[0_16px_42px_-32px_hsl(var(--foreground)/0.6)] transition-[transform,box-shadow] duration-300 hover:shadow-[0_20px_52px_-30px_hsl(var(--foreground)/0.7)] sm:p-5',
        transformClassName
      )}
    >
      <div className={cn('absolute top-5 bottom-5 left-0 w-1 rounded-r-full', accentClassName)} />
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-muted-foreground mb-1 text-[0.68rem] font-bold tracking-[0.14em] uppercase">
            {eyebrow}
          </div>
          <label
            htmlFor={`core-setting-${field}`}
            className="text-xl font-black tracking-tight sm:text-2xl"
          >
            {title}
          </label>
        </div>
        <div className={cn('text-primary-foreground rounded-xl p-2.5', accentClassName)}>
          <Icon className="h-4 w-4" />
        </div>
      </div>
      <p
        id={descriptionId}
        className="text-muted-foreground mb-3 min-h-10 text-xs leading-5 sm:text-sm"
      >
        {description}
      </p>
      <Textarea
        id={`core-setting-${field}`}
        aria-describedby={descriptionId}
        data-core-setting-field={field}
        value={value}
        onChange={(event) => onChange(field, event.target.value)}
        placeholder={placeholder}
        className="bg-muted/20 focus-visible:bg-background h-28 min-h-28 flex-1 resize-none rounded-xl border px-3 py-2.5 text-sm leading-6 shadow-inner focus-visible:ring-2 xl:h-32 xl:min-h-32"
      />
    </article>
  )
}

export function CoreSettings({
  botSection,
  onPersonalitySectionChange,
  personalitySection,
}: CoreSettingsProps) {
  const bot = requireSection(botSection, 'bot')
  const personality = requireSection(personalitySection, 'personality')
  const botName = requireStringField(bot, 'nickname')

  const updateCoreSetting = (field: CoreSettingField, value: string) => {
    onPersonalitySectionChange({
      ...personality,
      [field]: value,
    })
  }

  return (
    <section
      aria-label="麦麦核心设置"
      data-config-bot-core-settings="true"
      className="from-primary/[0.055] via-background to-muted/25 relative overflow-hidden rounded-[1.75rem] border bg-gradient-to-br px-4 py-5 sm:px-6 sm:py-6"
    >
      <div className="bg-primary/10 pointer-events-none absolute -top-24 -right-20 h-60 w-60 rounded-full blur-3xl" />
      <div className="bg-muted pointer-events-none absolute -bottom-36 -left-24 h-72 w-72 rounded-full opacity-60 blur-3xl" />

      <header className="relative flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="flex min-w-0 items-center gap-3">
          <span className="border-primary/20 bg-primary/10 text-primary inline-flex shrink-0 items-center rounded-full border px-3 py-1 text-[0.68rem] font-bold tracking-[0.14em] uppercase">
            核心设置
          </span>
          <h2 className="truncate text-2xl font-black tracking-[-0.04em] sm:text-3xl">{botName}</h2>
        </div>
        <div className="text-muted-foreground flex flex-wrap items-center gap-x-4 gap-y-1 text-xs sm:text-sm">
          <span>
            <strong className="text-foreground">人格 ＋ 表达</strong> 决定怎么说
          </span>
          <span className="text-border hidden sm:inline" aria-hidden="true">
            /
          </span>
          <span>
            <strong className="text-foreground">行为风格</strong> 决定怎么做
          </span>
          <span className="inline-flex items-center gap-1.5">
            <Sparkles className="text-primary h-3.5 w-3.5" />
            自动保存
          </span>
        </div>
      </header>

      <div className="relative mt-4 grid min-w-0 items-stretch gap-3 xl:grid-cols-[minmax(0,2fr)_2.25rem_minmax(17rem,1fr)]">
        <div className="relative min-w-0 px-3 pt-8 pb-3 sm:px-5 sm:pt-9 sm:pb-4">
          <svg
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 h-full w-full"
            preserveAspectRatio="none"
            viewBox="0 0 1000 360"
          >
            <path
              d="M38 62 C98 15 240 27 360 31 C521 37 664 10 846 35 C956 50 980 128 961 237 C946 322 829 346 684 331 C526 315 417 354 245 333 C100 315 26 267 34 171 C38 124 17 91 38 62 Z"
              fill="hsl(var(--muted) / 0.13)"
              stroke="hsl(var(--border))"
              strokeDasharray="9 9"
              strokeWidth="2"
              vectorEffect="non-scaling-stroke"
            />
          </svg>

          <div className="bg-background text-foreground absolute top-0 left-7 z-20 inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-bold shadow-sm">
            <MessageCircle className="text-primary h-3.5 w-3.5" />
            说话 · replyer
          </div>

          <div className="relative z-10 grid min-w-0 gap-4 md:grid-cols-2 xl:gap-5">
            <CoreSettingCard
              accentClassName="bg-violet-500"
              description="身份与长期性格。只交给 replyer，确保每次回复都像她自己。"
              eyebrow="replyer · 我是谁"
              field="personality"
              icon={UserRound}
              onChange={updateCoreSetting}
              placeholder="例如：你是一个正在网上和群友聊天的大学生……"
              title="人格配置"
              transformClassName="md:-rotate-[0.45deg]"
              value={requireStringField(personality, 'personality')}
            />
            <CoreSettingCard
              accentClassName="bg-sky-500"
              description="句子长短、语气与措辞。只约束 replyer 最终说出口的内容。"
              eyebrow="replyer · 怎么说"
              field="reply_style"
              icon={MessageCircle}
              onChange={updateCoreSetting}
              placeholder="例如：平淡简短，使用自然口语，不长篇大论……"
              title="表达方式"
              transformClassName="md:rotate-[0.5deg] md:translate-y-1"
              value={requireStringField(personality, 'reply_style')}
            />
          </div>
        </div>

        <div className="hidden items-center justify-center xl:flex" aria-hidden="true">
          <svg
            className="h-20 w-full overflow-visible"
            preserveAspectRatio="none"
            viewBox="0 0 42 80"
          >
            <path
              d="M3 42 C13 29 27 54 39 38"
              fill="none"
              stroke="hsl(var(--border))"
              strokeLinecap="round"
              strokeWidth="2"
              vectorEffect="non-scaling-stroke"
            />
            <circle cx="3" cy="42" fill="hsl(var(--primary))" r="3" />
            <circle cx="39" cy="38" fill="hsl(var(--primary))" r="3" />
          </svg>
        </div>

        <div className="relative min-w-0 px-1 pt-8 pb-3 sm:px-3 sm:pt-9 sm:pb-4">
          <div className="bg-background text-foreground absolute top-0 left-5 z-20 inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-bold shadow-sm">
            <Compass className="text-primary h-3.5 w-3.5" />
            行动 · planner
          </div>
          <CoreSettingCard
            accentClassName="bg-amber-500"
            description="何时参与、如何观察与选择动作。只交给 planner，不再读取人格。"
            eyebrow="planner · 怎么做"
            field="behavior_style"
            icon={Compass}
            onChange={updateCoreSetting}
            placeholder="例如：只在被提及或确实能推进话题时参与……"
            title="行为风格"
            transformClassName="xl:-rotate-[0.35deg]"
            value={requireStringField(personality, 'behavior_style')}
          />
        </div>
      </div>
    </section>
  )
}
