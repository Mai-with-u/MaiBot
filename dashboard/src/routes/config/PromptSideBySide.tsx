import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { getPromptFile, type PromptFileInfo } from '@/lib/prompt-api'
import { PromptBlockEditor } from './PromptBlockEditor'

interface Props {
  language: string
  files: PromptFileInfo[]
  filename: string
  content: string
  onChange: (content: string) => void
  rightFilename: string
  onEditTemplate: (file: PromptFileInfo) => boolean
}

export function PromptSideBySide({
  language,
  files,
  filename,
  content,
  onChange,
  rightFilename,
  onEditTemplate,
}: Props) {
  const { t } = useTranslation()
  const [right, setRight] = useState({ key: '', content: '', error: '' })
  const key = `${language}/${rightFilename}`
  const leftFile = files.find((file) => file.name === filename)
  const rightFile = files.find((file) => file.name === rightFilename)

  useEffect(() => {
    if (!rightFilename) return
    let cancelled = false
    void getPromptFile(language, rightFilename)
      .then((result) => {
        if (!cancelled) setRight({ key, content: result.content, error: '' })
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setRight({
            key,
            content: '',
            error: cause instanceof Error ? cause.message : String(cause),
          })
      })
    return () => {
      cancelled = true
    }
  }, [language, rightFilename, key])

  return (
    <div className="grid h-full min-h-0 grid-cols-2 gap-3">
      <section className="flex min-h-0 min-w-0 flex-col gap-2" aria-label={t('promptCompare.left')}>
        <div className="truncate text-sm" title={leftFile?.relative_path}>
          {leftFile?.display_name || filename}
        </div>
        <div className="text-muted-foreground text-xs">{t('promptCompare.current')}</div>
        <PromptBlockEditor
          value={content}
          onChange={onChange}
          language="text"
          height="100%"
          minHeight="0"
          className="min-h-0 flex-1"
          promptLanguage={language}
          promptFilename={filename}
          files={files}
          onEditTemplate={onEditTemplate}
        />
      </section>
      <section
        className="flex min-h-0 min-w-0 flex-col gap-2"
        aria-label={t('promptCompare.right')}
      >
        <div className="truncate text-sm" title={rightFile?.relative_path}>
          {rightFile?.display_name || t('promptCompare.right')}
        </div>
        <div className="text-muted-foreground text-xs">{t('promptCompare.applied')}</div>
        {!rightFilename ? (
          <p className="text-muted-foreground text-sm">{t('promptCompare.chooseFromDirectory')}</p>
        ) : right.key !== key ? (
          <p role="status">{t('promptBlocks.loading')}</p>
        ) : right.error ? (
          <p role="alert" className="text-destructive break-words">
            {right.error}
          </p>
        ) : (
          <PromptBlockEditor
            value={right.content}
            readOnly
            language="text"
            height="100%"
            minHeight="0"
            className="min-h-0 flex-1"
            promptLanguage={language}
            promptFilename={rightFilename}
            files={files}
            onEditTemplate={onEditTemplate}
          />
        )}
      </section>
    </div>
  )
}
