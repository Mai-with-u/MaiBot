/**
 * 通用思考开关格式工具
 *
 * 由服务商模板的 thinking 元数据（ThinkingFormatConfig）驱动，
 * 负责在模型 extra_params 中读写与校验思考相关参数。
 * DeepSeek 因格式特殊（含 Responses 客户端），由 deepSeekExtraParams.ts 单独处理。
 */

export type ThinkingFormatConfigKind = 'thinking_type' | 'enable_thinking' | 'reasoning_effort'

// 思考开关格式元数据（未配置则该提供商不显示思考开关）
export interface ThinkingFormatConfig {
  // 思考参数的写入格式
  kind: ThinkingFormatConfigKind
  // thinking_type：启用时写入 thinking.type 的值，默认 'enabled'（豆包为 auto，MiniMax 为 adaptive）
  onValue?: string
  // thinking_type：是否支持关闭（关闭写入 thinking.type = 'disabled'）
  canDisable?: boolean
  // thinking_type：关闭能力的模型限制说明
  disableNote?: string
  // thinking_type：思考力度参数名（如 reasoning_effort），未配置则不显示力度选择
  effortParam?: string
  // 思考力度档位值域
  efforts?: string[]
  // 默认思考力度档位，未配置时取 efforts 第一项
  defaultEffort?: string
  // 思考预算参数名（如 thinking_budget），未配置则不显示预算输入
  budgetParam?: string
  // 思考预算输入框的占位说明
  budgetNote?: string
  // 附加提示
  note?: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

// 思考力度参数名：reasoning_effort 格式固定写入 reasoning_effort，其余按模板配置
function getEffortParam(config: ThinkingFormatConfig): string | null {
  if (config.kind === 'reasoning_effort') return 'reasoning_effort'
  return config.effortParam ?? null
}

// 解析力度档位：已有值在值域内则原样返回，否则回退到默认档位
function resolveEffort(value: unknown, config: ThinkingFormatConfig): string {
  const efforts = config.efforts ?? []
  if (typeof value === 'string' && (efforts.length === 0 || efforts.includes(value))) {
    return value
  }
  return config.defaultEffort ?? efforts[0] ?? ''
}

export function isThinkingEnabled(
  params: Record<string, unknown>,
  config: ThinkingFormatConfig
): boolean {
  if (config.kind === 'reasoning_effort') {
    // 推理模型思考常开，仅支持调整力度档位
    return true
  }
  if (config.kind === 'enable_thinking') {
    return params.enable_thinking === true
  }
  const thinking = isRecord(params.thinking) ? params.thinking : {}
  return thinking.type !== 'disabled'
}

export function getThinkingEffort(
  params: Record<string, unknown>,
  config: ThinkingFormatConfig
): string {
  const effortParam = getEffortParam(config)
  if (!effortParam) return ''
  return resolveEffort(params[effortParam], config)
}

export function setThinkingEffort(
  params: Record<string, unknown>,
  config: ThinkingFormatConfig,
  effort: string
): Record<string, unknown> {
  const effortParam = getEffortParam(config)
  if (!effortParam) return params

  const nextParams = { ...params, [effortParam]: effort }
  if (config.kind === 'thinking_type') {
    // 力度参数仅在思考开启时生效，写入时保持 thinking.type 为启用值
    const thinking = isRecord(nextParams.thinking) ? nextParams.thinking : {}
    nextParams.thinking = { ...thinking, type: config.onValue ?? 'enabled' }
  }
  return nextParams
}

export function getThinkingBudget(
  params: Record<string, unknown>,
  config: ThinkingFormatConfig
): number | null {
  if (!config.budgetParam) return null
  const value = params[config.budgetParam]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function setThinkingBudget(
  params: Record<string, unknown>,
  config: ThinkingFormatConfig,
  budget: number | null
): Record<string, unknown> {
  if (!config.budgetParam) return params

  const nextParams = { ...params }
  if (budget === null) {
    // 留空使用服务商默认
    delete nextParams[config.budgetParam]
  } else {
    nextParams[config.budgetParam] = budget
  }
  return nextParams
}

export function setThinkingEnabled(
  params: Record<string, unknown>,
  config: ThinkingFormatConfig,
  enabled: boolean
): Record<string, unknown> {
  if (config.kind === 'reasoning_effort') {
    // 推理模型思考常开，不支持关闭
    return params
  }

  if (config.kind === 'enable_thinking') {
    return { ...params, enable_thinking: enabled }
  }

  const thinking = isRecord(params.thinking) ? params.thinking : {}
  if (enabled) {
    const nextParams: Record<string, unknown> = {
      ...params,
      thinking: { ...thinking, type: config.onValue ?? 'enabled' },
    }
    // 模板配置了力度参数且尚未设置时，写入默认档位
    if (config.effortParam && nextParams[config.effortParam] === undefined) {
      const defaultEffort = config.defaultEffort ?? config.efforts?.[0]
      if (defaultEffort !== undefined) {
        nextParams[config.effortParam] = defaultEffort
      }
    }
    return nextParams
  }

  // 关闭思考：写入 disabled，并移除随之失效的力度参数
  const nextParams: Record<string, unknown> = {
    ...params,
    thinking: { ...thinking, type: 'disabled' },
  }
  if (config.effortParam) {
    delete nextParams[config.effortParam]
  }
  return nextParams
}

export function validateThinkingParams(
  params: Record<string, unknown>,
  config: ThinkingFormatConfig
): string | null {
  if (config.kind === 'enable_thinking') {
    if (
      params.enable_thinking !== undefined &&
      typeof params.enable_thinking !== 'boolean'
    ) {
      return 'enable_thinking 只能是 true 或 false'
    }
  }

  if (config.kind === 'thinking_type' && params.thinking !== undefined) {
    if (!isRecord(params.thinking)) return 'thinking 必须是对象'
    if (params.thinking.type !== undefined && typeof params.thinking.type !== 'string') {
      return 'thinking.type 必须是字符串'
    }
    if (config.canDisable !== true && params.thinking.type === 'disabled') {
      return '当前模型不支持关闭思考'
    }
  }

  const effortParam = getEffortParam(config)
  if (effortParam && params[effortParam] !== undefined) {
    const efforts = config.efforts ?? []
    const value = params[effortParam]
    if (typeof value !== 'string' || (efforts.length > 0 && !efforts.includes(value))) {
      const allowed = efforts.length > 0 ? efforts.join('、') : '字符串'
      return `${effortParam} 只能是 ${allowed}`
    }
  }

  if (config.budgetParam && params[config.budgetParam] !== undefined) {
    const budget = params[config.budgetParam]
    if (typeof budget !== 'number' || !Number.isFinite(budget) || budget < 0) {
      return `${config.budgetParam} 必须是非负数字`
    }
  }

  return null
}
