import { useId, useRef, useState, type DragEvent, type ReactNode } from 'react'
import * as RadioGroupPrimitive from '@radix-ui/react-radio-group'
import { FileArchive, Upload, X } from 'lucide-react'

import { formatStorageBytes } from '@/lib/storage-size'
import { cn } from '@/lib/utils'
import { Button } from './button'
import type { ControlSize } from './control-size'
import { Checkbox } from './checkbox'
import { Separator } from './separator'
import { PageFrame } from './surface'

interface SectionPanelProps {
  title: ReactNode
  description?: ReactNode
  leading?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
  headerClassName?: string
}

/** 带标题和操作区的公共窗口；页面只决定内部布局。 */
export function SectionPanel({
  title,
  description,
  leading,
  actions,
  children,
  className,
  bodyClassName,
  headerClassName,
}: SectionPanelProps) {
  const titleId = useId()
  return (
    <PageFrame asChild className={className}>
      <section data-dashboard-section-panel="true" aria-labelledby={titleId}>
        <header data-section-part="header" className={cn('p-4', headerClassName)}>
          {leading && <span data-section-part="leading">{leading}</span>}
          <div data-section-part="heading">
            <h2 id={titleId}>{title}</h2>
            {description && <div data-section-part="description">{description}</div>}
          </div>
          {actions && <div data-section-part="actions">{actions}</div>}
        </header>
        <Separator variant="groove" />
        <div data-section-part="body" className={cn('p-4', bodyClassName)}>
          {children}
        </div>
      </section>
    </PageFrame>
  )
}

interface SelectionCardProps {
  id?: string
  label: string
  description?: ReactNode
  checked: boolean
  onCheckedChange?: (checked: boolean) => void
  disabled?: boolean
  required?: boolean
}

/** 必选项保持选中且只读，与暂不可用的禁用项分别表达。 */
export function SelectionCard({
  id,
  label,
  description,
  checked,
  onCheckedChange,
  disabled = false,
  required = false,
}: SelectionCardProps) {
  const generatedId = useId()
  const inputId = id ?? generatedId
  const readOnly = required || !onCheckedChange
  return (
    <label
      htmlFor={inputId}
      data-dashboard-selection-card="true"
      data-selected={required || checked}
      data-disabled={disabled}
      data-readonly={readOnly}
    >
      <Checkbox
        id={inputId}
        size="sm"
        checked={required || checked}
        disabled={disabled}
        aria-label={label}
        aria-readonly={readOnly || undefined}
        aria-describedby={description ? `${inputId}-description` : undefined}
        onCheckedChange={(next) => {
          if (!readOnly && !disabled) onCheckedChange?.(next === true)
        }}
      />
      <span data-selection-part="content">
        <span data-selection-part="label">{label}</span>
        {description && (
          <span id={`${inputId}-description`} data-selection-part="description">
            {description}
          </span>
        )}
      </span>
      {required && <span data-selection-part="required">必选</span>}
    </label>
  )
}

interface FileDropzoneProps {
  file: File | null
  onFileChange: (file: File | null) => void
  accept?: string
  disabled?: boolean
  label?: string
  description?: string
  onError?: (message: string) => void
}

function matchesAccept(file: File, accept: string): boolean {
  const rules = accept
    .split(',')
    .map((rule) => rule.trim().toLowerCase())
    .filter(Boolean)
  const filename = file.name.toLowerCase()
  const mime = file.type.toLowerCase()
  return (
    rules.length === 0 ||
    rules.some((rule) => {
      if (rule.startsWith('.')) return filename.endsWith(rule)
      if (rule.endsWith('/*')) return mime.startsWith(rule.slice(0, -1))
      return mime === rule
    })
  )
}

/** 文件选择和拖放共用校验；只交付本地 File，上传时机由业务决定。 */
export function FileDropzone({
  file,
  onFileChange,
  accept,
  disabled = false,
  label = '拖入文件，或点击选择',
  description,
  onError,
}: FileDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const dragDepth = useRef(0)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const descriptionId = useId()
  const errorId = useId()

  const selectFiles = (files: FileList | File[]) => {
    if (disabled || files.length === 0) return
    const nextError =
      files.length > 1
        ? '一次只能选择一个文件。'
        : accept && !matchesAccept(files[0], accept)
          ? `不支持此文件格式，请选择 ${accept} 文件。`
          : null
    setError(nextError)
    if (nextError) {
      onError?.(nextError)
      return
    }
    onFileChange(files[0])
  }

  const endDrag = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.stopPropagation()
    dragDepth.current = 0
    setDragging(false)
  }

  return (
    <div data-dashboard-file-field="true">
      <div
        data-dashboard-file-dropzone="true"
        data-dragging={dragging && !disabled}
        data-disabled={disabled}
        data-has-file={Boolean(file)}
        onDragEnter={(event) => {
          event.preventDefault()
          event.stopPropagation()
          dragDepth.current += 1
          if (!disabled) setDragging(true)
        }}
        onDragOver={(event) => {
          event.preventDefault()
          event.stopPropagation()
          event.dataTransfer.dropEffect = disabled ? 'none' : 'copy'
        }}
        onDragLeave={(event) => {
          event.preventDefault()
          event.stopPropagation()
          dragDepth.current = Math.max(0, dragDepth.current - 1)
          if (dragDepth.current === 0) setDragging(false)
        }}
        onDrop={(event) => {
          endDrag(event)
          selectFiles(event.dataTransfer.files)
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          disabled={disabled}
          hidden
          aria-label={label}
          onChange={(event) => {
            if (event.currentTarget.files) selectFiles(event.currentTarget.files)
            // 重选同一个文件也要触发校验；已选文件由受控属性显示。
            event.currentTarget.value = ''
          }}
        />
        <Button
          type="button"
          variant="ghost"
          disabled={disabled}
          data-file-part="picker"
          aria-label={file ? `更换文件：${file.name}` : label}
          aria-describedby={cn(description && descriptionId, error && errorId) || undefined}
          onClick={() => inputRef.current?.click()}
        >
          {file ? <FileArchive aria-hidden="true" /> : <Upload aria-hidden="true" />}
          <span data-file-part="label">{file ? file.name : label}</span>
          <span data-file-part="description" id={descriptionId}>
            {file ? `${formatStorageBytes(file.size)} · 点击更换文件` : description}
          </span>
        </Button>
        {file && (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            data-file-part="remove"
            disabled={disabled}
            aria-label="移除文件"
            onClick={() => {
              setError(null)
              onFileChange(null)
            }}
          >
            <X aria-hidden="true" />
          </Button>
        )}
      </div>
      {error && (
        <p role="alert" id={errorId} data-file-part="error">
          {error}
        </p>
      )}
    </div>
  )
}

interface ResourceRowProps {
  icon?: ReactNode
  title: ReactNode
  metadata?: ReactNode
  description?: ReactNode
  children?: ReactNode
  actions?: ReactNode
  className?: string
  onActivate?: () => void
  activationLabel?: string
}

/** 主动作通过原生按钮覆盖整行，行内操作在独立层，避免嵌套按钮和事件冒泡争抢。 */
export function ResourceRow({
  icon,
  title,
  metadata,
  description,
  children,
  actions,
  className,
  onActivate,
  activationLabel,
}: ResourceRowProps) {
  return (
    <div
      data-dashboard-resource-row="true"
      data-interactive={Boolean(onActivate)}
      className={cn('p-4', className)}
    >
      <div data-resource-part="main">
        {icon && (
          <div data-resource-part="icon" aria-hidden="true">
            {icon}
          </div>
        )}
        <div data-resource-part="content">
          <div data-resource-part="heading">
            <div data-resource-part="title">
              {onActivate ? (
                <button
                  type="button"
                  data-resource-part="primary-action"
                  aria-label={activationLabel}
                  onClick={onActivate}
                >
                  {title}
                </button>
              ) : (
                title
              )}
            </div>
            {metadata && <div data-resource-part="metadata">{metadata}</div>}
          </div>
          {description && <div data-resource-part="description">{description}</div>}
          {children && <div data-resource-part="details">{children}</div>}
        </div>
      </div>
      {actions && <div data-resource-part="actions">{actions}</div>}
    </div>
  )
}

interface SegmentedControlProps {
  size?: ControlSize
  className?: string
  value: string | undefined
  onValueChange: (value: string) => void
  options: { value: string; label: ReactNode; disabled?: boolean }[]
  disabled?: boolean
  'aria-label': string
}

/** 单选设置使用 radio group 的方向键逻辑，不借用切换页面的 tab 语义。 */
export function SegmentedControl({
  value,
  onValueChange,
  options,
  disabled,
  'aria-label': ariaLabel,
  size = 'sm',
  className,
}: SegmentedControlProps) {
  return (
    <RadioGroupPrimitive.Root
      data-dashboard-segmented-control="true"
      className={className}
      value={value ?? ''}
      onValueChange={onValueChange}
      disabled={disabled}
      aria-label={ariaLabel}
      orientation="horizontal"
    >
      {options.map((option) => (
        <RadioGroupPrimitive.Item
          key={option.value}
          asChild
          value={option.value}
          disabled={disabled || option.disabled}
        >
          <Button type="button" variant="outline" size={size} aria-pressed={value === option.value}>
            {option.label}
          </Button>
        </RadioGroupPrimitive.Item>
      ))}
    </RadioGroupPrimitive.Root>
  )
}
