/**
 * CSS 安全过滤器 - 用于过滤用户自定义 CSS 中的危险内容
 * 防范外部资源加载和 XSS 注入
 */

interface SanitizeResult {
  css: string
  warnings: string[]
}

/**
 * 过滤规则：基于正则表达式的危险模式检测
 * 与匹配的危险模式相关的警告消息
 */
interface FilterRule {
  pattern: RegExp
  message: string
}

/**
 * 定义所有过滤规则
 */
const filterRules: FilterRule[] = [
  {
    pattern: /@import\s+(?:url\()?['"]?(?:https?:|\/\/)?[^)'"]+['"]?\)?[;]?/gi,
    message: '移除 @import 语句（禁止加载外部资源）',
  },
  {
    pattern: /url\s*\(\s*(?:https?:|\/\/|data:|javascript:)[^)]*\)/gi,
    message: '移除 url() 调用（禁止外部请求）',
  },
  {
    pattern: /javascript:/gi,
    message: '移除 javascript: 协议（XSS 防护）',
  },
  {
    pattern: /expression\s*\(\s*[^)]*\)/gi,
    message: '移除 expression() 函数（IE 遗留 XSS 向量）',
  },
  {
    pattern: /-moz-binding\s*:\s*[^;]+/gi,
    message: '移除 -moz-binding 属性（Firefox XSS 向量）',
  },
  {
    pattern: /behavior\s*:\s*[^;]+/gi,
    message: '移除 behavior: 属性（IE HTC）',
  },
]

/**
 * 将原始 CSS 按行分割并跟踪行号
 */
function splitCSSByLines(css: string): string[] {
  return css.split(/\r?\n/)
}

/**
 * 在 CSS 中查找模式匹配的行号
 */
function findMatchingLineNumbers(css: string, pattern: RegExp): number[] {
  const lines = splitCSSByLines(css)
  const matchingLines: number[] = []

  lines.forEach((line, index) => {
    if (pattern.test(line)) {
      matchingLines.push(index + 1) // 行号从 1 开始
    }
  })

  return matchingLines
}

/**
 * 过滤 CSS 中的危险内容
 * @param rawCSS 原始 CSS 字符串
 * @returns 包含过滤后的 CSS 和警告列表的对象
 */
export function sanitizeCSS(rawCSS: string): SanitizeResult {
  let sanitizedCSS = rawCSS
  const warnings: string[] = []

  // 应用所有过滤规则
  filterRules.forEach((rule) => {
    const lineNumbers = findMatchingLineNumbers(sanitizedCSS, rule.pattern)

    // 对每个匹配的行生成警告
    lineNumbers.forEach((lineNum) => {
      warnings.push(`Line ${lineNum}: ${rule.message}`)
    })

    // 从 CSS 中移除匹配内容
    sanitizedCSS = sanitizedCSS.replace(rule.pattern, '')
  })

  // 清理多余的空白行
  sanitizedCSS = sanitizedCSS.replace(/\n\s*\n/g, '\n').trim()

  return {
    css: sanitizedCSS,
    warnings,
  }
}

/**
 * 快速检查 CSS 是否包含危险模式
 * @param css CSS 字符串
 * @returns 如果包含危险模式返回 true，否则返回 false
 */
export function isCSSSafe(css: string): boolean {
  return !filterRules.some((rule) => rule.pattern.test(css))
}
