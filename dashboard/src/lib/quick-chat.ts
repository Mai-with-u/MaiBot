/** 首页快速聊天：首页写下的消息暂存在这里，进入聊天页后自动发送给本地聊天室。 */
const QUICK_CHAT_DRAFT_KEY = 'maibot-quick-chat-draft'

export function stashQuickChatMessage(message: string): void {
  try {
    sessionStorage.setItem(QUICK_CHAT_DRAFT_KEY, message)
  } catch {
    // 存储不可用时直接进入聊天页，消息需要用户重新输入
  }
}

export function takeQuickChatMessage(): string | null {
  try {
    const message = sessionStorage.getItem(QUICK_CHAT_DRAFT_KEY)
    sessionStorage.removeItem(QUICK_CHAT_DRAFT_KEY)
    return message?.trim() ? message : null
  } catch {
    return null
  }
}
