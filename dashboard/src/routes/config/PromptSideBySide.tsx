import { Check, Save } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { StatefulButton } from '@/components/ui/stateful-button'
import { useToast } from '@/hooks/use-toast'
import {
  getDefaultPromptFile,
  getPromptFile,
  getPromptVersionFile,
  updateDefaultPromptFile,
  updatePromptFile,
  type PromptFileContent,
  type PromptFileInfo,
} from '@/lib/prompt-api'
import { PromptBlockEditor } from './PromptBlockEditor'

interface Props {
  language: string
  files: PromptFileInfo[]
  filename: string
  content: string
  onChange: (content: string) => void
  rightFilename: string
  onEditTemplate: (file: PromptFileInfo) => boolean
  editDefaultPrompt: boolean
  onSave: () => Promise<void>
  saving: boolean
  hasUnsavedChanges: boolean
  saveLabel: string
  onRightStateChange: (dirty: boolean, busy: boolean) => void
  onSaved: () => void
}

export function PromptSideBySide({
  language,
  files,
  filename,
  content,
  onChange,
  rightFilename,
  onEditTemplate,
  editDefaultPrompt,
  onSave,
  saving,
  hasUnsavedChanges,
  saveLabel,
  onRightStateChange,
  onSaved,
}: Props) {
  const { t } = useTranslation()
  const { toast } = useToast()
  const [right, setRight] = useState<{
    key: string
    content: string
    savedContent: string
    error: string
    file: PromptFileContent | null
    versionId: string
  }>({ key: '', content: '', savedContent: '', error: '', file: null, versionId: '__default__' })
  const [rightSaving, setRightSaving] = useState(false)
  const [loadingVersion, setLoadingVersion] = useState(false)
  const sameFile = filename === rightFilename
  const key = `${language}/${rightFilename}`
  const leftFile = files.find((file) => file.name === filename)
  const rightFile = files.find((file) => file.name === rightFilename)

  const dirty = !sameFile && right.key === key && right.content !== right.savedContent
  const editingDefault = editDefaultPrompt && right.versionId === '__default__'
  const rightSaveLabel = editingDefault
    ? t('settings.other.saveDefaultPrompt')
    : right.versionId === '__default__'
      ? t('promptCompare.saveNew')
      : t('promptCompare.saveChanges')

  useEffect(() => {
    onRightStateChange(dirty, rightSaving || loadingVersion)
  }, [dirty, rightSaving, loadingVersion, onRightStateChange])

  useEffect(() => {
    if (!rightFilename || sameFile) return
    let cancelled = false
    void getPromptFile(language, rightFilename)
      .then((result) => {
        if (!cancelled) setRight({
          key, content: result.content, savedContent: result.content, error: '', file: result,
          versionId: result.active_version_id ?? '__default__',
        })
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setRight({
            key,
            content: '',
            savedContent: '',
            file: null,
            versionId: '__default__',
            error: cause instanceof Error ? cause.message : String(cause),
          })
      })
    return () => {
      cancelled = true
    }
  }, [language, rightFilename, key, sameFile])

  const handleVersionChange = async (versionId: string) => {
    if (dirty || rightSaving) return
    setLoadingVersion(true)
    try {
      const result = versionId === '__default__'
        ? await getDefaultPromptFile(language, rightFilename)
        : await getPromptVersionFile(language, rightFilename, versionId)
      setRight({ key, content: result.content, savedContent: result.content, error: '', file: result, versionId })
    } catch (error) {
      toast({ title: t('promptCompare.loadFailed'), description: (error as Error).message, variant: 'destructive' })
    } finally {
      setLoadingVersion(false)
    }
  }

  const handleSaveRight = async () => {
    if (!dirty || rightSaving || loadingVersion) return
    setRightSaving(true)
    try {
      const result = editingDefault
        ? await updateDefaultPromptFile(language, rightFilename, right.content)
        : await updatePromptFile(language, rightFilename, right.content, {
            versionId: right.versionId === '__default__' ? null : right.versionId,
            createVersion: right.versionId === '__default__',
          })
      setRight({
        key, content: result.content, savedContent: result.content, error: '', file: result,
        versionId: editingDefault ? '__default__' : (result.active_version_id ?? '__default__'),
      })
      toast({ title: t('promptCompare.saved'), description: `${language}/${rightFilename}` })
      onSaved()
    } catch (error) {
      toast({ title: t('promptCompare.saveFailed'), description: (error as Error).message, variant: 'destructive' })
    } finally {
      setRightSaving(false)
    }
  }

  return (
    <div className="grid h-full min-h-0 grid-cols-2 gap-2">
      <section className="flex min-h-0 min-w-0 flex-col gap-1" aria-label={t('promptCompare.left')}>
        <div className="flex min-w-0 items-center justify-between gap-2 text-xs">
          <span className="min-w-0 truncate font-medium" title={leftFile?.relative_path}>
            {leftFile?.display_name || filename}
          </span>
          <StatefulButton
            size="xs"
            className="shrink-0 gap-1"
            status={saving ? 'loading' : hasUnsavedChanges ? 'idle' : 'success'}
            idleLabel={saveLabel}
            loadingLabel={t('promptCompare.saving')}
            successLabel={t('promptCompare.saved')}
            idleIcon={<Save className="size-3" />}
            successIcon={<Check className="size-3" />}
            onClick={() => void onSave()}
            disabled={!hasUnsavedChanges || saving}
          />
        </div>
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
        className="flex min-h-0 min-w-0 flex-col gap-1"
        aria-label={t('promptCompare.right')}
      >
        <div className="flex min-w-0 items-center justify-between gap-2 text-xs">
          <span className="min-w-0 truncate font-medium" title={rightFile?.relative_path}>
            {rightFile?.display_name || t('promptCompare.right')}
          </span>
          {!sameFile && right.key === key && right.file && (
            <Select value={right.versionId} onValueChange={(value) => void handleVersionChange(value)} disabled={dirty || rightSaving || loadingVersion}>
              <SelectTrigger controlSize="xs" aria-label={t('promptCompare.rightVersion')} className="w-[120px] shrink-0 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__default__">{t('promptCompare.defaultVersion')}</SelectItem>
                {right.file.versions.map((version) => (
                  <SelectItem key={version.id} value={version.id}>{version.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <StatefulButton
            size="xs"
            className="shrink-0 gap-1"
            status={
              (sameFile ? saving : rightSaving)
                ? 'loading'
                : (sameFile ? hasUnsavedChanges : dirty)
                  ? 'idle'
                  : 'success'
            }
            idleLabel={sameFile ? saveLabel : rightSaveLabel}
            loadingLabel={t('promptCompare.saving')}
            successLabel={t('promptCompare.saved')}
            idleIcon={<Save className="size-3" />}
            successIcon={<Check className="size-3" />}
            onClick={() => void (sameFile ? onSave() : handleSaveRight())}
            disabled={sameFile ? !hasUnsavedChanges || saving : !dirty || rightSaving || loadingVersion}
          />
        </div>
        {!rightFilename ? (
          <p className="text-muted-foreground text-sm">{t('promptCompare.chooseFromDirectory')}</p>
        ) : sameFile ? (
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
        ) : right.key !== key || loadingVersion ? (
          <p role="status">{t('promptBlocks.loading')}</p>
        ) : right.error ? (
          <p role="alert" className="text-destructive break-words">
            {right.error}
          </p>
        ) : (
          <PromptBlockEditor
            value={right.content}
            readOnly={rightSaving}
            onChange={(value) => setRight((current) => ({ ...current, content: value }))}
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
