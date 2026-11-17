import { getHostRuntime } from '@/host/state'
import { invokePluginWebUI, type APIArguments } from '@/lib/plugin-webui'

let queue: Promise<unknown> = Promise.resolve()

/** QQ 页面与轮询共享队列，避免分别限流后仍挤满插件请求名额。 */
export function invokeQQWebUI(
  kind: 'queries' | 'actions',
  name: string,
  args: APIArguments = {},
  confirmed = false
): Promise<unknown> {
  const botId = getHostRuntime().selected_bot_id
  const request = queue.then(() => {
    if (getHostRuntime().selected_bot_id !== botId) throw new Error('实例已切换，请重新操作')
    return invokePluginWebUI('Mai-with-u.qq-adapter', 'group-mappings', kind, name, args, confirmed)
  })
  queue = request.catch(() => undefined)
  return request
}
