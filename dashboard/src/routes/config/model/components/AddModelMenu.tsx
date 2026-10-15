import { Plus } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

export type ModelCreationType = 'llm' | 'embedding'

interface AddModelMenuProps {
  onAdd: (type: ModelCreationType) => void
  className?: string
  dataTour?: string
}

export function AddModelMenu({ onAdd, className, dataTour }: AddModelMenuProps) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="secondary" size="sm" className={className} data-tour={dataTour}>
          <Plus className="h-4 w-4" strokeWidth={2} fill="none" />
          添加
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onSelect={() => onAdd('llm')}>添加 LLM 模型</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => onAdd('embedding')}>添加嵌入模型</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
