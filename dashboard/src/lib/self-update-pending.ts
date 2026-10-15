/**
 * 自更新会重启后端并跳转回登录页；发起前记下目标提交，
 * 页面重新加载后据此提示这一次更新的结果。
 */
const STORAGE_KEY = 'maibot-self-update-pending'

export function markSelfUpdatePending(commit: string): void {
  sessionStorage.setItem(STORAGE_KEY, commit)
}

/** 取出并清除待提示的目标提交 */
export function takeSelfUpdatePending(): string | null {
  const commit = sessionStorage.getItem(STORAGE_KEY)
  if (commit !== null) {
    sessionStorage.removeItem(STORAGE_KEY)
  }
  return commit
}
