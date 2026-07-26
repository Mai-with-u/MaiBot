/**
 * Vitest 全局测试环境初始化。
 *
 * jsdom 只实现了 DOM 规范的一部分，浏览器里习以为常的 API（观察者、Canvas、
 * 剪贴板、对象 URL 等）在 jsdom 里要么缺失、要么调用即抛 "Not implemented"。
 * 这里统一补齐，避免每个组件测试各自重复打桩。
 *
 * 约定：所有桩一律用普通函数实现，不使用 vi.fn()。
 * 因为 vitest.config.ts 开启了 restoreMocks / mockReset，
 * 用 vi.fn() 写的全局桩会在每个用例前被重置成空实现而失效；
 * 用普通函数则始终有效，需要断言调用的测试自行 vi.spyOn 即可。
 */
import '@testing-library/jest-dom/vitest'

// ResizeObserver：Radix、recharts、虚拟列表等组件在挂载时就会实例化它
globalThis.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

// IntersectionObserver：懒加载、无限滚动、曝光埋点类组件依赖它
globalThis.IntersectionObserver = class IntersectionObserver {
  readonly root: Element | Document | null = null
  readonly rootMargin: string = '0px'
  readonly thresholds: ReadonlyArray<number> = [0]
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords(): IntersectionObserverEntry[] {
    return []
  }
} as unknown as typeof IntersectionObserver

if (typeof window !== 'undefined') {
  // matchMedia：主题切换、响应式断点 Hook 都会读取它，jsdom 未实现
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => {},
    }),
  })

  // scrollTo：jsdom 的实现直接抛 "Not implemented"，页面级组件滚动复位会踩到
  window.scrollTo = () => {}
}

if (typeof Element !== 'undefined') {
  // scrollIntoView：聊天消息列表、命令面板选中项定位都会调用，jsdom 未实现
  Element.prototype.scrollIntoView = () => {}
}

if (typeof HTMLCanvasElement !== 'undefined') {
  // getContext：jsdom 未内置 Canvas 实现，直接调用会抛错。
  // recharts / 图表组件在测量文字宽度时会取 2d 上下文，这里给一个足够用的空实现。
  HTMLCanvasElement.prototype.getContext = function getContext() {
    return {
      arc: () => {},
      beginPath: () => {},
      clearRect: () => {},
      clip: () => {},
      closePath: () => {},
      createLinearGradient: () => ({ addColorStop: () => {} }),
      createRadialGradient: () => ({ addColorStop: () => {} }),
      drawImage: () => {},
      fill: () => {},
      fillRect: () => {},
      fillText: () => {},
      getImageData: () => ({ data: new Uint8ClampedArray(4) }),
      lineTo: () => {},
      measureText: (text: string) => ({ width: text.length * 8 }),
      moveTo: () => {},
      putImageData: () => {},
      rect: () => {},
      restore: () => {},
      rotate: () => {},
      save: () => {},
      scale: () => {},
      setTransform: () => {},
      stroke: () => {},
      strokeRect: () => {},
      strokeText: () => {},
      transform: () => {},
      translate: () => {},
    }
  } as unknown as HTMLCanvasElement['getContext']
}

if (typeof URL !== 'undefined' && typeof URL.createObjectURL !== 'function') {
  // createObjectURL / revokeObjectURL：文件下载、图片预览、Blob 导出链路需要，jsdom 未实现
  let objectUrlSeq = 0
  URL.createObjectURL = () => `blob:maibot-test/${(objectUrlSeq += 1)}`
  URL.revokeObjectURL = () => {}
}

if (typeof globalThis.crypto === 'undefined') {
  // 极端环境下连 crypto 都没有时兜个最小对象，保证下面的 randomUUID 能挂上去
  Object.defineProperty(globalThis, 'crypto', { value: {}, configurable: true, writable: true })
}

if (typeof globalThis.crypto.randomUUID !== 'function') {
  // randomUUID：生成临时 ID 的业务代码依赖它，部分 jsdom / Node 组合下缺失。
  // 测试环境不需要密码学随机性，用自增计数拼一个格式合法的 UUID 即可。
  let uuidSeq = 0
  Object.defineProperty(globalThis.crypto, 'randomUUID', {
    configurable: true,
    writable: true,
    value: () => {
      const seq = (uuidSeq += 1).toString(16).padStart(12, '0')
      return `00000000-0000-4000-8000-${seq}` as `${string}-${string}-${string}-${string}-${string}`
    },
  })
}

if (typeof navigator !== 'undefined' && !navigator.clipboard) {
  // clipboard：jsdom 未实现，"复制到剪贴板" 类按钮点击后会崩。
  // 需要断言复制内容的测试可以 vi.spyOn(navigator.clipboard, 'writeText')。
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    writable: true,
    value: {
      writeText: () => Promise.resolve(),
      readText: () => Promise.resolve(''),
    },
  })
}
