import type { ModelInfo } from './types'

/** 移除 TOML 不支持的 null；缓存价格留空按输入价解析，0 仍表示免费。 */
export function serializeModel(model: ModelInfo): ModelInfo {
  const result: ModelInfo = {
    model_identifier: model.model_identifier,
    model_type: model.model_type,
    name: model.name,
    api_provider: model.api_provider,
    price_in: model.price_in ?? 0,
    price_out: model.price_out ?? 0,
    cache_price_in: model.cache_price_in ?? model.price_in ?? 0,
    price_periods: model.price_periods?.map((period) => ({ ...period })),
    send_temperature: model.send_temperature ?? true,
    visual: model.visual ?? false,
    force_stream_mode: model.force_stream_mode ?? false,
    extra_params: model.extra_params ?? {},
  }
  // 可选生成参数只在有值时写入。
  if (model.temperature != null) result.temperature = model.temperature
  if (model.max_tokens != null) result.max_tokens = model.max_tokens
  return result
}
