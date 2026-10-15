import type { Virtualizer } from '@tanstack/react-virtual'
import { useEffect } from 'react'

/** 卸载后顶替 targetWindow 的空窗口：排帧请求直接丢弃，scrollToIndex 的重试链到此为止 */
const detachedWindow = { requestAnimationFrame: () => 0 } as unknown as Window & typeof globalThis

/**
 * 规避 @tanstack/virtual-core 3.13 的卸载竞态。
 *
 * scrollToIndex 会跨帧校准位置；列表卸载时库把 targetWindow 置空，已排队的回调仍会调用
 * `targetWindow.requestAnimationFrame`，抛出 "Cannot read properties of null"。
 * 上游在重写 scrollToIndex 后已修复（3.19 起改为可取消的 rafId），升级后可删除本 hook。
 */
export function useVirtualizerUnmountGuard<
  TScrollElement extends Element | Window,
  TItemElement extends Element,
>(virtualizer: Virtualizer<TScrollElement, TItemElement>) {
  useEffect(
    () => () => {
      // 库在 layout effect 清理阶段置空 targetWindow，这里的清理晚于它执行。
      virtualizer.targetWindow = detachedWindow
    },
    [virtualizer]
  )
}
