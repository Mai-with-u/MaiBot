"use client"

import { useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { KeyValueEditor } from "@/components/ui/key-value-editor"

interface ExtraParamsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  value: Record<string, unknown>
  onChange: (value: Record<string, unknown>) => void
}

export function ExtraParamsDialog({
  open,
  onOpenChange,
  value,
  onChange,
}: ExtraParamsDialogProps) {
  const [editingValue, setEditingValue] = useState<Record<string, unknown>>(value)

  // 当对话框打开状态改变时的处理
  const handleOpenChange = (newOpen: boolean) => {
    if (newOpen) {
      // 打开时同步最新的 value
      setEditingValue(value)
    }
    onOpenChange(newOpen)
  }

  const handleSave = () => {
    onChange(editingValue)
    onOpenChange(false)
  }

  const handleCancel = () => {
    setEditingValue(value) // 恢复原始值
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-3xl h-[70vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>编辑额外参数</DialogTitle>
          <DialogDescription>
            配置模型调用时的额外参数，支持嵌套对象和数组
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-hidden min-h-0">
          <KeyValueEditor
            value={editingValue}
            onChange={setEditingValue}
            placeholder="添加额外参数（如 thinking、top_p 等）..."
          />
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={handleCancel}>
            取消
          </Button>
          <Button onClick={handleSave}>保存</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
