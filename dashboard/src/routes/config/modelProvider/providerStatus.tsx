import type { TestConnectionResult } from '@/lib/config-api'

export function getProviderTestStatus(
  result: TestConnectionResult | undefined,
  isTesting: boolean
) {
  if (isTesting) {
    return {
      description: '正在测试厂商连接',
      className: 'border-amber-500 animate-pulse',
    }
  }

  if (!result) {
    return {
      description: '未测试：尚未执行厂商连接测试',
      className: 'border-transparent',
    }
  }

  if (result.network_ok) {
    if (result.api_key_valid === true) {
      return {
        description: `连接正常：网络可访问，API Key 有效${
          result.latency_ms != null ? `，延迟 ${result.latency_ms}ms` : ''
        }`,
        className: 'border-green-500',
      }
    }

    if (result.api_key_valid === false) {
      return {
        description: result.error || '连接异常：网络可访问，但 API Key 无效或已过期',
        className: 'border-red-500',
      }
    }

    return {
      description: `可访问：网络连接正常，但未确认 API Key 是否有效${
        result.latency_ms != null ? `，延迟 ${result.latency_ms}ms` : ''
      }`,
      className: 'border-blue-500',
    }
  }

  return {
    description: result.error || '连接失败：无法访问该厂商',
    className: 'border-red-500',
  }
}
