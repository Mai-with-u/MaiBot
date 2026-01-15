/**
 * 问卷调查类型定义
 */

// 问题类型
export type QuestionType = 
  | 'single'      // 单选
  | 'multiple'    // 多选
  | 'text'        // 文本输入
  | 'textarea'    // 多行文本
  | 'rating'      // 评分（1-5星）
  | 'scale'       // 量表（如 1-10）
  | 'dropdown'    // 下拉选择

// 单个选项
export interface QuestionOption {
  id: string           // 选项ID
  label: string        // 选项文本
  value: string        // 选项值
}

// 问题定义
export interface SurveyQuestion {
  id: string                    // 问题ID
  type: QuestionType            // 问题类型
  title: string                 // 问题标题
  description?: string          // 问题描述/说明
  required?: boolean            // 是否必填
  readOnly?: boolean            // 是否只读（用于自动填充的字段）
  options?: QuestionOption[]    // 选项列表（用于单选、多选、下拉）
  placeholder?: string          // 占位符（用于文本输入）
  minLength?: number            // 最小长度（用于文本）
  maxLength?: number            // 最大长度（用于文本）
  min?: number                  // 最小值（用于量表）
  max?: number                  // 最大值（用于量表）
  step?: number                 // 步长（用于量表）
  minLabel?: string             // 最小值标签（用于量表）
  maxLabel?: string             // 最大值标签（用于量表）
  maxSelections?: number        // 最大选择数（用于多选）
}

// 问卷配置
export interface SurveyConfig {
  id: string                    // 问卷ID
  version: string               // 问卷版本
  title: string                 // 问卷标题
  description?: string          // 问卷描述
  questions: SurveyQuestion[]   // 问题列表
  settings?: {
    allowAnonymous?: boolean    // 是否允许匿名提交
    allowMultiple?: boolean     // 是否允许多次提交
    startTime?: string          // 开始时间
    endTime?: string            // 结束时间
    thankYouMessage?: string    // 提交成功消息
  }
}

// 单个答案
export interface QuestionAnswer {
  questionId: string            // 问题ID
  value: string | string[] | number  // 答案值
}

// 问卷提交数据
export interface SurveySubmission {
  surveyId: string              // 问卷ID
  surveyVersion: string         // 问卷版本
  userId?: string               // 用户ID（可选）
  answers: QuestionAnswer[]     // 答案列表
  submittedAt: string           // 提交时间
  metadata?: {
    userAgent?: string          // 用户代理
    language?: string           // 语言
  }
}

// 存储的提交记录
export interface StoredSubmission extends SurveySubmission {
  id: string                    // 提交记录ID
  ip?: string                   // 提交者IP（脱敏）
}

// 问卷统计数据
export interface SurveyStats {
  surveyId: string              // 问卷ID
  totalSubmissions: number      // 总提交数
  uniqueUsers: number           // 唯一用户数
  lastSubmissionAt?: string     // 最后提交时间
  questionStats: {
    [questionId: string]: {
      answered: number          // 回答数
      // 对于选择题，记录各选项的选择次数
      optionCounts?: { [optionValue: string]: number }
      // 对于评分/量表题，记录平均值
      average?: number
      // 对于文本题，记录样本答案（可选）
      sampleAnswers?: string[]
    }
  }
}

// API 响应
export interface SurveySubmitResponse {
  success: boolean
  submissionId?: string
  message?: string
  error?: string
}

export interface SurveyStatsResponse {
  success: boolean
  stats?: SurveyStats
  error?: string
}

export interface UserSubmissionsResponse {
  success: boolean
  submissions?: StoredSubmission[]
  error?: string
}
