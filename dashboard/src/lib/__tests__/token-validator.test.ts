import { describe, expect, it } from 'vitest'

import {
  TOKEN_VALIDATION_RULES,
  getFailedRules,
  isTokenValid,
  validateToken,
} from '../token-validator'

/** 一个满足全部规则的合法 Token：长度 10、含大写、小写与特殊符号 */
const VALID_TOKEN = 'Abcdef!234'

/** 按 id 取出单条规则，规则缺失时直接抛错让测试失败 */
function ruleById(id: string) {
  const rule = TOKEN_VALIDATION_RULES.find((item) => item.id === id)
  if (!rule) {
    throw new Error(`未找到规则: ${id}`)
  }
  return rule
}

describe('TOKEN_VALIDATION_RULES', () => {
  it('按顺序定义了 4 条规则', () => {
    expect(TOKEN_VALIDATION_RULES.map((rule) => rule.id)).toEqual([
      'minLength',
      'hasUppercase',
      'hasLowercase',
      'hasSpecialChar',
    ])
  })

  it('minLength 规则以 10 个字符为边界', () => {
    const rule = ruleById('minLength')
    expect(rule.validate('123456789')).toBe(false)
    expect(rule.validate('1234567890')).toBe(true)
    expect(rule.validate('12345678901')).toBe(true)
  })

  it('hasUppercase 规则要求至少一个大写字母', () => {
    const rule = ruleById('hasUppercase')
    expect(rule.validate('abc123!')).toBe(false)
    expect(rule.validate('aBc')).toBe(true)
  })

  it('hasLowercase 规则要求至少一个小写字母', () => {
    const rule = ruleById('hasLowercase')
    expect(rule.validate('ABC123!')).toBe(false)
    expect(rule.validate('AbC')).toBe(true)
  })

  it('hasSpecialChar 规则识别定义集合内的特殊符号', () => {
    const rule = ruleById('hasSpecialChar')
    // 集合内的代表性符号逐个通过
    for (const char of ['!', '@', '#', '-', '[', ']', '{', '}', '|', ';', ',', '?', '/']) {
      expect(rule.validate(`abc${char}`)).toBe(true)
    }
    // 纯字母数字不通过
    expect(rule.validate('abc123')).toBe(false)
    // 集合外的符号（如 ~ 与空格）不通过
    expect(rule.validate('abc~')).toBe(false)
    expect(rule.validate('abc def')).toBe(false)
  })
})

describe('validateToken', () => {
  it('合法 Token 全部规则通过且 isValid 为 true', () => {
    const result = validateToken(VALID_TOKEN)

    expect(result.isValid).toBe(true)
    expect(result.rules).toHaveLength(4)
    expect(result.rules.every((rule) => rule.passed)).toBe(true)
  })

  it('结果中的每条规则携带 id、label、description', () => {
    const result = validateToken(VALID_TOKEN)

    for (const [index, rule] of result.rules.entries()) {
      expect(rule.id).toBe(TOKEN_VALIDATION_RULES[index].id)
      expect(rule.label).toBe(TOKEN_VALIDATION_RULES[index].label)
      expect(rule.description).toBe(TOKEN_VALIDATION_RULES[index].description)
    }
  })

  it('部分规则未通过时逐条标记 passed 且 isValid 为 false', () => {
    // 'abc'：长度不足、无大写、无特殊符号，仅小写通过
    const result = validateToken('abc')

    expect(result.isValid).toBe(false)
    const passedById = Object.fromEntries(result.rules.map((rule) => [rule.id, rule.passed]))
    expect(passedById).toEqual({
      minLength: false,
      hasUppercase: false,
      hasLowercase: true,
      hasSpecialChar: false,
    })
  })

  it('空字符串所有规则均不通过', () => {
    const result = validateToken('')

    expect(result.isValid).toBe(false)
    expect(result.rules.every((rule) => !rule.passed)).toBe(true)
  })
})

describe('getFailedRules', () => {
  it('返回未通过规则的 label 列表', () => {
    // 'Abcdefghij'：长度够、大小写齐全，仅缺特殊符号
    expect(getFailedRules('Abcdefghij')).toEqual(['包含特殊符号'])
  })

  it('多条规则未通过时按定义顺序返回全部 label', () => {
    expect(getFailedRules('abc')).toEqual(['长度至少 10 位', '包含大写字母', '包含特殊符号'])
  })

  it('合法 Token 返回空数组', () => {
    expect(getFailedRules(VALID_TOKEN)).toEqual([])
  })
})

describe('isTokenValid', () => {
  it('合法 Token 返回 true', () => {
    expect(isTokenValid(VALID_TOKEN)).toBe(true)
  })

  it('任意一条规则未通过即返回 false', () => {
    // 仅长度不足
    expect(isTokenValid('Ab!1')).toBe(false)
    // 仅缺大写
    expect(isTokenValid('abcdef!234')).toBe(false)
    // 仅缺小写
    expect(isTokenValid('ABCDEF!234')).toBe(false)
    // 仅缺特殊符号
    expect(isTokenValid('Abcdefg234')).toBe(false)
  })
})
