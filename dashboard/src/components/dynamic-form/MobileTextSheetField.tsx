import * as React from 'react'
import { Check, ChevronRight, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { MOBILE_GLASS_CLASS } from '@/components/layout/mobile-glass'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { cn } from '@/lib/utils'

interface MobileTextSheetFieldProps {
  label: string
  description?: string
  value: string
  onChange: (value: string) => void
}

/**
 * 手机上的长文本字段：列表行只显示前两行内容，点按后从底部弹层里编辑，
 * 「完成」才写回配置，「取消」放弃本次修改。
 */
export function MobileTextSheetField({ label, description, value, onChange }: MobileTextSheetFieldProps) {
  const { t } = useTranslation()
  const [open, setOpen] = React.useState(false)
  const [draft, setDraft] = React.useState(value)

  const openSheet = () => {
    setDraft(value)
    setOpen(true)
  }

  const commit = () => {
    if (draft !== value) onChange(draft)
    setOpen(false)
  }

  return (
    <>
      <button
        type="button"
        data-mobile-text-row="true"
        onClick={openSheet}
        className="flex w-full min-w-0 items-start gap-3 py-1.5 text-left"
      >
        <span className="flex min-w-0 flex-1 flex-col gap-1">
          <span className="text-[15px] leading-6 font-medium">{label}</span>
          <span className="text-muted-foreground line-clamp-2 text-sm leading-[1.45] whitespace-pre-line">
            {value || t('dynamicForm.mobileTextEmpty')}
          </span>
        </span>
        <ChevronRight className="text-muted-foreground/60 mt-1 h-4 w-4 shrink-0" aria-hidden="true" />
      </button>

      <Dialog open={open} onOpenChange={(next) => (next ? setOpen(true) : setOpen(false))}>
        <DialogContent
          hideCloseButton
          data-mobile-text-sheet="true"
          className="max-sm:h-[88dvh] max-sm:gap-5 max-sm:px-4"
        >
          <div className="flex items-center justify-between gap-3">
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label={t('dynamicForm.mobileTextCancel')}
              data-mobile-sheet-button="cancel"
              className={cn('flex h-11 w-11 shrink-0 items-center justify-center rounded-full', MOBILE_GLASS_CLASS)}
            >
              <X className="h-5 w-5" strokeWidth={1.7} aria-hidden="true" />
            </button>
            <DialogTitle className="min-w-0 truncate text-[17px] font-semibold">{label}</DialogTitle>
            <button
              type="button"
              onClick={commit}
              aria-label={t('dynamicForm.mobileTextDone')}
              data-mobile-sheet-button="confirm"
              data-dialog-action="confirm"
              className="bg-foreground text-background flex h-11 w-11 shrink-0 items-center justify-center rounded-full"
            >
              <Check className="h-5 w-5" strokeWidth={1.9} aria-hidden="true" />
            </button>
          </div>
          <div
            data-mobile-text-sheet-card="true"
            className="bg-card flex min-h-0 flex-1 flex-col gap-2.5 rounded-[22px] px-[18px] py-4"
          >
            {description ? (
              <DialogDescription className="text-[13px]">{description}</DialogDescription>
            ) : (
              <DialogDescription className="sr-only">{label}</DialogDescription>
            )}
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              aria-label={label}
              autoFocus
              className="min-h-[12rem] flex-1 resize-none bg-transparent text-[17px] leading-relaxed outline-none"
            />
            <div className="text-muted-foreground text-right text-[13px] tabular-nums">
              {draft.length}
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
