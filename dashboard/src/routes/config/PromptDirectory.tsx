import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronRight, FileText, Folder, FolderOpen } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { PromptWarningDot } from '@/components/ui/prompt-warning-dot'
import { SelectionListItem } from '@/components/ui/selection-list-item'
import type { PromptFileInfo } from '@/lib/prompt-api'
import { cn } from '@/lib/utils'
import { buildPromptFolders, inPromptFolder, promptFolder, PROMPT_FOLDER_LABELS } from './prompt-folders'

interface PromptDirectoryProps {
  files: PromptFileInfo[]
  selectedFile?: PromptFileInfo
  filtering: boolean
  onSelect: (file: PromptFileInfo) => void
}

/** 文件与目录共用一列；展开目录不会改变编辑器中的文件。 */
export function PromptDirectory({
  files,
  selectedFile,
  filtering,
  onSelect,
}: PromptDirectoryProps) {
  const { t } = useTranslation()
  const folders = useMemo(() => buildPromptFolders(files, {
    ...PROMPT_FOLDER_LABELS,
    ...Object.fromEntries(['default', 'focus', 'mai_replier', 'common', 'writing', 'group', 'private'].map(key => [
      key, t(`promptFolders.${key}`, { defaultValue: PROMPT_FOLDER_LABELS[key] }),
    ])),
  }), [files, t])
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  function renderFolder(path: string, depth: number) {
    return (
      <>
        {folders
          .filter((folder) => folder.path.split('/').slice(0, -1).join('/') === path)
          .map((folder) => {
            const open =
              filtering ||
              (expanded[folder.path] ??
                Boolean(selectedFile && inPromptFolder(selectedFile, folder.path)))
            return (
              <Collapsible
                key={folder.path}
                open={open}
                onOpenChange={(value) =>
                  setExpanded((current) => ({ ...current, [folder.path]: value }))
                }
              >
                <CollapsibleTrigger asChild>
                  <SelectionListItem
                    aria-label={`${folder.label} ${folder.path}/ ${folder.count}`}
                    aria-pressed={undefined}
                    className="flex items-center gap-2 py-2"
                    style={{ paddingLeft: 8 + depth * 14 }}
                    title={`${folder.path}/`}
                  >
                    <ChevronRight className={cn('size-3 shrink-0', open && 'rotate-90')} />
                    {open ? (
                      <FolderOpen className="size-4 shrink-0" />
                    ) : (
                      <Folder className="size-4 shrink-0" />
                    )}
                    <span className="min-w-0 flex-1 truncate font-semibold">{folder.label}</span>
                    {folder.customized && (
                      <span data-prompt-modified-dot="true" title="包含已修改模板" />
                    )}
                    <span className="text-muted-foreground shrink-0 font-mono text-xs">
                      {folder.count}
                    </span>
                  </SelectionListItem>
                </CollapsibleTrigger>
                <CollapsibleContent>{renderFolder(folder.path, depth + 1)}</CollapsibleContent>
              </Collapsible>
            )
          })}
        {files
          .filter((file) => promptFolder(file) === path)
          .map((file) => (
            <SelectionListItem
              key={file.relative_path}
              selected={selectedFile?.name === file.name}
              onClick={() => onSelect(file)}
              data-prompt-file="true"
              style={{ paddingLeft: 12 + depth * 14 }}
              className="py-2"
              title={[
                file.relative_path,
                file.description,
                file.custom_version_count ? `${file.custom_version_count} 个自定义版本` : '',
              ]
                .filter(Boolean)
                .join('\n')}
            >
              <span className="flex items-center gap-2">
                <FileText className="size-3.5 shrink-0" />
                <span className="min-w-0 flex-1 truncate font-semibold">
                  {file.display_name || file.name}
                </span>
                {file.validation?.valid === false && (
                  <PromptWarningDot message={file.validation.message} />
                )}
                {file.customized && <span data-prompt-modified-dot="true" title="自定义" />}
                {file.advanced && (
                  <Badge variant="outline" className="shrink-0 text-[10px]">
                    高级
                  </Badge>
                )}
              </span>
              <span className="mt-1 block truncate font-mono text-[10px] opacity-75">
                {file.name}
              </span>
            </SelectionListItem>
          ))}
      </>
    )
  }

  return <nav aria-label="Prompt 模板目录">{renderFolder('', 0)}</nav>
}
