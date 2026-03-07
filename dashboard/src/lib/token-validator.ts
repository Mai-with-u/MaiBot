/**
 * Token 验证规则和状态
 */

export interface TokenValidationRule {
  id: string
  label: string
  validate: (token: string) => boolean
  description: string
}

export interface TokenValidationResult {
  isValid: boolean
  rules: Array<{
    id: string
    label: string
    passed: boolean
    description: string
  }>
}

// Token 验证规则定义
export const TOKEN_VALIDATION_RULES: TokenValidationRule[] = [
  {
    id: 'minLength',
    label: '长度至少 10 位',
    description: 'Token 长度必须大于等于 10 个字符',
    validate: (token: string) => token.length >= 10,
  },
  {
    id: 'hasUppercase',
    label: '包含大写字母',
    description: '至少包含一个大写字母 (A-Z)',
    validate: (token: string) => /[A-Z]/.test(token),
  },
  {
    id: 'hasLowercase',
    label: '包含小写字母',
    description: '至少包含一个小写字母 (a-z)',
    validate: (token: string) => /[a-z]/.test(token),
  },
  {
    id: 'hasSpecialChar',
    label: '包含特殊符号',
    description: '至少包含一个特殊符号 (!@#$%^&*()_+-=[]{}|;:,.<>?/)',
    validate: (token: string) => /[!@#$%^&*()_+\-=[\]{}|;:,.<>?/]/.test(token),
  },
]

/**
 * 验证 Token 并返回详细结果
 */
export function validateToken(token: string): TokenValidationResult {
  const rules = TOKEN_VALIDATION_RULES.map((rule) => ({
    id: rule.id,
    label: rule.label,
    description: rule.description,
    passed: rule.validate(token),
  }))

  const isValid = rules.every((rule) => rule.passed)

  return {
    isValid,
    rules,
  }
}

/**
 * 获取验证失败的规则
 */
export function getFailedRules(token: string): string[] {
  const result = validateToken(token)
  return result.rules.filter((rule) => !rule.passed).map((rule) => rule.label)
}

/**
 * 检查 Token 是否完全有效
 */
export function isTokenValid(token: string): boolean {
  return validateToken(token).isValid
}
