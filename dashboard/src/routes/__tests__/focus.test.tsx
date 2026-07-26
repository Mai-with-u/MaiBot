/**
 * 专注陪伴页（focus.tsx）特征化测试。
 *
 * 桩策略：
 * - three / GLTFLoader / @pixiv/three-vrm 属于重量级 3D 依赖，全部用轻量桩替换，
 *   只实现 focus.tsx 实际触碰到的构造器与方法；
 * - chat-ws-client 一律 mock，不建立真实 WebSocket 连接；
 * - requestAnimationFrame 桩为空实现，阻断渲染循环，避免与假计时器互相纠缠；
 * - jsdom 未实现 requestFullscreen，这里在 HTMLElement.prototype 上补桩。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { VRMUtils } from '@pixiv/three-vrm'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { FocusCompanionPage } from '../focus'

import type { Mock } from 'vitest'
import type { ReactNode } from 'react'

// 陪伴会话 WS 客户端桩：页面只依赖这五个方法
const chatWsMocks = vi.hoisted(() => ({
  closeSession: vi.fn(),
  onConnectionChange: vi.fn(),
  onSessionMessage: vi.fn(),
  openSession: vi.fn(),
  sendMessage: vi.fn(),
}))

// 聊天流列表 API 桩：chat 指标的数据来源
const chatApiMocks = vi.hoisted(() => ({
  getChatStreams: vi.fn(),
}))

// WebUI 设置桩：控制专注陪伴入口开关
const settingsMocks = vi.hoisted(() => ({
  getSetting: vi.fn(),
}))

// GLTF 加载器桩：由各用例决定 load 走成功还是失败回调
const gltfLoaderMocks = vi.hoisted(() => ({
  load: vi.fn(),
  register: vi.fn(),
}))

vi.mock('@/lib/chat-ws-client', () => ({ chatWsClient: chatWsMocks }))

vi.mock('@/lib/chat-management-api', () => ({
  getChatStreams: chatApiMocks.getChatStreams,
}))

vi.mock('@/lib/settings-manager', () => ({
  DEFAULT_SETTINGS: { enableFocusCompanion: false },
  getSetting: settingsMocks.getSetting,
}))

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to }: { children?: ReactNode; search?: Record<string, unknown>; to: string }) => (
    <a href={to}>{children}</a>
  ),
}))

vi.mock('three/examples/jsm/loaders/GLTFLoader.js', () => ({
  GLTFLoader: class MockGLTFLoader {
    register = gltfLoaderMocks.register
    load = gltfLoaderMocks.load
  },
}))

vi.mock('@pixiv/three-vrm', () => ({
  VRMHumanBoneName: {
    Chest: 'chest',
    Head: 'head',
    Hips: 'hips',
    LeftHand: 'leftHand',
    LeftLowerArm: 'leftLowerArm',
    LeftShoulder: 'leftShoulder',
    LeftUpperArm: 'leftUpperArm',
    Neck: 'neck',
    RightHand: 'rightHand',
    RightLowerArm: 'rightLowerArm',
    RightShoulder: 'rightShoulder',
    RightUpperArm: 'rightUpperArm',
    Spine: 'spine',
    UpperChest: 'upperChest',
  },
  VRMLoaderPlugin: class MockVRMLoaderPlugin {},
  VRMUtils: { deepDispose: vi.fn(), rotateVRM0: vi.fn() },
}))

// three.js 极简桩：显式列出 focus.tsx 用到的全部导出（禁止 Proxy 式整体代理）
vi.mock('three', () => {
  class MockVector2 {
    x: number
    y: number
    constructor(x = 0, y = 0) {
      this.x = x
      this.y = y
    }
    set(x: number, y: number) {
      this.x = x
      this.y = y
      return this
    }
  }

  class MockVector3 {
    x: number
    y: number
    z: number
    constructor(x = 0, y = 0, z = 0) {
      this.x = x
      this.y = y
      this.z = z
    }
    set(x: number, y: number, z: number) {
      this.x = x
      this.y = y
      this.z = z
      return this
    }
    setScalar(value: number) {
      return this.set(value, value, value)
    }
    copy(other: { x: number; y: number; z: number }) {
      return this.set(other.x, other.y, other.z)
    }
    sub(other: { x: number; y: number; z: number }) {
      return this.set(this.x - other.x, this.y - other.y, this.z - other.z)
    }
    multiplyScalar(value: number) {
      return this.set(this.x * value, this.y * value, this.z * value)
    }
  }

  class MockRotation {
    x = 0
    y = 0
    z = 0
    set(x: number, y: number, z: number) {
      this.x = x
      this.y = y
      this.z = z
      return this
    }
    copy(other: { x: number; y: number; z: number }) {
      return this.set(other.x, other.y, other.z)
    }
  }

  class MockQuaternion {
    w = 1
    x = 0
    y = 0
    z = 0
    setFromEuler() {
      return this
    }
    copy(other: { w: number; x: number; y: number; z: number }) {
      this.x = other.x
      this.y = other.y
      this.z = other.z
      this.w = other.w
      return this
    }
  }

  class MockEuler {}

  class MockObject3D {
    castShadow = false
    children: MockObject3D[] = []
    frustumCulled = true
    name = ''
    parent: MockObject3D | null = null
    position = new MockVector3()
    quaternion = new MockQuaternion()
    receiveShadow = false
    renderOrder = 0
    rotation = new MockRotation()
    scale = new MockVector3(1, 1, 1)
    userData: Record<string, unknown> = {}
    add(child: MockObject3D) {
      child.parent = this
      this.children.push(child)
      return this
    }
    lookAt() {
      return this
    }
    traverse(callback: (object: MockObject3D) => void) {
      callback(this)
      for (const child of this.children) {
        child.traverse(callback)
      }
    }
  }

  class MockScene extends MockObject3D {}
  class MockGroup extends MockObject3D {}

  class MockPerspectiveCamera extends MockObject3D {
    aspect = 1
    updateProjectionMatrix() {}
  }

  class MockLight extends MockObject3D {
    shadow = {
      bias: 0,
      camera: { bottom: 0, far: 0, left: 0, near: 0, right: 0, top: 0 },
      mapSize: new MockVector2(),
    }
    target: MockObject3D | null = null
  }

  class MockBufferGeometry {
    dispose() {}
  }

  class MockMaterial {
    alphaTest = 0
    map: unknown = null
    name = ''
    needsUpdate = false
    opacity = 1
    side = 0
    transparent = false
    constructor(parameters?: Record<string, unknown>) {
      Object.assign(this, parameters)
    }
    dispose() {}
  }

  class MockTexture {
    magFilter = 0
    minFilter = 0
    needsUpdate = false
    dispose() {}
  }

  class MockDataTexture extends MockTexture {}

  class MockColor {
    clone() {
      return new MockColor()
    }
    offsetHSL() {
      return this
    }
  }

  class MockMesh extends MockObject3D {
    geometry: { dispose: () => void }
    material: unknown
    constructor(geometry?: { dispose: () => void }, material?: unknown) {
      super()
      this.geometry = geometry ?? new MockBufferGeometry()
      this.material = material ?? new MockMaterial()
    }
  }

  class MockSkinnedMesh extends MockMesh {
    bindMatrix = {}
    skeleton = {}
    bind() {}
  }

  class MockWebGLRenderer {
    domElement = document.createElement('canvas')
    outputColorSpace = ''
    shadowMap = { enabled: false, type: 0 }
    toneMapping = 0
    toneMappingExposure = 1
    dispose() {}
    render() {}
    setPixelRatio() {}
    setSize() {}
  }

  class MockClock {
    getDelta() {
      return 0.016
    }
    getElapsedTime() {
      return 0
    }
    start() {}
    stop() {}
  }

  class MockBox3 {
    setFromObject() {
      return this
    }
    getCenter(target: MockVector3) {
      return target.set(0, 0, 0)
    }
    getSize(target: MockVector3) {
      return target.set(0, 0, 0)
    }
  }

  class MockAnimationMixer {
    clipAction() {
      return { play() {} }
    }
    update() {}
  }

  return {
    ACESFilmicToneMapping: 4,
    AmbientLight: MockLight,
    AnimationMixer: MockAnimationMixer,
    BackSide: 1,
    Box3: MockBox3,
    BoxGeometry: MockBufferGeometry,
    CircleGeometry: MockBufferGeometry,
    Clock: MockClock,
    Color: MockColor,
    ConeGeometry: MockBufferGeometry,
    CylinderGeometry: MockBufferGeometry,
    DataTexture: MockDataTexture,
    DirectionalLight: MockLight,
    DoubleSide: 2,
    Euler: MockEuler,
    Group: MockGroup,
    HemisphereLight: MockLight,
    MathUtils: {
      clamp: (value: number, min: number, max: number) => Math.min(max, Math.max(min, value)),
    },
    Mesh: MockMesh,
    MeshBasicMaterial: MockMaterial,
    MeshStandardMaterial: MockMaterial,
    MeshToonMaterial: MockMaterial,
    NearestFilter: 1003,
    Object3D: MockObject3D,
    PCFSoftShadowMap: 2,
    PerspectiveCamera: MockPerspectiveCamera,
    PlaneGeometry: MockBufferGeometry,
    PointLight: MockLight,
    Quaternion: MockQuaternion,
    RGBAFormat: 1023,
    Scene: MockScene,
    SkinnedMesh: MockSkinnedMesh,
    SRGBColorSpace: 'srgb',
    Texture: MockTexture,
    TorusGeometry: MockBufferGeometry,
    Vector2: MockVector2,
    Vector3: MockVector3,
    WebGLRenderer: MockWebGLRenderer,
  }
})

const FOCUS_STORAGE_KEY = 'maibot-focus-companion-state'
const FOCUS_SESSION_ID = 'webui-focus-companion'
const MODEL_URL = '/maimai-focus/mai_vrc_0.9.vrm'

type SessionMessage = Record<string, unknown>

let sessionMessageListener: ((message: SessionMessage) => void) | null = null
let requestFullscreenMock: Mock

/** 供加载成功用例使用的最小场景节点：只实现 focus.tsx 会触碰的属性 */
function createFakeSceneNode() {
  const node = {
    parent: null as unknown,
    position: {
      x: 0,
      y: 0,
      z: 0,
      sub() {
        return node.position
      },
    },
    scale: {
      setScalar() {
        return node.scale
      },
    },
    traverse(callback: (target: unknown) => void) {
      callback(node)
    },
  }
  return node
}

async function renderFocusPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={queryClient}>
      <FocusCompanionPage />
    </QueryClientProvider>
  )
  // 冲刷 openSession / getChatStreams 的微任务，避免挂载后游离的 act 警告
  await act(async () => {})
  return view
}

/** 读取 MetricPill 的展示值：标签节点的下一个兄弟即数值节点 */
function getMetricValue(label: string): string {
  return screen.getByText(label).nextElementSibling?.textContent ?? ''
}

/** 读取聊天条右侧「done」轮数计数 */
function getRoundsValue(): string {
  return screen.getByText('done').previousElementSibling?.textContent ?? ''
}

/** 监听沉浸模式布局事件，返回收集到的 immersive 值序列 */
function trackImmersiveEvents() {
  const events: boolean[] = []
  const listener = (event: Event) => {
    events.push(Boolean((event as CustomEvent<{ immersive?: boolean }>).detail?.immersive))
  }
  window.addEventListener('maibot-layout-immersive-change', listener)
  return {
    events,
    stop: () => window.removeEventListener('maibot-layout-immersive-change', listener),
  }
}

function emitSessionMessage(message: SessionMessage) {
  act(() => {
    sessionMessageListener?.(message)
  })
}

beforeEach(() => {
  window.localStorage.clear()
  sessionMessageListener = null

  settingsMocks.getSetting.mockReturnValue(true)
  chatApiMocks.getChatStreams.mockResolvedValue([])
  chatWsMocks.onSessionMessage.mockImplementation(
    (_sessionId: string, listener: (message: SessionMessage) => void) => {
      sessionMessageListener = listener
      return () => {}
    }
  )
  chatWsMocks.onConnectionChange.mockReturnValue(() => {})
  chatWsMocks.openSession.mockResolvedValue(undefined)
  chatWsMocks.closeSession.mockResolvedValue(undefined)
  chatWsMocks.sendMessage.mockResolvedValue(undefined)
  gltfLoaderMocks.load.mockImplementation(() => {})

  // 阻断渲染循环：动画帧回调不执行，卸载时的 cancel 也不报错
  vi.stubGlobal('requestAnimationFrame', () => 0)
  vi.stubGlobal('cancelAnimationFrame', () => {})

  // jsdom 未实现全屏 API，这里补一个可断言的桩
  requestFullscreenMock = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', {
    configurable: true,
    writable: true,
    value: requestFullscreenMock,
  })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  delete (HTMLElement.prototype as { requestFullscreen?: unknown }).requestFullscreen
  window.localStorage.clear()
})

describe('FocusCompanionPage 功能开关', () => {
  it('设置关闭时显示隐藏说明与设置入口', async () => {
    settingsMocks.getSetting.mockReturnValue(false)

    await renderFocusPage()

    expect(settingsMocks.getSetting).toHaveBeenCalledWith('enableFocusCompanion')
    expect(screen.getByText('专注陪伴已隐藏')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '去设置打开' })).toHaveAttribute('href', '/settings')
    // 未启用时不应打开陪伴会话
    expect(chatWsMocks.openSession).not.toHaveBeenCalled()
  })

  it('监听设置变更事件在启用与禁用之间切换', async () => {
    settingsMocks.getSetting.mockReturnValue(false)

    await renderFocusPage()
    expect(screen.getByText('专注陪伴已隐藏')).toBeInTheDocument()

    // 设置变更事件启用后挂载沉浸体验
    act(() => {
      window.dispatchEvent(
        new CustomEvent('maibot-settings-change', {
          detail: { key: 'enableFocusCompanion', value: true },
        })
      )
    })
    await act(async () => {})
    expect(screen.queryByText('专注陪伴已隐藏')).not.toBeInTheDocument()
    expect(screen.getByLabelText('和麦麦互动')).toBeInTheDocument()
    expect(chatWsMocks.openSession).toHaveBeenCalledTimes(1)

    // 重置事件回落到 DEFAULT_SETTINGS（false）并关闭会话
    act(() => {
      window.dispatchEvent(new Event('maibot-settings-reset'))
    })
    expect(screen.getByText('专注陪伴已隐藏')).toBeInTheDocument()
    expect(chatWsMocks.closeSession).toHaveBeenCalledWith(FOCUS_SESSION_ID)
  })
})

describe('FocusCompanionExperience 计时器', () => {
  it('默认渲染 25 分钟倒计时、心情台词与统计指标', async () => {
    await renderFocusPage()

    expect(document.title).toBe('专注陪伴 - MaiBot Dashboard')
    expect(screen.getByText('25:00')).toBeInTheDocument()
    // 默认 idle 心情与开场台词
    expect(screen.getByText('麦麦在这里。')).toBeInTheDocument()
    expect(screen.getByText('今天也一起慢慢来。')).toBeInTheDocument()
    // 三个指标与轮数计数初始为零
    expect(getMetricValue('today')).toBe('0m')
    expect(getMetricValue('chat')).toBe('0')
    expect(getMetricValue('grove')).toBe('0')
    expect(getRoundsValue()).toBe('0')
    // 无树苗时展示引导文案
    expect(screen.getByText('完成一段专注后，会长出第一棵树苗。')).toBeInTheDocument()
  })

  it('切换休息模式更新倒计时时长，重置恢复当前模式初始值', async () => {
    await renderFocusPage()

    fireEvent.click(screen.getByRole('button', { name: '5 分钟' }))
    expect(screen.getByText('05:00')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '15 分钟' }))
    expect(screen.getByText('15:00')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '25 分钟' }))
    expect(screen.getByText('25:00')).toBeInTheDocument()

    // 开始后重置：停止计时并回到当前模式初始时长与 focus 心情
    fireEvent.click(screen.getByRole('button', { name: '开始' }))
    expect(screen.getByRole('button', { name: '暂停' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重置' }))
    expect(screen.getByRole('button', { name: '开始' })).toBeInTheDocument()
    expect(screen.getByText('25:00')).toBeInTheDocument()
    expect(screen.getByText('安静推进就好。')).toBeInTheDocument()
  })

  it('自定义专注分钟数会被钳制到 1-240 区间', async () => {
    await renderFocusPage()
    const minutesInput = screen.getByLabelText('自定义专注分钟数') as HTMLInputElement

    // 超上限钳制到 240，并同步倒计时与模式按钮文案
    fireEvent.change(minutesInput, { target: { value: '999' } })
    expect(minutesInput.value).toBe('240')
    expect(screen.getByText('240:00')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '240 分钟' })).toBeInTheDocument()

    // 低于下限钳制到 1
    fireEvent.change(minutesInput, { target: { value: '0' } })
    expect(minutesInput.value).toBe('1')
    expect(screen.getByText('01:00')).toBeInTheDocument()
  })

  it('开始专注请求全屏并锁定控件，暂停后解锁', async () => {
    const immersive = trackImmersiveEvents()
    await renderFocusPage()

    fireEvent.click(screen.getByRole('button', { name: '开始' }))

    // 进入沉浸 + 请求全屏
    expect(requestFullscreenMock).toHaveBeenCalledTimes(1)
    expect(immersive.events.at(-1)).toBe(true)
    // 专注锁定：模式/分钟数/聊天/沉浸按钮全部禁用
    expect(screen.getByRole('button', { name: '5 分钟' })).toBeDisabled()
    expect(screen.getByLabelText('自定义专注分钟数')).toBeDisabled()
    expect(screen.getByLabelText('和麦麦对话')).toBeDisabled()
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '退出沉浸' })).toBeDisabled()
    // 锁定期间点击麦麦被文档级捕获拦截，心情保持 focus
    fireEvent.click(screen.getByLabelText('和麦麦互动'))
    expect(screen.getByText('安静推进就好。')).toBeInTheDocument()

    // 暂停按钮是锁定白名单控件，可以点击解除锁定
    fireEvent.click(screen.getByRole('button', { name: '暂停' }))
    expect(screen.getByRole('button', { name: '开始' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '5 分钟' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '退出沉浸' })).toBeEnabled()
    // 解锁后可以再和麦麦互动
    fireEvent.click(screen.getByLabelText('和麦麦互动'))
    expect(screen.getByText('完成一段啦。')).toBeInTheDocument()

    // 手动退出沉浸并广播事件
    fireEvent.click(screen.getByRole('button', { name: '退出沉浸' }))
    expect(immersive.events.at(-1)).toBe(false)
    expect(screen.getByRole('button', { name: '隐藏边栏' })).toBeInTheDocument()
    immersive.stop()
  })

  it('专注计时完成：长出树苗、累计今日时长并向麦麦报喜', async () => {
    vi.useFakeTimers()
    // 固定随机数：树苗为琥珀树苗，鼓励语取第一条
    vi.spyOn(Math, 'random').mockReturnValue(0)
    await renderFocusPage()

    fireEvent.change(screen.getByLabelText('自定义专注分钟数'), { target: { value: '1' } })
    expect(screen.getByText('01:00')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '开始' }))
    expect(requestFullscreenMock).toHaveBeenCalledTimes(1)

    // 走完 60 秒倒计时，再触发结算的 0ms 定时器
    await act(async () => {
      vi.advanceTimersByTime(60_000)
    })
    await act(async () => {
      vi.advanceTimersByTime(0)
    })

    // 结算：停止计时并重置回 1 分钟
    expect(screen.getByRole('button', { name: '开始' })).toBeInTheDocument()
    expect(screen.getByText('01:00')).toBeInTheDocument()
    expect(getRoundsValue()).toBe('1')
    // cheer 心情 + 本地鼓励台词（含随机树苗描述）
    expect(screen.getByText('完成一段啦。')).toBeInTheDocument()
    expect(
      screen.getByText('完成啦，今天的专注已经长出形状了。 获得 琥珀树苗：像一枚安静发亮的时间切片。')
    ).toBeInTheDocument()
    // 树苗与今日时长指标更新
    expect(getMetricValue('grove')).toBe('1')
    expect(getMetricValue('today')).toBe('1m')
    expect(screen.getAllByLabelText('琥珀树苗：像一枚安静发亮的时间切片。').length).toBeGreaterThan(0)
    // 静默向麦麦报喜（不显示正在输入）
    expect(chatWsMocks.sendMessage).toHaveBeenCalledTimes(1)
    expect(chatWsMocks.sendMessage).toHaveBeenCalledWith(
      FOCUS_SESSION_ID,
      '我完成了一段专注计时，并获得了琥珀树苗。用一句很短的话鼓励我。',
      '专注中的你'
    )
    // 存档回写：树苗、今日秒数与自定义分钟数
    const stored = JSON.parse(window.localStorage.getItem(FOCUS_STORAGE_KEY) ?? '{}')
    expect(stored.saplings).toEqual(['amber'])
    expect(stored.todayFocusSeconds).toBe(60)
    expect(stored.customFocusMinutes).toBe(1)
  })

  it('休息计时完成：不长树苗，只发送休息消息并显示正在思考', async () => {
    vi.useFakeTimers()
    await renderFocusPage()

    fireEvent.click(screen.getByRole('button', { name: '5 分钟' }))
    fireEvent.click(screen.getByRole('button', { name: '开始' }))
    // 休息模式不进入沉浸也不请求全屏
    expect(requestFullscreenMock).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '隐藏边栏' })).toBeInTheDocument()

    await act(async () => {
      vi.advanceTimersByTime(300_000)
    })
    await act(async () => {
      vi.advanceTimersByTime(0)
    })

    expect(getRoundsValue()).toBe('1')
    expect(screen.getByText('05:00')).toBeInTheDocument()
    // 不长树苗
    expect(getMetricValue('grove')).toBe('0')
    expect(getMetricValue('today')).toBe('0m')
    expect(chatWsMocks.sendMessage).toHaveBeenCalledWith(
      FOCUS_SESSION_ID,
      '我完成了一段休息计时，用一句很短的话回应我。',
      '专注中的你'
    )
    // 默认 showTyping：展示「正在想」占位
    expect(screen.getByText('麦麦正在想...')).toBeInTheDocument()
  })

  it('全屏请求失败只记录警告，不影响计时继续', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    requestFullscreenMock.mockRejectedValue(new Error('用户拒绝了全屏'))
    await renderFocusPage()

    fireEvent.click(screen.getByRole('button', { name: '开始' }))

    await waitFor(() => {
      expect(warnSpy).toHaveBeenCalledWith('进入专注全屏失败:', expect.any(Error))
    })
    expect(screen.getByRole('button', { name: '暂停' })).toBeInTheDocument()
  })
})

describe('FocusCompanionExperience 陪伴聊天', () => {
  it('挂载时以固定参数打开陪伴会话，并处理 typing/bot_message/history 消息', async () => {
    await renderFocusPage()

    expect(chatWsMocks.openSession).toHaveBeenCalledWith(FOCUS_SESSION_ID, {
      client: { type: 'webui', name: 'MaiBot WebUI' },
      user_id: 'webui_focus_user',
      user_name: '专注中的你',
      platform: 'webui',
      group_name: '麦麦的专注房间',
      group_id: 'webui_focus_room',
    })
    expect(chatWsMocks.onSessionMessage).toHaveBeenCalledWith(FOCUS_SESSION_ID, expect.any(Function))
    expect(sessionMessageListener).not.toBeNull()

    // typing 消息切换「正在想」占位
    emitSessionMessage({ type: 'typing', is_typing: true })
    expect(screen.getByText('麦麦正在想...')).toBeInTheDocument()

    // bot_message 去掉首尾空白后展示，并结束输入状态
    emitSessionMessage({ type: 'bot_message', content: '  今晚也很棒。  ' })
    expect(screen.getByText('今晚也很棒。')).toBeInTheDocument()
    expect(screen.queryByText('麦麦正在想...')).not.toBeInTheDocument()

    // 空内容的 bot_message 不覆盖既有台词，但会结束输入状态
    emitSessionMessage({ type: 'typing', is_typing: true })
    emitSessionMessage({ type: 'bot_message', content: '   ' })
    expect(screen.getByText('今晚也很棒。')).toBeInTheDocument()

    // history 消息取最后一条机器人回复
    emitSessionMessage({
      type: 'history',
      messages: [
        { is_bot: true, content: '较早的回复' },
        { is_bot: false, content: '我的提问' },
        { is_bot: true, content: '最新的回复' },
        { is_bot: false, content: '结尾用户消息' },
      ],
    })
    expect(screen.getByText('最新的回复')).toBeInTheDocument()
  })

  it('发送输入内容：调用 WS、清空草稿并切换聆听心情', async () => {
    await renderFocusPage()
    const chatInput = screen.getByLabelText('和麦麦对话') as HTMLInputElement
    const sendButton = screen.getByRole('button', { name: '发送' })

    // 空草稿与纯空白草稿都不可发送
    expect(sendButton).toBeDisabled()
    fireEvent.change(chatInput, { target: { value: '   ' } })
    expect(sendButton).toBeDisabled()

    fireEvent.change(chatInput, { target: { value: '今晚一起复习线代' } })
    expect(sendButton).toBeEnabled()
    fireEvent.click(sendButton)

    expect(chatWsMocks.sendMessage).toHaveBeenCalledTimes(1)
    expect(chatWsMocks.sendMessage).toHaveBeenCalledWith(
      FOCUS_SESSION_ID,
      '今晚一起复习线代',
      '专注中的你'
    )
    expect(chatInput.value).toBe('')
    // 心情切到 listening，且默认展示「正在想」
    expect(screen.getByText('我听见了。')).toBeInTheDocument()
    expect(screen.getByText('麦麦正在想...')).toBeInTheDocument()
  })

  it('发送失败时展示本地安抚台词', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    chatWsMocks.sendMessage.mockRejectedValue(new Error('连接断开'))
    await renderFocusPage()

    fireEvent.change(screen.getByLabelText('和麦麦对话'), { target: { value: '在吗' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    await waitFor(() => {
      expect(screen.getByText('发送没有成功，先继续专注。')).toBeInTheDocument()
    })
    expect(screen.queryByText('麦麦正在想...')).not.toBeInTheDocument()
    expect(errorSpy).toHaveBeenCalledWith('专注陪伴消息发送失败:', expect.any(Error))
  })

  it('会话打开失败时提示没有连上', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    chatWsMocks.openSession.mockRejectedValue(new Error('后端离线'))
    await renderFocusPage()

    await waitFor(() => {
      expect(screen.getByText('麦麦会话暂时没有连上。')).toBeInTheDocument()
    })
    expect(errorSpy).toHaveBeenCalledWith('专注陪伴会话打开失败:', expect.any(Error))
  })

  it('卸载时关闭陪伴会话并退出沉浸布局', async () => {
    const immersive = trackImmersiveEvents()
    const view = await renderFocusPage()

    view.unmount()

    expect(chatWsMocks.closeSession).toHaveBeenCalledWith(FOCUS_SESSION_ID)
    expect(immersive.events.at(-1)).toBe(false)
    immersive.stop()
  })

  it('点击麦麦循环切换心情台词', async () => {
    await renderFocusPage()
    const character = screen.getByLabelText('和麦麦互动')

    expect(screen.getByText('麦麦在这里。')).toBeInTheDocument()
    fireEvent.click(character)
    expect(screen.getByText('完成一段啦。')).toBeInTheDocument()
    fireEvent.click(character)
    expect(screen.getByText('我听见了。')).toBeInTheDocument()
    fireEvent.click(character)
    expect(screen.getByText('安静推进就好。')).toBeInTheDocument()
    fireEvent.click(character)
    expect(screen.getByText('完成一段啦。')).toBeInTheDocument()
  })

  it('聊天流数量展示在 chat 指标中', async () => {
    chatApiMocks.getChatStreams.mockResolvedValue([
      { stream_id: 'a' },
      { stream_id: 'b' },
      { stream_id: 'c' },
    ])
    await renderFocusPage()

    expect(chatApiMocks.getChatStreams).toHaveBeenCalledWith(200)
    await waitFor(() => {
      expect(getMetricValue('chat')).toBe('3')
    })
  })
})

describe('FocusCompanionExperience 本地存档', () => {
  it('读取存档恢复分钟数、树苗与今日时长，并过滤非法树苗', async () => {
    const today = new Date().toISOString().slice(0, 10)
    window.localStorage.setItem(
      FOCUS_STORAGE_KEY,
      JSON.stringify({
        customFocusMinutes: 50.4,
        saplings: ['moss', 'amber', 'ghost-kind'],
        todayFocusDate: today,
        todayFocusSeconds: 3599.9,
      })
    )

    await renderFocusPage()

    // 50.4 四舍五入为 50 分钟
    expect(screen.getByText('50:00')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '50 分钟' })).toBeInTheDocument()
    // 3599 秒向下取整为 59 分钟
    expect(getMetricValue('today')).toBe('59m')
    // 非法树苗被过滤，只剩两棵
    expect(getMetricValue('grove')).toBe('2')
    expect(screen.getAllByLabelText(/苔光树苗/).length).toBe(1)
    // 最近获得的树苗展示在面板里
    expect(screen.getAllByText('琥珀树苗').length).toBeGreaterThan(0)
  })

  it('兼容数字型旧存档并展示溢出计数', async () => {
    const today = new Date().toISOString().slice(0, 10)
    window.localStorage.setItem(
      FOCUS_STORAGE_KEY,
      JSON.stringify({
        customFocusMinutes: 25,
        saplings: 16,
        todayFocusDate: today,
        todayFocusSeconds: 0,
      })
    )

    await renderFocusPage()

    // 数字 16 展开成循环的 16 棵树苗
    expect(getMetricValue('grove')).toBe('16')
    // 主面板最多展示 14 棵，其余以 +N 汇总
    expect(screen.getByText('+2')).toBeInTheDocument()
    expect(screen.getAllByLabelText(/琥珀树苗/).length).toBe(4)
  })

  it('跨天存档重置今日专注秒数并回写当天日期', async () => {
    window.localStorage.setItem(
      FOCUS_STORAGE_KEY,
      JSON.stringify({
        customFocusMinutes: 30,
        saplings: ['paper'],
        todayFocusDate: '2000-01-01',
        todayFocusSeconds: 1200,
      })
    )

    await renderFocusPage()

    expect(screen.getByText('30:00')).toBeInTheDocument()
    // 昨天的累计不带入今天
    expect(getMetricValue('today')).toBe('0m')
    expect(getMetricValue('grove')).toBe('1')

    const stored = JSON.parse(window.localStorage.getItem(FOCUS_STORAGE_KEY) ?? '{}')
    expect(stored.todayFocusDate).toBe(new Date().toISOString().slice(0, 10))
    expect(stored.todayFocusSeconds).toBe(0)
    expect(stored.saplings).toEqual(['paper'])
  })

  it('损坏存档回退到默认状态', async () => {
    window.localStorage.setItem(FOCUS_STORAGE_KEY, '{{{not-json')

    await renderFocusPage()

    expect(screen.getByText('25:00')).toBeInTheDocument()
    expect(getMetricValue('today')).toBe('0m')
    expect(getMetricValue('grove')).toBe('0')
  })
})

describe('FocusCompanionExperience 三维模型加载', () => {
  it('普通 GLTF 加载成功后在画布上打标记', async () => {
    gltfLoaderMocks.load.mockImplementation(
      (_url: unknown, onLoad: (gltf: unknown) => void) => {
        onLoad({ animations: [{}], scene: createFakeSceneNode(), userData: {} })
      }
    )

    await renderFocusPage()

    expect(gltfLoaderMocks.register).toHaveBeenCalledWith(expect.any(Function))
    expect(gltfLoaderMocks.load).toHaveBeenCalledTimes(1)
    expect(gltfLoaderMocks.load.mock.calls[0][0]).toBe(MODEL_URL)
    const canvas = document.querySelector('[data-focus-model-canvas="true"]') as HTMLElement | null
    expect(canvas).not.toBeNull()
    expect(canvas?.dataset.focusModelLoaded).toBe('true')
  })

  it('VRM 加载成功应用初始姿态，卸载时深度释放', async () => {
    const poseSpy = vi.fn()
    const vrmScene = createFakeSceneNode()
    const fakeVRM = {
      scene: vrmScene,
      humanoid: { setNormalizedPose: poseSpy },
      expressionManager: null,
      springBoneManager: undefined,
      lookAt: null,
      update: vi.fn(),
    }
    gltfLoaderMocks.load.mockImplementation(
      (_url: unknown, onLoad: (gltf: unknown) => void) => {
        onLoad({ animations: [], scene: createFakeSceneNode(), userData: { vrm: fakeVRM } })
      }
    )

    const view = await renderFocusPage()

    // VRM0 旋转修正与初始姿态：hips 为单位四元数旋转
    expect(vi.mocked(VRMUtils.rotateVRM0)).toHaveBeenCalledTimes(1)
    expect(poseSpy).toHaveBeenCalled()
    const pose = poseSpy.mock.calls[0][0] as Record<string, { rotation: number[] }>
    expect(pose.hips.rotation).toEqual([0, 0, 0, 1])
    expect(pose.head.rotation).toHaveLength(4)

    view.unmount()
    expect(vi.mocked(VRMUtils.deepDispose)).toHaveBeenCalledWith(vrmScene)
  })

  it('模型加载失败时记录错误日志', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    gltfLoaderMocks.load.mockImplementation(
      (_url: unknown, _onLoad: unknown, _onProgress: unknown, onError?: (error: unknown) => void) => {
        onError?.(new Error('模型文件损坏'))
      }
    )

    await renderFocusPage()

    expect(errorSpy).toHaveBeenCalledWith('专注陪伴模型加载失败:', expect.any(Error))
    const canvas = document.querySelector('[data-focus-model-canvas="true"]') as HTMLElement | null
    expect(canvas?.dataset.focusModelLoaded).toBeUndefined()
  })
})
