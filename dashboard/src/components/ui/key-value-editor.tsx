"use client"

import { useState, useEffect, useCallback, useMemo } from "react"
import { AlertCircle, Check } from "lucide-react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"
import { NestedKeyValueEditor } from "./nested-key-value-editor"

interface KeyValueEditorProps {
  value: Record<string, unknown>
  onChange: (value: Record<string, unknown>) => void
  className?: string
  placeholder?: string
}

// 验证 JSON 字符串
function validateJson(jsonStr: string): { valid: boolean; error?: string; parsed?: Record<string, unknown> } {
  if (!jsonStr.trim()) {
    return { valid: true, parsed: {} }
  }
  try {
    const parsed = JSON.parse(jsonStr)
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      return { valid: false, error: '必须是一个 JSON 对象 {}' }
    }
    // 支持任意 JSON 值类型（包括嵌套对象和数组）
    return { valid: true, parsed: parsed as Record<string, unknown> }
  } catch {
    return { valid: false, error: 'JSON 格式错误' }
  }
}

export function KeyValueEditor({
  value,
  onChange,
  className,
  placeholder = "添加额外参数...",
}: KeyValueEditorProps) {
  const [mode, setMode] = useState<'list' | 'json'>('list')
  
  const initialJsonText = useMemo(() => 
    Object.keys(value || {}).length > 0 ? JSON.stringify(value, null, 2) : '', 
    [value]
  )
  
  const [editingJsonText, setEditingJsonText] = useState(initialJsonText)
  const [jsonError, setJsonError] = useState<string | null>(null)

  // 当 value 变化时重置编辑状态
  useEffect(() => {
    setEditingJsonText(initialJsonText)
  }, [initialJsonText])

  // JSON 预览数据
  const previewData = useMemo(() => {
    const validation = validateJson(editingJsonText)
    if (validation.valid && validation.parsed) {
      return { success: true, data: validation.parsed }
    }
    return { success: false, data: {} }
  }, [editingJsonText])

  // 切换模式时同步数据
  const handleModeChange = useCallback((newMode: string) => {
    const targetMode = newMode as 'list' | 'json'
    if (targetMode === 'json' && mode === 'list') {
      // 从列表模式切换到 JSON 模式：将当前value转换为JSON
      setEditingJsonText(Object.keys(value).length > 0 ? JSON.stringify(value, null, 2) : '')
      setJsonError(null)
    }
    setMode(targetMode)
  }, [mode, value])

  // JSON 文本变化
  const handleJsonChange = useCallback((text: string) => {
    setEditingJsonText(text)
    const validation = validateJson(text)
    if (validation.valid && validation.parsed) {
      setJsonError(null)
      onChange(validation.parsed)
    } else {
      setJsonError(validation.error || 'JSON 格式错误')
    }
  }, [onChange])

  return (
    <div className={cn("h-full flex flex-col", className)}>
      <Tabs value={mode} onValueChange={handleModeChange} className="w-full flex-1 flex flex-col">
        <TabsList className="h-8 p-0.5 bg-muted/60 w-fit">
          <TabsTrigger 
            value="list" 
            className="h-7 px-3 text-xs data-[state=active]:bg-background data-[state=active]:shadow-sm"
          >
            可视化编辑
          </TabsTrigger>
          <TabsTrigger 
            value="json" 
            className="h-7 px-3 text-xs data-[state=active]:bg-background data-[state=active]:shadow-sm"
          >
            JSON 编辑
          </TabsTrigger>
        </TabsList>

        {/* 可视化编辑模式（嵌套键值对） */}
        <TabsContent 
          value="list" 
          className="mt-2 flex-1 flex flex-col overflow-hidden data-[state=inactive]:hidden data-[state=inactive]:h-0"
        >
          <NestedKeyValueEditor
            value={value}
            onChange={onChange}
            placeholder={placeholder}
          />
        </TabsContent>

        {/* JSON 编辑模式 - 左右分栏 */}
        <TabsContent 
          value="json" 
          className="mt-2 flex-1 flex flex-col overflow-hidden data-[state=inactive]:hidden data-[state=inactive]:h-0"
        >
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 flex-1 overflow-hidden">
            {/* 左侧：JSON 编辑器 */}
            <div className="flex flex-col gap-2 overflow-hidden">
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">编辑</span>
                {jsonError ? (
                  <div className="flex items-center gap-1 text-xs text-destructive">
                    <AlertCircle className="h-3 w-3" />
                    <span className="truncate max-w-[150px]">{jsonError}</span>
                  </div>
                ) : editingJsonText.trim() && (
                  <div className="flex items-center gap-1 text-xs text-green-600 dark:text-green-400">
                    <Check className="h-3 w-3" />
                    <span>有效</span>
                  </div>
                )}
              </div>
              <Textarea
                value={editingJsonText}
                onChange={(e) => handleJsonChange(e.target.value)}
                placeholder={'{\n  "key": "value"\n}'}
                className={cn(
                  "font-mono text-sm flex-1 resize-none",
                  jsonError && "border-destructive focus-visible:ring-destructive"
                )}
              />
              <p className="text-xs text-muted-foreground">
                支持任意 JSON 类型（包括嵌套对象和数组）
              </p>
            </div>

            {/* 右侧：预览 */}
            <div className="flex flex-col gap-2 overflow-hidden">
              <span className="text-xs text-muted-foreground">预览</span>
              <div className="flex-1 rounded-md border bg-muted/30 p-3 overflow-auto">
                {previewData.success && Object.keys(previewData.data).length > 0 ? (
                  <pre className="font-mono text-xs whitespace-pre-wrap break-words">
                    {JSON.stringify(previewData.data, null, 2)}
                  </pre>
                ) : previewData.success ? (
                  <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
                    暂无参数
                  </div>
                ) : (
                  <div className="flex items-center justify-center h-full text-sm text-destructive">
                    JSON 格式错误
                  </div>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                实时预览解析结果
              </p>
            </div>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  )
}
