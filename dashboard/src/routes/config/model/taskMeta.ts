/** 「分工」页里每项工作的简称、说明和可调的参数。 */
export interface TaskMeta {
  name: string
  desc: string
  /** 留空时的去向；没有则表示这项能力关闭 */
  fallback?: string
  required?: boolean
  hideTemperature?: boolean
  hideMaxTokens?: boolean
  hideStrategy?: boolean
  singleModel?: boolean
}

export const TASK_META: Record<string, TaskMeta> = {
  replyer: { name: '回复', desc: '麦麦说出口的每一句话', required: true },
  planner: { name: '规划', desc: '决定下一步做什么，需要会用工具', required: true },
  utils: { name: '工具', desc: '概括、整理等小任务', required: true },
  vlm: { name: '识图', desc: '看懂聊天里的图片', required: true },
  fast_model: { name: '快速', desc: '断句、挑表达等要快的小事', fallback: '用工具模型' },
  memory: { name: '记忆', desc: '总结并写回长期记忆', fallback: '用工具模型' },
  learner: { name: '学习', desc: '学习表达方式和黑话', fallback: '用工具模型' },
  emoji: { name: '表情', desc: '挑选要发的表情包', fallback: '用规划模型' },
  voice: { name: '语音', desc: '把语音消息转成文字', hideTemperature: true, hideMaxTokens: true },
  web_search: { name: '联网搜索', desc: '搜索网页、读取链接', singleModel: true },
  embedding: {
    name: '文本嵌入', desc: '给记忆和表达建立索引',
    hideTemperature: true, hideMaxTokens: true, hideStrategy: true, singleModel: true,
  },
  image_embedding: {
    name: '图片嵌入', desc: '给图片建立索引',
    hideTemperature: true, hideMaxTokens: true, hideStrategy: true, singleModel: true,
  },
}

/** 工作的简称，例如 replyer → 回复；没有登记的沿用原名。 */
export function taskShortName(task: string) {
  return TASK_META[task]?.name ?? task
}
