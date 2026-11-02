import type { ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog, DialogBody, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'

/** 将配置分组展示为按钮打开的卡片，字段状态仍由外层表单管理。 */
export function ConfigSectionDialog({ label, children, open, onOpenChange }: {
  label: string
  children: ReactNode
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        <Button type="button" variant="outline" className="min-h-8 text-base font-semibold">{label}</Button>
      </DialogTrigger>
      <DialogContent aria-describedby={undefined} className="[--dialog-width:48rem]">
        <DialogHeader><DialogTitle>{label}</DialogTitle></DialogHeader>
        <DialogBody>{children}</DialogBody>
      </DialogContent>
    </Dialog>
  )
}
