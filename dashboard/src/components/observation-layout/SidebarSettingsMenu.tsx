import { MoreHorizontal } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { MenuItem } from '@/components/layout/types'
import { Button } from '@/components/ui/button'
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { SortableCardGrid } from '@/components/ui/sortable-card-grid'

export function SidebarSettingsMenu({ rows, items, onRowsChange, onReorder, onResetOrder }: {
  rows: number
  items: MenuItem[]
  onRowsChange: (rows: number) => void
  onReorder: (paths: string[]) => void
  onResetOrder: () => void
}) {
  const { t } = useTranslation()
  const [sortOpen, setSortOpen] = useState(false)
  return (
    <>
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <button type="button" data-observation-section-title="true" aria-label="侧边栏设置">
            <MoreHorizontal aria-hidden="true" strokeWidth={3.5} style={{ width: 20, height: 16 }} />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuLabel>显示排数</DropdownMenuLabel>
          <DropdownMenuRadioGroup value={String(rows)} onValueChange={(value) => onRowsChange(Number(value))}>
            {[1, 2, 3].map((count) => (
              <DropdownMenuRadioItem key={count} value={String(count)}>{['一排', '两排', '三排'][count - 1]}</DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setSortOpen(true)}>自定义卡片顺序</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <Dialog open={sortOpen} onOpenChange={setSortOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>自定义卡片顺序</DialogTitle>
            <DialogDescription>拖动卡片调整先后顺序，修改立即生效。也可用空格选中、方向键移动、空格放下。</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <SortableCardGrid
              label="侧边栏卡片顺序"
              items={items.map((item) => {
                const Icon = item.icon
                return { id: item.path, label: item.literalLabel ? item.label : t(item.label), icon: <Icon className="h-5 w-5" /> }
              })}
              onReorder={onReorder}
            />
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" onClick={onResetOrder}>恢复默认顺序</Button>
            <Button onClick={() => setSortOpen(false)}>完成</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
