/**
 * 问卷静态配置结构合法性测试
 * 覆盖 webui-feedback 与 maibot-feedback 两份问卷定义：
 * 基础字段、问题/选项唯一性、选择类问题选项完整性、提交设置与只读版本问题约定
 */
import { describe, expect, it } from 'vitest'

import { maibotFeedbackSurvey } from '../maibot-feedback'
import { webuiFeedbackSurvey } from '../webui-feedback'
import type { QuestionType, SurveyConfig } from '@/types/survey'

const VALID_TYPES: QuestionType[] = ['single', 'multiple', 'text', 'textarea', 'rating', 'scale', 'dropdown']
const CHOICE_TYPES: QuestionType[] = ['single', 'multiple', 'dropdown']

const cases: Array<[string, SurveyConfig]> = [
  ['webuiFeedbackSurvey', webuiFeedbackSurvey],
  ['maibotFeedbackSurvey', maibotFeedbackSurvey],
]

describe.each(cases)('%s 配置结构', (_name, config) => {
  it('基础字段完整且版本号符合语义化格式', () => {
    expect(config.id).toBeTruthy()
    expect(config.version).toMatch(/^\d+\.\d+\.\d+$/)
    expect(config.title).toBeTruthy()
    expect(config.description).toBeTruthy()
    expect(config.questions.length).toBeGreaterThan(0)
  })

  it('问题 ID 唯一、类型合法且标题非空', () => {
    const ids = config.questions.map((q) => q.id)
    expect(new Set(ids).size).toBe(ids.length)
    for (const question of config.questions) {
      expect(VALID_TYPES).toContain(question.type)
      expect(question.title).toBeTruthy()
    }
  })

  it('选择类问题携带非空选项且选项 ID / 值唯一、标签非空', () => {
    for (const question of config.questions) {
      if (CHOICE_TYPES.includes(question.type)) {
        expect(question.options?.length ?? 0).toBeGreaterThan(0)
        const optionIds = question.options!.map((o) => o.id)
        const optionValues = question.options!.map((o) => o.value)
        expect(new Set(optionIds).size).toBe(optionIds.length)
        expect(new Set(optionValues).size).toBe(optionValues.length)
        for (const option of question.options!) {
          expect(option.label).toBeTruthy()
        }
      } else {
        // 非选择类问题不应携带 options
        expect(question.options).toBeUndefined()
      }
    }
  })

  it('文本长度限制为正数且区间合法', () => {
    for (const question of config.questions) {
      if (question.maxLength !== undefined) {
        expect(question.maxLength).toBeGreaterThan(0)
      }
      if (question.minLength !== undefined) {
        expect(question.minLength).toBeGreaterThan(0)
        expect(question.minLength).toBeLessThanOrEqual(question.maxLength ?? Number.POSITIVE_INFINITY)
      }
    }
  })

  it('设置为单次提交且带感谢语', () => {
    expect(config.settings?.allowMultiple).toBe(false)
    expect(config.settings?.thankYouMessage).toBeTruthy()
  })

  it('首题为系统自动填写的只读必填版本问题', () => {
    const versionQuestion = config.questions[0]
    expect(versionQuestion.readOnly).toBe(true)
    expect(versionQuestion.required).toBe(true)
    expect(versionQuestion.type).toBe('text')
    // 只读版本问题应有占位符提示自动检测
    expect(versionQuestion.placeholder).toBeTruthy()
  })
})

describe('问卷标识约定', () => {
  it('webui 问卷 ID 与版本问题符合约定', () => {
    expect(webuiFeedbackSurvey.id).toBe('webui-feedback-v1')
    expect(webuiFeedbackSurvey.questions[0].id).toBe('webui_version')
  })

  it('maibot 问卷 ID 与版本问题符合约定', () => {
    expect(maibotFeedbackSurvey.id).toBe('maibot-feedback-v1')
    expect(maibotFeedbackSurvey.questions[0].id).toBe('maibot_version')
  })

  it('两份问卷 ID 互不冲突', () => {
    expect(webuiFeedbackSurvey.id).not.toBe(maibotFeedbackSurvey.id)
  })
})
