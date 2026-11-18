/**
 * useExpressionImportExport —— 表达方式「清除」领域 hook（页面逻辑下沉切片）。
 *
 * 导入已统一到学习页的「导入学习内容」（learning/LearningImportDialog）。
 * - 清除需要先在左侧选择一个具体聊天（currentChat），未选中时弹 toast 拦截；
 * - 清除：清空当前聊天全部表达方式，成功后调用 onChanged() 刷新；
 * - 写失败弹全局 toast（与原页面一致）。
 */
import { useCallback } from 'react'

import { useToast } from '@/hooks/use-toast'

import { clearExpressions } from '@/lib/expression-api'

import type { ChatInfo } from '@/types/expression'

export interface UseExpressionImportExportOptions {
  /** 当前选中的具体聊天（按聊天浏览且已选中时非空） */
  currentChat: ChatInfo | null
  /** 写成功后回调（页面接 list.invalidate()），刷新列表 + 统计 */
  onChanged: () => void
  /** 清除成功后清空选中集 */
  onClearSelection: () => void
  /** 关闭清除确认对话框 */
  onCloseClearConfirm: () => void
}

export interface UseExpressionImportExportResult {
  /** 清除当前聊天全部表达方式 */
  clearCurrentChat: () => Promise<void>
}

export function useExpressionImportExport({
  currentChat,
  onChanged,
  onClearSelection,
  onCloseClearConfirm,
}: UseExpressionImportExportOptions): UseExpressionImportExportResult {
  const { toast } = useToast()

  // 校验是否已选择具体聊天，未选中时拦截并提示
  const getCurrentChatId = useCallback((): string | null => {
    if (!currentChat) {
      toast({
        title: '请选择聊天',
        description: '清除表达方式需要先在左侧选择一个具体聊天',
        variant: 'destructive',
      })
      return null
    }
    return currentChat.chat_id
  }, [currentChat, toast])

  const clearCurrentChat = useCallback(async () => {
    const chatId = getCurrentChatId()
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
  }, [getCurrentChatId, onChanged, onClearSelection, onCloseClearConfirm, toast])

  return {
    clearCurrentChat,
  }
}
