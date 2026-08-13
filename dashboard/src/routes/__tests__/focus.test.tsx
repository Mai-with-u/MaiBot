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
import { act, cleanup, createEvent, fireEvent, render, screen, waitFor } from '@testing-library/react'
import * as THREE from 'three'
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
let connectionListener: ((connected: boolean) => void) | null = null
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

/** 按需执行动画帧，覆盖相机跟随 / 表情 / 可见性，而不把 RAF 交给假计时器 */
function installRafQueue() {
  let nextId = 1
  const callbacks = new Map<number, FrameRequestCallback>()
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    const id = nextId
    nextId += 1
    callbacks.set(id, callback)
    return id
  })
  vi.stubGlobal('cancelAnimationFrame', (id: number) => {
    callbacks.delete(Number(id))
  })
  return {
    flush() {
      const pending = [...callbacks.values()]
      callbacks.clear()
      for (const callback of pending) {
        callback(0)
      }
    },
  }
}

function findNamedChild(root: THREE.Object3D, name: string): THREE.Object3D | undefined {
  return root.children.find((child) => child.name === name)
}

/** 带各类材质名的场景图，用来走卡通化 / 描边 / 释放分支 */
function createStyledModelScene() {
  const root = new THREE.Group()
  const addMesh = (meshName: string, material: THREE.Material | THREE.Material[], skinned = false) => {
    const mesh = skinned
      ? new THREE.SkinnedMesh(new THREE.BoxGeometry(), material)
      : new THREE.Mesh(new THREE.BoxGeometry(), material)
    mesh.name = meshName
    root.add(mesh)
    return mesh
  }

  addMesh(
    'leaf',
    new THREE.MeshStandardMaterial({
      name: '三叶草叶',
      map: new THREE.Texture(),
      color: new THREE.Color(),
    })
  )
  addMesh('skin', new THREE.MeshStandardMaterial({ name: '皮肤', color: new THREE.Color() }))
  addMesh('sclera', new THREE.MeshStandardMaterial({ name: '眼白' }))
  addMesh(
    'hair',
    new THREE.MeshStandardMaterial({
      name: '头发',
      map: new THREE.Texture(),
      color: new THREE.Color(),
    }),
    true
  )
  addMesh('brow', new THREE.MeshStandardMaterial({ name: 'eyebrow', color: new THREE.Color() }))
  addMesh('cloth', [
    new THREE.MeshStandardMaterial({ name: 'jkq', color: new THREE.Color() }),
    new THREE.MeshStandardMaterial({ name: '罩袍', color: new THREE.Color() }),
  ])
  addMesh('body', new THREE.MeshStandardMaterial({ name: 'body', color: new THREE.Color() }))
  addMesh('highlight', new THREE.MeshStandardMaterial({ name: 'highlight' }))
  addMesh('mouth', new THREE.MeshStandardMaterial({ name: '口腔' }))
  addMesh('tongue', new THREE.MeshStandardMaterial({ name: '舌头' }))
  addMesh('face', new THREE.MeshStandardMaterial({ name: '脸', color: new THREE.Color() }))
  addMesh('eye-en', new THREE.MeshStandardMaterial({ name: 'LeftEye' }))
  addMesh('clover', new THREE.MeshStandardMaterial({ name: 'clover', color: new THREE.Color() }))
  addMesh('dark-hair', new THREE.MeshStandardMaterial({ name: '深色毛发', color: new THREE.Color() }))
  addMesh('grass', new THREE.MeshStandardMaterial({ name: '草', color: new THREE.Color() }))
  addMesh('leaf-en', new THREE.MeshStandardMaterial({ name: 'leaf', color: new THREE.Color() }))
  addMesh('unnamed', new THREE.MeshStandardMaterial({ name: '', color: new THREE.Color() }))

  const bone = new THREE.Object3D()
  bone.name = 'hip-bone'
  root.add(bone)
  return root
}

function captureLoader() {
  let onLoad: ((gltf: unknown) => void) | null = null
  let onError: ((error: unknown) => void) | null = null
  gltfLoaderMocks.load.mockImplementation(
    (_url: unknown, load: (gltf: unknown) => void, _progress: unknown, error?: (error: unknown) => void) => {
      onLoad = load
      onError = error ?? null
    }
  )
  return {
    finish(gltf: unknown) {
      act(() => {
        onLoad?.(gltf)
      })
    },
    fail(error: unknown) {
      act(() => {
        onError?.(error)
      })
    },
  }
}

function createExpressionManager(available: string[]) {
  return {
    getExpression: vi.fn((name: string) => (available.includes(name) ? { name } : null)),
    setValue: vi.fn(),
  }
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
  connectionListener = null

  settingsMocks.getSetting.mockReturnValue(true)
  chatApiMocks.getChatStreams.mockResolvedValue([])
  chatWsMocks.onSessionMessage.mockImplementation(
    (_sessionId: string, listener: (message: SessionMessage) => void) => {
      sessionMessageListener = listener
      return () => {}
    }
  )
  chatWsMocks.onConnectionChange.mockImplementation((listener: (connected: boolean) => void) => {
    connectionListener = listener
    return () => {}
  })
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

  it('其它设置项变更不会打开专注陪伴', async () => {
    settingsMocks.getSetting.mockReturnValue(false)
    await renderFocusPage()

    act(() => {
      window.dispatchEvent(
        new CustomEvent('maibot-settings-change', {
          detail: { key: 'theme', value: 'dark' },
        })
      )
    })

    expect(screen.getByText('专注陪伴已隐藏')).toBeInTheDocument()
    expect(chatWsMocks.openSession).not.toHaveBeenCalled()
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

  it('已有全屏元素时不再重复请求，优先使用 #main-content', async () => {
    const main = document.createElement('main')
    main.id = 'main-content'
    const mainFullscreen = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(main, 'requestFullscreen', {
      configurable: true,
      value: mainFullscreen,
    })
    document.body.appendChild(main)

    Object.defineProperty(document, 'fullscreenElement', {
      configurable: true,
      get: () => main,
    })

    try {
      await renderFocusPage()
      fireEvent.click(screen.getByRole('button', { name: '开始' }))
      expect(mainFullscreen).not.toHaveBeenCalled()
      expect(requestFullscreenMock).not.toHaveBeenCalled()
    } finally {
      delete (document as { fullscreenElement?: unknown }).fullscreenElement
      main.remove()
    }
  })

  it('没有全屏元素时对 #main-content 请求全屏', async () => {
    const main = document.createElement('main')
    main.id = 'main-content'
    const mainFullscreen = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(main, 'requestFullscreen', {
      configurable: true,
      value: mainFullscreen,
    })
    document.body.appendChild(main)

    try {
      await renderFocusPage()
      fireEvent.click(screen.getByRole('button', { name: '开始' }))
      expect(mainFullscreen).toHaveBeenCalledTimes(1)
      expect(requestFullscreenMock).not.toHaveBeenCalled()
    } finally {
      main.remove()
    }
  })

  it('休息模式修改自定义分钟数只改按钮文案，不改当前倒计时', async () => {
    await renderFocusPage()
    fireEvent.click(screen.getByRole('button', { name: '5 分钟' }))
    expect(screen.getByText('05:00')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('自定义专注分钟数'), { target: { value: '40' } })
    expect(screen.getByRole('button', { name: '40 分钟' })).toBeInTheDocument()
    expect(screen.getByText('05:00')).toBeInTheDocument()
  })

  it('数字框无法解析的分钟会变成 0 并被钳到 1', async () => {
    await renderFocusPage()
    // number input 把非数字收成空串，Number('') === 0，再钳到下限 1
    fireEvent.change(screen.getByLabelText('自定义专注分钟数'), { target: { value: 'abc' } })
    expect((screen.getByLabelText('自定义专注分钟数') as HTMLInputElement).value).toBe('1')
    expect(screen.getByText('01:00')).toBeInTheDocument()
  })

  it('手动切换沉浸模式广播布局事件', async () => {
    const immersive = trackImmersiveEvents()
    await renderFocusPage()

    fireEvent.click(screen.getByRole('button', { name: '隐藏边栏' }))
    expect(immersive.events.at(-1)).toBe(true)
    expect(screen.getByRole('button', { name: '退出沉浸' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '退出沉浸' }))
    expect(immersive.events.at(-1)).toBe(false)
    immersive.stop()
  })

  it('专注锁定拦截文档级键盘，白名单控件与表单提交除外', async () => {
    await renderFocusPage()
    fireEvent.change(screen.getByLabelText('和麦麦对话'), { target: { value: '先写下来' } })
    fireEvent.click(screen.getByRole('button', { name: '开始' }))

    const lockedRoot = document.querySelector('[data-focus-companion="true"]') as HTMLElement
    const blocked = createEvent.keyDown(lockedRoot, { key: 'a', bubbles: true, cancelable: true })
    fireEvent(lockedRoot, blocked)
    expect(blocked.defaultPrevented).toBe(true)

    const pauseButton = screen.getByRole('button', { name: '暂停' })
    const allowed = createEvent.keyDown(pauseButton, { key: 'Enter', bubbles: true, cancelable: true })
    fireEvent(pauseButton, allowed)
    expect(allowed.defaultPrevented).toBe(false)

    fireEvent.keyDown(screen.getByLabelText('和麦麦互动'), { key: 'Enter' })
    expect(screen.getByText('安静推进就好。')).toBeInTheDocument()

    const form = screen.getByLabelText('和麦麦对话').closest('form')
    expect(form).not.toBeNull()
    fireEvent.submit(form as HTMLFormElement)
    expect(chatWsMocks.sendMessage).not.toHaveBeenCalled()
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

    // session_info 只更新内部昵称，空名字与无关类型都不改台词
    emitSessionMessage({ type: 'session_info', bot_name: '  小麦  ' })
    emitSessionMessage({ type: 'session_info', bot_name: '   ' })
    emitSessionMessage({ type: 'unknown_event' })
    expect(screen.getByText('最新的回复')).toBeInTheDocument()

    // history 非数组 / 无机器人 / 空白内容 / 缺 content 字段都不覆盖台词
    emitSessionMessage({ type: 'history', messages: 'not-an-array' })
    emitSessionMessage({ type: 'history', messages: [{ is_bot: false, content: '用户自己说的' }] })
    emitSessionMessage({ type: 'history', messages: [{ is_bot: true, content: '   ' }] })
    emitSessionMessage({ type: 'history', messages: [null, 12, { is_bot: true }] })
    expect(screen.getByText('最新的回复')).toBeInTheDocument()

    emitSessionMessage({ type: 'typing', is_typing: false })
    expect(screen.queryByText('麦麦正在想...')).not.toBeInTheDocument()

    act(() => {
      connectionListener?.(true)
      connectionListener?.(false)
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

  it('键盘 Enter / Space 切换心情，其它键忽略', async () => {
    await renderFocusPage()
    const character = screen.getByLabelText('和麦麦互动')

    fireEvent.keyDown(character, { key: 'Enter' })
    expect(screen.getByText('完成一段啦。')).toBeInTheDocument()
    fireEvent.keyDown(character, { key: ' ' })
    expect(screen.getByText('我听见了。')).toBeInTheDocument()
    fireEvent.keyDown(character, { key: 'Tab' })
    expect(screen.getByText('我听见了。')).toBeInTheDocument()
  })

  it('卸载后到达的会话消息与开关会话结果不会写回界面', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    let resolveOpen: (() => void) | null = null
    let rejectOpen: ((error: Error) => void) | null = null
    let openCount = 0
    chatWsMocks.openSession.mockImplementation(
      () =>
        new Promise<void>((resolve, reject) => {
          openCount += 1
          if (openCount === 1) {
            resolveOpen = resolve
          } else {
            rejectOpen = reject
          }
        })
    )

    const first = await renderFocusPage()
    first.unmount()
    emitSessionMessage({ type: 'bot_message', content: '迟到的回复' })
    act(() => {
      connectionListener?.(true)
    })
    await act(async () => {
      resolveOpen?.()
    })
    expect(screen.queryByText('迟到的回复')).not.toBeInTheDocument()

    const second = await renderFocusPage()
    second.unmount()
    await act(async () => {
      rejectOpen?.(new Error('已经卸载'))
    })
    expect(errorSpy).toHaveBeenCalledWith('专注陪伴会话打开失败:', expect.any(Error))
    expect(screen.queryByText('麦麦会话暂时没有连上。')).not.toBeInTheDocument()
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

  it('聊天流请求失败时 chat 指标保持 0', async () => {
    chatApiMocks.getChatStreams.mockRejectedValue(new Error('聊天流不可用'))
    await renderFocusPage()

    await waitFor(() => {
      expect(chatApiMocks.getChatStreams).toHaveBeenCalled()
    })
    expect(getMetricValue('chat')).toBe('0')
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

  it('非法分钟、字符串树苗和负数秒数按默认值归一', async () => {
    const today = new Date().toISOString().slice(0, 10)
    window.localStorage.setItem(
      FOCUS_STORAGE_KEY,
      JSON.stringify({
        customFocusMinutes: 'not-a-number',
        saplings: 'amber',
        todayFocusDate: today,
        todayFocusSeconds: -80,
      })
    )

    await renderFocusPage()

    expect(screen.getByText('25:00')).toBeInTheDocument()
    expect(getMetricValue('grove')).toBe('0')
    expect(getMetricValue('today')).toBe('0m')
  })

  it('橙芽树苗渲染果实造型与悬浮说明', async () => {
    const today = new Date().toISOString().slice(0, 10)
    window.localStorage.setItem(
      FOCUS_STORAGE_KEY,
      JSON.stringify({
        customFocusMinutes: 25,
        saplings: ['citrus'],
        todayFocusDate: today,
        todayFocusSeconds: 0,
      })
    )

    await renderFocusPage()

    expect(screen.getAllByLabelText('橙芽树苗：把刚完成的专注收成一点暖橙色。').length).toBeGreaterThan(0)
    expect(screen.getAllByText('橙芽树苗').length).toBeGreaterThan(0)
    expect(screen.getAllByText('把刚完成的专注收成一点暖橙色。').length).toBeGreaterThan(0)
  })

  it('挂载后读到的日期跨天时清零今日秒数', async () => {
    window.localStorage.setItem(
      FOCUS_STORAGE_KEY,
      JSON.stringify({
        customFocusMinutes: 25,
        saplings: [],
        todayFocusDate: '2026-08-13',
        todayFocusSeconds: 600,
      })
    )

    // 存档读取里先取 today 再 getItem；用 getItem 作为“已经读过旧日”的分界，
    // 让随后的跨日 effect 看到新的一天。jsdom 的 localStorage 方法在原型上，必须 spy 原型。
    let storageRead = false
    const originalGetItem = Storage.prototype.getItem
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (this: Storage, key: string) {
      const value = originalGetItem.call(this, key)
      if (String(key) === FOCUS_STORAGE_KEY) {
        storageRead = true
      }
      return value
    })
    vi.spyOn(Date.prototype, 'toISOString').mockImplementation(() =>
      storageRead ? '2026-08-14T12:00:00.000Z' : '2026-08-13T12:00:00.000Z'
    )

    const writes: Array<{ date: string; seconds: number }> = []
    const originalSetItem = Storage.prototype.setItem
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (this: Storage, key: string, value: string) {
      if (String(key) === FOCUS_STORAGE_KEY) {
        const parsed = JSON.parse(String(value)) as { todayFocusDate: string; todayFocusSeconds: number }
        writes.push({ date: parsed.todayFocusDate, seconds: parsed.todayFocusSeconds })
      }
      return originalSetItem.call(this, key, value)
    })

    await renderFocusPage()

    expect(writes.some((item) => item.date === '2026-08-13' && item.seconds === 600)).toBe(true)
    expect(writes.at(-1)).toEqual({ date: '2026-08-14', seconds: 0 })
    expect(getMetricValue('today')).toBe('0m')
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
    expect(document.querySelector('[data-focus-scene-canvas="true"]')).not.toBeNull()
  })

  it('模型尚未返回时画布不标记 loaded，并会实例化 VRM 插件', async () => {
    captureLoader()
    await renderFocusPage()

    const canvas = document.querySelector('[data-focus-model-canvas="true"]') as HTMLElement | null
    expect(canvas).not.toBeNull()
    expect(canvas?.dataset.focusModelLoaded).toBeUndefined()
    expect(gltfLoaderMocks.register).toHaveBeenCalledWith(expect.any(Function))
    const pluginFactory = gltfLoaderMocks.register.mock.calls[0][0] as (parser: unknown) => unknown
    expect(pluginFactory({})).toBeInstanceOf(Object)
  })

  it('VRM 卡通化按材质名分流，并给蒙皮网格绑定描边', async () => {
    const scene = createStyledModelScene()
    const leafMap = (findNamedChild(scene, 'leaf') as THREE.Mesh).material as THREE.Material & { map?: unknown }
    const sourceLeafMap = leafMap.map
    vi.spyOn(THREE.Box3.prototype, 'getSize').mockImplementation((target: THREE.Vector3) => {
      target.set(2, 4, 2)
      return target
    })
    const bindSpy = vi.spyOn(THREE.SkinnedMesh.prototype, 'bind')
    gltfLoaderMocks.load.mockImplementation((_url: unknown, onLoad: (gltf: unknown) => void) => {
      onLoad({
        animations: [],
        scene,
        userData: {
          vrm: {
            scene,
            humanoid: { setNormalizedPose: vi.fn() },
            expressionManager: null,
            update: vi.fn(),
          },
        },
      })
    })

    await renderFocusPage()

    expect((findNamedChild(scene, 'leaf') as THREE.Mesh).material).toEqual(
      expect.objectContaining({ name: '三叶草叶 toon', map: sourceLeafMap })
    )
    expect((findNamedChild(scene, 'skin') as THREE.Mesh).material).toEqual(
      expect.objectContaining({ name: '皮肤 skin toon' })
    )
    expect((findNamedChild(scene, 'sclera') as THREE.Mesh).material).toEqual(
      expect.objectContaining({ name: '眼白 soft face' })
    )
    expect((findNamedChild(scene, 'hair') as THREE.Mesh).material).toEqual(
      expect.objectContaining({ name: '头发 toon' })
    )
    expect((findNamedChild(scene, 'highlight') as THREE.Mesh).material).toEqual(
      expect.objectContaining({ name: 'highlight soft face' })
    )
    expect((findNamedChild(scene, 'unnamed') as THREE.Mesh).material).toEqual(
      expect.objectContaining({ name: 'material toon' })
    )

    const clothMaterial = (findNamedChild(scene, 'cloth') as THREE.Mesh).material
    expect(Array.isArray(clothMaterial)).toBe(true)
    expect(clothMaterial).toEqual([
      expect.objectContaining({ name: 'jkq toon' }),
      expect.objectContaining({ name: '罩袍 toon' }),
    ])

    expect(findNamedChild(scene, 'skin stylized outline')).toBeUndefined()
    expect(findNamedChild(scene, 'sclera stylized outline')).toBeUndefined()
    expect(findNamedChild(scene, 'face stylized outline')).toBeUndefined()
    expect(findNamedChild(scene, 'hair stylized outline')).toBeInstanceOf(THREE.SkinnedMesh)
    expect(findNamedChild(scene, 'leaf stylized outline')).toBeInstanceOf(THREE.Mesh)
    expect(bindSpy).toHaveBeenCalled()
    expect(scene.scale.x).toBeCloseTo(10.65 / 4)
    expect(scene.position.y).toBeCloseTo(5.325)
  })

  it('VRM 表情随心情切换，并稳定弹簧骨与视线目标', async () => {
    const raf = installRafQueue()
    const loader = captureLoader()
    const scene = createStyledModelScene()
    const expressions = createExpressionManager(['blink', 'blinkLeft', 'blinkRight', 'fun', 'relaxed', 'aa'])
    const gravityDir = { set: vi.fn() }
    const springBoneManager = {
      joints: [{ settings: { gravityDir } }],
      reset: vi.fn(),
    }
    const lookAt = { target: null as unknown }
    const poseSpy = vi.fn()
    const updateSpy = vi.fn()
    vi.spyOn(THREE.Clock.prototype, 'getElapsedTime').mockReturnValue(0.09)

    await renderFocusPage()
    fireEvent.click(screen.getByLabelText('和麦麦互动'))
    await act(async () => {})

    loader.finish({
      animations: [{}],
      scene,
      userData: {
        vrm: {
          scene,
          humanoid: { setNormalizedPose: poseSpy },
          expressionManager: expressions,
          springBoneManager,
          lookAt,
          update: updateSpy,
        },
      },
    })

    expect(lookAt.target).toBeInstanceOf(THREE.Object3D)
    expect(gravityDir.set).toHaveBeenCalledWith(0, -1, 0)
    expect(springBoneManager.reset).toHaveBeenCalled()
    expect(expressions.setValue).toHaveBeenCalledWith('fun', 0.68)
    expect(expressions.setValue).toHaveBeenCalledWith('aa', 0.12)
    expect(expressions.setValue).toHaveBeenCalledWith('blink', 0)

    const canvas = document.querySelector('[data-focus-model-canvas="true"]') as HTMLElement | null
    expect(canvas?.dataset.focusModelLoaded).toBe('true')

    act(() => {
      raf.flush()
    })
    expect(expressions.setValue).toHaveBeenCalledWith('blink', 1)
    expect(expressions.setValue).toHaveBeenCalledWith('blinkLeft', 0.75)
    expect(expressions.setValue).toHaveBeenCalledWith('blinkRight', 0.75)
    expect(updateSpy).toHaveBeenCalledWith(0.016)
    expect(poseSpy.mock.calls.length).toBeGreaterThan(1)

    fireEvent.click(screen.getByLabelText('和麦麦互动'))
    await act(async () => {})
    expressions.setValue.mockClear()
    act(() => {
      raf.flush()
    })
    expect(expressions.setValue).toHaveBeenCalledWith('relaxed', 0.34)

    fireEvent.click(screen.getByLabelText('和麦麦互动'))
    await act(async () => {})
    expressions.setValue.mockClear()
    act(() => {
      raf.flush()
    })
    expect(expressions.setValue).toHaveBeenCalledWith('relaxed', 0.16)
  })

  it('userData.vrm 缺少 scene 时按普通模型处理', async () => {
    gltfLoaderMocks.load.mockImplementation((_url: unknown, onLoad: (gltf: unknown) => void) => {
      onLoad({
        animations: [],
        scene: createFakeSceneNode(),
        userData: { vrm: { version: 1 } },
      })
    })

    await renderFocusPage()

    expect(vi.mocked(VRMUtils.rotateVRM0)).not.toHaveBeenCalled()
    const canvas = document.querySelector('[data-focus-model-canvas="true"]') as HTMLElement | null
    expect(canvas?.dataset.focusModelLoaded).toBe('true')
  })

  it('卸载后才加载成功：VRM 走 deepDispose，普通模型释放几何体与数组材质', async () => {
    const loader = captureLoader()
    const vrmScene = createStyledModelScene()
    const plainScene = createStyledModelScene()
    const leaf = findNamedChild(plainScene, 'leaf') as THREE.Mesh
    const cloth = findNamedChild(plainScene, 'cloth') as THREE.Mesh
    const geometryDispose = vi.spyOn(leaf.geometry, 'dispose')
    const leafDispose = vi.spyOn(leaf.material as THREE.Material, 'dispose')
    const clothDisposes = (cloth.material as THREE.Material[]).map((material) => vi.spyOn(material, 'dispose'))

    const vrmView = await renderFocusPage()
    vrmView.unmount()
    loader.finish({
      animations: [],
      scene: vrmScene,
      userData: {
        vrm: {
          scene: vrmScene,
          humanoid: { setNormalizedPose: vi.fn() },
          update: vi.fn(),
        },
      },
    })
    expect(vi.mocked(VRMUtils.deepDispose)).toHaveBeenCalledWith(vrmScene)
    expect(document.querySelector('[data-focus-model-canvas="true"]')).toBeNull()

    vi.mocked(VRMUtils.deepDispose).mockClear()
    const plainView = await renderFocusPage()
    plainView.unmount()
    loader.finish({ animations: [], scene: plainScene, userData: {} })
    expect(vi.mocked(VRMUtils.deepDispose)).not.toHaveBeenCalled()
    expect(geometryDispose).toHaveBeenCalled()
    expect(leafDispose).toHaveBeenCalled()
    clothDisposes.forEach((dispose) => expect(dispose).toHaveBeenCalled())
  })

  it('卸载后才加载失败不再记录错误', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const loader = captureLoader()
    const view = await renderFocusPage()
    view.unmount()

    loader.fail(new Error('来迟了'))

    expect(errorSpy).not.toHaveBeenCalled()
  })

  it('指针移动后动画帧更新相机，页面隐藏停止渲染并在可见时恢复', async () => {
    const raf = installRafQueue()
    const renderSpy = vi.spyOn(THREE.WebGLRenderer.prototype, 'render')
    const lookAtSpy = vi.spyOn(THREE.PerspectiveCamera.prototype, 'lookAt')
    const stopSpy = vi.spyOn(THREE.Clock.prototype, 'stop')
    const startSpy = vi.spyOn(THREE.Clock.prototype, 'start')
    const mixerUpdate = vi.spyOn(THREE.AnimationMixer.prototype, 'update')
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      width: 200,
      height: 200,
      top: 0,
      left: 0,
      right: 200,
      bottom: 200,
      x: 0,
      y: 0,
      toJSON() {
        return {}
      },
    } as DOMRect)

    gltfLoaderMocks.load.mockImplementation((_url: unknown, onLoad: (gltf: unknown) => void) => {
      onLoad({ animations: [{}], scene: createFakeSceneNode(), userData: {} })
    })

    await renderFocusPage()
    fireEvent.click(screen.getByLabelText('和麦麦互动'))
    await act(async () => {})

    renderSpy.mockClear()
    lookAtSpy.mockClear()
    fireEvent.pointerMove(window, { clientX: window.innerWidth, clientY: 0 })
    const sceneMount = document.querySelector('[aria-hidden="true"]') as HTMLElement
    fireEvent.pointerMove(sceneMount, { clientX: 200, clientY: 0 })

    act(() => {
      raf.flush()
    })
    expect(renderSpy).toHaveBeenCalled()
    expect(lookAtSpy).toHaveBeenCalled()
    expect(mixerUpdate).toHaveBeenCalled()
    const cameras = renderSpy.mock.calls.map(([, camera]) => camera as { position: { x: number } })
    expect(cameras.some((camera) => camera.position.x !== 0)).toBe(true)

    Object.defineProperty(document, 'hidden', { configurable: true, value: true })
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(stopSpy).toHaveBeenCalled()
    const rendersAfterHide = renderSpy.mock.calls.length
    act(() => {
      raf.flush()
    })
    expect(renderSpy.mock.calls.length).toBe(rendersAfterHide)

    delete (document as { hidden?: unknown }).hidden
    startSpy.mockClear()
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(startSpy).toHaveBeenCalled()
    act(() => {
      raf.flush()
    })
    expect(renderSpy.mock.calls.length).toBeGreaterThan(rendersAfterHide)
  })

  it('ResizeObserver 按挂载节点尺寸调用 setSize', async () => {
    const observers: ResizeObserverCallback[] = []
    class CapturingResizeObserver {
      constructor(private readonly callback: ResizeObserverCallback) {
        observers.push(callback)
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal('ResizeObserver', CapturingResizeObserver)

    const rect = {
      width: 0,
      height: 0,
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      x: 0,
      y: 0,
      toJSON() {
        return {}
      },
    }
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(() => rect as DOMRect)
    const setSize = vi.spyOn(THREE.WebGLRenderer.prototype, 'setSize')
    const updateProjection = vi.spyOn(THREE.PerspectiveCamera.prototype, 'updateProjectionMatrix')

    await renderFocusPage()
    setSize.mockClear()
    updateProjection.mockClear()
    rect.width = 800
    rect.height = 600
    rect.right = 800
    rect.bottom = 600

    act(() => {
      observers.forEach((callback) => {
        callback([], {} as ResizeObserver)
      })
    })

    expect(setSize).toHaveBeenCalledWith(800, 600, false)
    expect(updateProjection).toHaveBeenCalled()
  })
})
