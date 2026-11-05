import type { ComponentPropsWithoutRef, ReactNode } from 'react'

import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'
import '@/styles/form-section.css'

/** 两栏分组表单：窄屏自动变成单栏，栏间用刻线分开。 */
function FormColumns({ className, ...props }: ComponentPropsWithoutRef<'div'>) {
  return <div data-form-columns="true" className={className} {...props} />
}

function FormColumn({ className, ...props }: ComponentPropsWithoutRef<'div'>) {
  return <div data-form-column="true" className={cn('min-w-0', className)} {...props} />
}

interface FormSectionProps extends Omit<ComponentPropsWithoutRef<'section'>, 'title'> {
  title: ReactNode
  /** 标题行右侧的内容，比如单位说明、开关或按钮。 */
  action?: ReactNode
}

/** 一组设置：标题下面一道刻线，组内各行用虚线分隔。 */
function FormSection({ title, action, children, className, ...props }: FormSectionProps) {
  return (
    <section data-form-section="true" className={className} {...props}>
      <div data-form-section-heading="true">
        <h3>{title}</h3>
        {action}
      </div>
      {children}
    </section>
  )
}

interface FormRowProps extends Omit<ComponentPropsWithoutRef<'div'>, 'children'> {
  label: ReactNode
  htmlFor?: string
  required?: boolean
  error?: ReactNode
  /** 名称后面的补充，比如帮助提示图标。 */
  labelExtra?: ReactNode
  /** 名称对齐控件第一行，用于下面还会展开内容的行。 */
  alignTop?: boolean
  children: ReactNode
}

/** 名称固定一列，控件从同一条竖线开始；错误提示排在控件下方。 */
function FormRow({ label, htmlFor, required, error, labelExtra, alignTop, children, className, ...props }: FormRowProps) {
  return (
    <div data-form-row="true" data-align-top={alignTop ? 'true' : undefined} className={className} {...props}>
      <div data-form-row-label="true">
        <Label htmlFor={htmlFor} className={cn(error && 'text-destructive')}>
          {label}
          {required && <span aria-hidden="true" className="text-destructive"> *</span>}
        </Label>
        {labelExtra}
      </div>
      <div data-form-row-control="true">
        {children}
        {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
      </div>
    </div>
  )
}

/** 输入框后面的单位，不占控件本身的宽度。 */
function FormUnit({ className, ...props }: ComponentPropsWithoutRef<'span'>) {
  return <span data-form-unit="true" className={cn('text-xs text-muted-foreground whitespace-nowrap', className)} {...props} />
}

interface FoldSectionProps extends Omit<ComponentPropsWithoutRef<'section'>, 'title'> {
  title: ReactNode
  open: boolean
  onOpenChange: (open: boolean) => void
}

/** 可折叠的一组设置：标题本身是开关键，内容展开和收起都有过渡。 */
function FoldSection({ title, open, onOpenChange, children, className, ...props }: FoldSectionProps) {
  return (
    <section data-form-section="true" className={className} {...props}>
      <button
        type="button"
        data-form-section-heading="true"
        data-fold-trigger="true"
        aria-expanded={open}
        onClick={() => onOpenChange(!open)}
      >
        <h3>{title}</h3>
        <svg viewBox="0 0 24 24" aria-hidden="true" data-fold-chevron="true">
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      <div data-fold-body="true" data-open={open ? 'true' : 'false'} inert={!open}>
        <div data-fold-inner="true">{children}</div>
      </div>
    </section>
  )
}

export { FoldSection, FormColumn, FormColumns, FormRow, FormSection, FormUnit }
