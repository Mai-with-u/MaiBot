/**
 * useExpressionImportExport —— 表达方式「导入 / 导出 / 清除」领域 hook（页面逻辑下沉切片）。
 *
 * 收编按聊天的导入导出清除写逻辑：
 * - 导入导出清除都需要先在左侧选择一个具体聊天（currentChat），未选中时弹 toast 拦截；
 * - 导出：拉取数据后在浏览器侧下载 JSON（全部 / 仅所选）；
 * - 导入：解析上传的 JSON、规范化条目后提交，成功后调用 onChanged() 刷新；
 * - 清除：清空当前聊天全部表达方式，成功后调用 onChanged() 刷新；
 * - 写失败弹全局 toast（与原页面一致）。
 */
import { useCallback } from 'react'
import type { ChangeEvent } from 'react'

import { useToast } from '@/hooks/use-toast'

import {
  clearExpressions,
  exportExpressions,
  importExpressions,
} from '@/lib/expression-api'

import type { ChatInfo, ExpressionExportItem } from '@/types/expression'

export interface UseExpressionImportExportOptions {
  /** 当前选中的具体聊天（按聊天浏览且已选中时非空） */
  currentChat: ChatInfo | null
  /** 当前选中的表达方式 id 集合（用于「导出所选」） */
  selectedIds: Set<number>
  /** 写成功后回调（页面接 list.invalidate()），刷新列表 + 统计 */
  onChanged: () => void
  /** 导入 / 清除成功后清空选中集 */
  onClearSelection: () => void
  /** 关闭清除确认对话框 */
  onCloseClearConfirm: () => void
}

export interface UseExpressionImportExportResult {
  /** 导出表达方式（onlySelected=true 仅导出选中项） */
  exportExpressionsToFile: (onlySelected: boolean) => Promise<void>
  /** 导入文件变更处理（绑定到 <input type="file"> 的 onChange） */
  handleImportFileChange: (event: ChangeEvent<HTMLInputElement>) => Promise<void>
  /** 清除当前聊天全部表达方式 */
  clearCurrentChat: () => Promise<void>
}

export function useExpressionImportExport({
  currentChat,
  selectedIds,
  onChanged,
  onClearSelection,
  onCloseClearConfirm,
}: UseExpressionImportExportOptions): UseExpressionImportExportResult {
  const { toast } = useToast()

  // 校验是否已选择具体聊天，未选中时拦截并提示
  const getImportExportChatId = useCallback((): string | null => {
    if (!currentChat) {
      toast({
        title: '请选择聊天',
        description: '表达方式导入导出需要先在左侧选择一个具体聊天',
        variant: 'destructive',
      })
      return null
    }
    return currentChat.chat_id
  }, [currentChat, toast])

  const downloadJson = useCallback((filename: string, data: unknown) => {
    const blob = new Blob([JSON.stringify(data, null, 2)], {
      type: 'application/json;charset=utf-8',
    })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  }, [])

  const sanitizeFilename = useCallback((name: string) => {
    return name.replace(/[\\/:*?"<>|]/g, '_').slice(0, 60) || 'chat'
  }, [])

  const exportExpressionsToFile = useCallback(
    async (onlySelected: boolean) => {
      const chatId = getImportExportChatId()
      if (!chatId || !currentChat) return
      if (onlySelected && selectedIds.size === 0) {
        toast({
          title: '没有选中项目',
          description: '请先选择要导出的表达方式',
          variant: 'destructive',
        })
        return
      }

      let result
      try {
        result = await exportExpressions({
          chat_id: chatId,
          ids: onlySelected ? Array.from(selectedIds) : undefined,
        })
      } catch (error) {
        toast({
          title: '导出失败',
          description: error instanceof Error ? error.message : '导出表达方式失败',
          variant: 'destructive',
        })
        return
      }

      const filename = `expressions-${sanitizeFilename(currentChat.chat_name)}-${onlySelected ? 'selected' : 'all'}.json`
      downloadJson(filename, result)
      toast({
        title: '导出成功',
        description: `已导出 ${result.count} 个表达方式`,
      })
    },
    [currentChat, downloadJson, getImportExportChatId, sanitizeFilename, selectedIds, toast]
  )

  // 规范化导入载荷：兼容裸数组与 { expressions: [...] } 两种形态
  const normalizeImportItems = useCallback((payload: unknown): ExpressionExportItem[] => {
    if (Array.isArray(payload)) {
      return payload as ExpressionExportItem[]
    }
    if (
      payload &&
      typeof payload === 'object' &&
      Array.isArray((payload as { expressions?: unknown }).expressions)
    ) {
      return (payload as { expressions: ExpressionExportItem[] }).expressions
    }
    return []
  }, [])

  const handleImportFileChange = useCallback(
    async (event: ChangeEvent<HTMLInputElement>) => {
      const chatId = getImportExportChatId()
      const file = event.target.files?.[0]
      event.target.value = ''
      if (!chatId || !file) return

      try {
        const payload = JSON.parse(await file.text()) as unknown
        const expressionsToImport = normalizeImportItems(payload)
        if (expressionsToImport.length === 0) {
          toast({
            title: '导入失败',
            description: 'JSON 中没有可导入的表达方式',
            variant: 'destructive',
          })
          return
        }

        const result = await importExpressions({
          chat_id: chatId,
          expressions: expressionsToImport,
        })
        toast({
          title: '导入成功',
          description: `成功 ${result.imported_count} 个，跳过 ${result.skipped_count} 个，失败 ${result.failed_count} 个`,
        })
        onChanged()
      } catch (error) {
        toast({
          title: '导入失败',
          description: error instanceof Error ? error.message : '无法解析 JSON 文件',
          variant: 'destructive',
        })
      }
    },
    [getImportExportChatId, normalizeImportItems, onChanged, toast]
  )

  const clearCurrentChat = useCallback(async () => {
    const chatId = getImportExportChatId()
    if (!chatId) return

    let result
    try {
      result = await clearExpressions({ chat_id: chatId })
    } catch (error) {
      toast({
        title: '清除失败',
        description: error instanceof Error ? error.message : '清除表达方式失败',
        variant: 'destructive',
      })
      return
    }

    toast({
      title: '清除成功',
      description: result.message,
    })
    onClearSelection()
    onCloseClearConfirm()
    onChanged()
  }, [getImportExportChatId, onChanged, onClearSelection, onCloseClearConfirm, toast])

  return {
    exportExpressionsToFile,
    handleImportFileChange,
    clearCurrentChat,
  }
}
