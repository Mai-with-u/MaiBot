import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { CodeEditor, type CodeEditorProps } from '@/components/CodeEditor'
import { Button } from '@/components/ui/button'
import { Popover, PopoverAnchor, PopoverContent } from '@/components/ui/popover'
import { getPromptBlockInstructions, getPromptFile, type PromptFileInfo } from '@/lib/prompt-api'

const templateBlocks: Record<string, string> = {
  chat_anlyze: 'reply_conversation_analysis.prompt',
  chat_anlyze_block: 'reply_conversation_analysis.prompt',
  planner_reasoning: 'reply_conversation_analysis.prompt',
  attachment_prompt: 'reply_attachments.prompt',
}

const dynamicBlocks: Record<string, { kind: string; example: string }> = {
  identity: { kind: 'identity', example: 'identity' },
  bot_name: { kind: 'identity', example: 'name' },
  reply_style: { kind: 'style', example: 'style' },
  dialogue_prompt: { kind: 'history', example: 'history' },
  expression_habits: { kind: 'expression', example: 'expression' },
  expression_habits_block: { kind: 'expression', example: 'expression' },
  chat_anlyze: { kind: 'analysis', example: 'analysis' },
  chat_anlyze_block: { kind: 'analysis', example: 'analysis' },
  planner_reasoning: { kind: 'analysis', example: 'analysis' },
  time_block: { kind: 'time', example: 'time' },
  current_time: { kind: 'time', example: 'time' },
  reply_target_block: { kind: 'target', example: 'target' },
  reply_requirements: { kind: 'requirements', example: 'requirements' },
  extra_info_block: { kind: 'requirements', example: 'requirements' },
  group_chat_attention_block: { kind: 'background', example: 'background' },
  chat_attention_block: { kind: 'background', example: 'background' },
  attention_drift_block: { kind: 'attention', example: 'requirements' },
  duplicate_target_reply_reminder: { kind: 'duplicate', example: 'duplicate' },
  attachment_block: { kind: 'attachment', example: 'attachment' },
  attachment_prompt: { kind: 'attachment', example: 'attachment' },
}

interface Props extends CodeEditorProps {
  promptLanguage: string
  promptFilename?: string
  files: PromptFileInfo[]
  onEditTemplate: (file: PromptFileInfo) => boolean
}

export function PromptBlockEditor({
  promptLanguage,
  promptFilename,
  files,
  onEditTemplate,
  ...props
}: Props) {
  const { t } = useTranslation()
  const [selected, setSelected] = useState<{ name: string; x: number; y: number } | null>(null)
  const [preview, setPreview] = useState<Array<{ label: string; content: string }>>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const targetName = selected ? (templateBlocks[selected.name] ?? `${selected.name}.prompt`) : ''
  const target = files.find((file) => file.name === targetName)
  const dynamic = selected ? dynamicBlocks[selected.name] : undefined

  useEffect(() => {
    if (!selected) return
    let cancelled = false
    setPreview([])
    setError('')
    const shortInstruction = selected.name === 'reply_guidance'
    if (!target && !shortInstruction) {
      setLoading(false)
      return
    }
    setLoading(true)
    const request = target
      ? getPromptFile(promptLanguage, target.name).then((result) => [
          { label: target.display_name, content: result.content },
        ])
      : getPromptBlockInstructions(promptLanguage, promptFilename).then((result) =>
          Object.entries(result[selected.name]).map(([label, content]) => ({ label, content }))
        )
    void request
      .then((result) => {
        if (!cancelled) setPreview(result)
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [selected, promptLanguage, promptFilename, target])

  useEffect(() => {
    setSelected(null)
  }, [promptLanguage, promptFilename])

  return (
    <>
      <CodeEditor {...props} highlightActiveLine={false} onPlaceholderClick={setSelected} />
      <Popover
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) setSelected(null)
        }}
      >
        <PopoverAnchor asChild>
          <span
            aria-hidden
            style={{
              position: 'fixed',
              left: selected?.x ?? 0,
              top: selected?.y ?? 0,
              width: 1,
              height: 1,
              pointerEvents: 'none',
            }}
          />
        </PopoverAnchor>
        <PopoverContent
          align="start"
          className="max-h-[min(70vh,600px)] w-[min(420px,calc(100vw-24px))] overflow-y-auto"
          aria-label={t('promptBlocks.title')}
        >
          <div className="mb-3 flex items-center justify-between gap-3">
            <code className="break-all">
              {'{'}
              {selected?.name}
              {'}'}
            </code>
            <Button variant="ghost" size="sm" onClick={() => setSelected(null)}>
              {t('promptBlocks.close')}
            </Button>
          </div>
          <p className="text-muted-foreground text-sm">
            {t(
              `promptBlocks.descriptions.${dynamic?.kind ?? (target ? 'template' : selected?.name === 'reply_guidance' ? 'instruction' : 'unknown')}`
            )}
          </p>
          {loading && (
            <p className="mt-3 text-sm" role="status">
              {t('promptBlocks.loading')}
            </p>
          )}
          {error && (
            <p className="text-destructive mt-3 text-sm" role="alert">
              {error}
            </p>
          )}
          {preview.map((item) => (
            <div key={item.label} className="mt-3 space-y-1">
              <div className="text-muted-foreground text-xs">
                {t('promptBlocks.actual')} ·{' '}
                {['简短表达', '正常回复', '长回复'].includes(item.label)
                  ? t(`promptBlocks.styles.${item.label}`)
                  : item.label}
              </div>
              <pre className="bg-muted rounded-md p-3 text-sm break-words whitespace-pre-wrap">
                {item.content}
              </pre>
            </div>
          ))}
          {!loading && !error && dynamic && (
            <div className="mt-3 space-y-1">
              <div className="text-muted-foreground text-xs">{t('promptBlocks.example')}</div>
              <pre className="bg-muted rounded-md p-3 text-sm break-words whitespace-pre-wrap">
                {t(`promptBlocks.examples.${dynamic.example}`)}
              </pre>
            </div>
          )}
          {!loading && !error && !dynamic && preview.length === 0 && (
            <p className="mt-3 text-sm">
              {t('promptBlocks.genericExample', { name: selected?.name })}
            </p>
          )}
          {target && (
            <Button
              className="mt-3"
              variant="outline"
              onClick={() => {
                if (onEditTemplate(target)) setSelected(null)
              }}
            >
              {t('promptBlocks.edit')}
            </Button>
          )}
        </PopoverContent>
      </Popover>
    </>
  )
}
