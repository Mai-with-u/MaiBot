import { Plus } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuPortal,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import type { ModelInfo } from '../types'

export type ModelCreationType = ModelInfo['model_type']

interface AddModelMenuProps {
  onAdd: (type: ModelCreationType) => void
  disabled: boolean
  className?: string
  dataTour?: string
}

export function AddModelMenu({ onAdd, disabled, className, dataTour }: AddModelMenuProps) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="sm" className={className} data-tour={dataTour}>
          <Plus className="h-4 w-4" strokeWidth={2} fill="none" />
          <span data-add-model-label="full">添加模型</span>
          <span data-add-model-label="compact" className="hidden">模型</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem disabled={disabled} onSelect={() => onAdd('llm')}>添加 LLM 模型</DropdownMenuItem>
        <DropdownMenuSub>
          <DropdownMenuSubTrigger data-other-models-trigger className="gap-2">
            其他模型
          </DropdownMenuSubTrigger>
          <DropdownMenuPortal>
            <DropdownMenuSubContent data-other-models-menu sideOffset={14} collisionPadding={8} className="z-[60]">
              <DropdownMenuItem disabled={disabled} onSelect={() => onAdd('embedding')}>嵌入模型</DropdownMenuItem>
              <DropdownMenuItem disabled={disabled} onSelect={() => onAdd('image_embedding')}>多模态嵌入模型</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => onAdd('asr')}>语音识别模型</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => onAdd('tts')}>语音合成模型</DropdownMenuItem>
            </DropdownMenuSubContent>
          </DropdownMenuPortal>
        </DropdownMenuSub>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
