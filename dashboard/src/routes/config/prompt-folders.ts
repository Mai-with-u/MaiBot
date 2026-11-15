import type { PromptFileInfo } from '@/lib/prompt-api'

export const PROMPT_FOLDER_LABELS: Record<string, string> = {
  agent: 'Agent',
  context: '上下文',
  desktop_pet: '桌宠',
  learning: '学习',
  media: '图片与表情包',
  memory: '记忆',
  planner: '规划器',
  reply: '回复',
  group: '群聊',
  private: '私聊',
  webui: 'WebUI',
}

export function promptFolder(file: PromptFileInfo): string {
  const parts = file.relative_path.split('/')
  if (parts[0] === 'main' || parts[0] === 'instructions') parts.shift()
  return parts.slice(0, -1).join('/')
}

export function inPromptFolder(file: PromptFileInfo, folder: string): boolean {
  const path = promptFolder(file)
  return !folder || path === folder || path.startsWith(`${folder}/`)
}

/** 从真实路径收集父目录，父目录统计包含所有后代文件。 */
export function buildPromptFolders(files: PromptFileInfo[]) {
  const folders = new Map<
    string,
    { path: string; label: string; depth: number; count: number; customized: boolean }
  >()
  for (const file of files) {
    const folder = promptFolder(file)
    if (!folder) continue
    const parts = folder.split('/')
    parts.forEach((part, depth) => {
      const path = parts.slice(0, depth + 1).join('/')
      const entry = folders.get(path) ?? {
        path,
        label: PROMPT_FOLDER_LABELS[part] ?? part,
        depth,
        count: 0,
        customized: false,
      }
      entry.count += 1
      entry.customized ||= file.customized
      folders.set(path, entry)
    })
  }
  return [...folders.values()].sort((a, b) => a.path.localeCompare(b.path, 'en'))
}
