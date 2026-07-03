import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { VRMHumanBoneName, VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm'
import {
  Expand,
  Minimize2,
  Moon,
  Pause,
  Play,
  RotateCcw,
  Send,
  Sprout,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import * as THREE from 'three'

import { Button } from '@/components/ui/button'
import { getChatStreams } from '@/lib/chat-management-api'
import { chatWsClient } from '@/lib/chat-ws-client'
import { DEFAULT_SETTINGS, getSetting } from '@/lib/settings-manager'
import { cn } from '@/lib/utils'

import type { GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js'
import type { ChangeEvent, FormEvent } from 'react'
import type { VRM, VRMPose } from '@pixiv/three-vrm'

const DEFAULT_MODEL_NAME = 'mai_vrc_0.9.vrm'
const DEFAULT_MODEL_URL = '/maimai-focus/mai_vrc_0.9.vrm'
const FOCUS_SESSION_ID = 'webui-focus-companion'
const LAYOUT_IMMERSIVE_EVENT = 'maibot-layout-immersive-change'
const FOCUS_COMPANION_STORAGE_KEY = 'maibot-focus-companion-state'
const DEFAULT_FOCUS_MINUTES = 25
const MIN_FOCUS_MINUTES = 1
const MAX_FOCUS_MINUTES = 240

type TimerMode = 'focus' | 'short' | 'long'
type CompanionMood = 'idle' | 'focus' | 'cheer' | 'listening'
type ModelKind = 'gltf' | 'vrm'
type ModelLoadState = 'idle' | 'loading' | 'ready' | 'error'
type SaplingKind = 'amber' | 'moss' | 'paper' | 'citrus'
type SaplingShape = 'twin' | 'triple' | 'lantern' | 'fruit'
type FocusCompanionStorage = {
  customFocusMinutes: number
  saplings: SaplingKind[]
  todayFocusDate: string
  todayFocusSeconds: number
}

const TIMER_MODE_SECONDS: Record<TimerMode, number> = {
  focus: DEFAULT_FOCUS_MINUTES * 60,
  short: 5 * 60,
  long: 15 * 60,
}

const MODE_ITEMS: Array<{ mode: TimerMode; label: string }> = [
  { mode: 'focus', label: '25' },
  { mode: 'short', label: '5' },
  { mode: 'long', label: '15' },
]

const MOOD_LINES: Record<CompanionMood, string> = {
  idle: '麦麦在这里。',
  focus: '安静推进就好。',
  cheer: '完成一段啦。',
  listening: '我听见了。',
}

const SAPLING_KINDS: Record<
  SaplingKind,
  {
    label: string
    description: string
    shape: SaplingShape
    stemClass: string
    leftLeafClass: string
    rightLeafClass: string
    accentClass: string
  }
> = {
  amber: {
    label: '琥珀树苗',
    description: '像一枚安静发亮的时间切片。',
    shape: 'twin',
    stemClass: 'bg-[#c24d24]',
    leftLeafClass: 'bg-[#c99a3e]',
    rightLeafClass: 'bg-[#f3e3cc]',
    accentClass: 'bg-[#c24d24]',
  },
  moss: {
    label: '苔光树苗',
    description: '在慢慢呼吸的绿意里扎根。',
    shape: 'triple',
    stemClass: 'bg-[#0a4550]',
    leftLeafClass: 'bg-[#f3e3cc]',
    rightLeafClass: 'bg-[#8fb28d]',
    accentClass: 'bg-[#0a4550]',
  },
  paper: {
    label: '纸灯树苗',
    description: '像桌边亮起的一片小纸灯。',
    shape: 'lantern',
    stemClass: 'bg-[#c99a3e]',
    leftLeafClass: 'bg-[#f3e3cc]',
    rightLeafClass: 'bg-[#f6d05f]',
    accentClass: 'bg-[#f6d05f]',
  },
  citrus: {
    label: '橙芽树苗',
    description: '把刚完成的专注收成一点暖橙色。',
    shape: 'fruit',
    stemClass: 'bg-[#c24d24]',
    leftLeafClass: 'bg-[#f6d05f]',
    rightLeafClass: 'bg-[#c99a3e]',
    accentClass: 'bg-[#c24d24]',
  },
}

const SAPLING_KIND_LIST = Object.keys(SAPLING_KINDS) as SaplingKind[]
const ENCOURAGEMENT_LINES = [
  '完成啦，今天的专注已经长出形状了。',
  '很好，这一段稳稳落地了。',
  '你刚刚认真守住了一小片时间。',
  '做得漂亮，给这段专注留一盏暖灯。',
]
const FOCUS_LOCK_CONTROL_SELECTOR = '[data-focus-lock-control="true"]'

const RETRO_ICON_BUTTON_CLASS =
  'focus-local-glass h-10 w-10 rounded-none border-0 text-[#0a4550] transition hover:bg-[#0a4550] hover:text-[#f3e3cc] disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent disabled:hover:text-[#0a4550] focus-visible:ring-2 focus-visible:ring-[#c99a3e] focus-visible:ring-offset-2 focus-visible:ring-offset-[#f3e3cc]'
const RETRO_PANEL_CLASS =
  'rounded-none border-4 border-[#0a4550] bg-[#f3e3cc] text-[#0a4550] shadow-none'
const RETRO_GLASS_SURFACE_CLASS =
  'focus-local-glass rounded-none border-0 text-[#0a4550]'

function clampFocusMinutes(minutes: number): number {
  if (!Number.isFinite(minutes)) {
    return DEFAULT_FOCUS_MINUTES
  }

  return Math.min(MAX_FOCUS_MINUTES, Math.max(MIN_FOCUS_MINUTES, Math.round(minutes)))
}

function normalizeSaplings(value: unknown): SaplingKind[] {
  if (Array.isArray(value)) {
    return value.filter((item): item is SaplingKind => SAPLING_KIND_LIST.includes(item as SaplingKind))
  }

  if (typeof value === 'number' && Number.isFinite(value)) {
    return Array.from({ length: Math.max(0, Math.floor(value)) }, (_, index) => SAPLING_KIND_LIST[index % SAPLING_KIND_LIST.length])
  }

  return []
}

function getTodayStorageDate(): string {
  return new Date().toISOString().slice(0, 10)
}

function normalizeTodayFocusSeconds(value: unknown): number {
  return Math.max(0, Math.floor(Number(value) || 0))
}

function getRandomSaplingKind(): SaplingKind {
  return SAPLING_KIND_LIST[Math.floor(Math.random() * SAPLING_KIND_LIST.length)]
}

function getRandomEncouragement(): string {
  return ENCOURAGEMENT_LINES[Math.floor(Math.random() * ENCOURAGEMENT_LINES.length)]
}

function getFullscreenTarget(): HTMLElement {
  return document.getElementById('main-content') ?? document.documentElement
}

function isFocusLockControlTarget(target: EventTarget | null): boolean {
  return target instanceof Element && Boolean(target.closest(FOCUS_LOCK_CONTROL_SELECTOR))
}

function readFocusCompanionStorage(): FocusCompanionStorage {
  const today = getTodayStorageDate()
  if (typeof window === 'undefined') {
    return { customFocusMinutes: DEFAULT_FOCUS_MINUTES, saplings: [], todayFocusDate: today, todayFocusSeconds: 0 }
  }

  try {
    const raw = window.localStorage.getItem(FOCUS_COMPANION_STORAGE_KEY)
    if (!raw) {
      return { customFocusMinutes: DEFAULT_FOCUS_MINUTES, saplings: [], todayFocusDate: today, todayFocusSeconds: 0 }
    }

    const parsed = JSON.parse(raw) as Partial<FocusCompanionStorage>
    const storedDate = String(parsed.todayFocusDate ?? today)
    const isToday = storedDate === today
    return {
      customFocusMinutes: clampFocusMinutes(Number(parsed.customFocusMinutes ?? DEFAULT_FOCUS_MINUTES)),
      saplings: normalizeSaplings(parsed.saplings),
      todayFocusDate: today,
      todayFocusSeconds: isToday ? normalizeTodayFocusSeconds(parsed.todayFocusSeconds) : 0,
    }
  } catch {
    return { customFocusMinutes: DEFAULT_FOCUS_MINUTES, saplings: [], todayFocusDate: today, todayFocusSeconds: 0 }
  }
}

function writeFocusCompanionStorage(nextState: FocusCompanionStorage): void {
  if (typeof window === 'undefined') {
    return
  }

  window.localStorage.setItem(
    FOCUS_COMPANION_STORAGE_KEY,
    JSON.stringify({
      customFocusMinutes: clampFocusMinutes(nextState.customFocusMinutes),
      saplings: normalizeSaplings(nextState.saplings),
      todayFocusDate: nextState.todayFocusDate || getTodayStorageDate(),
      todayFocusSeconds: normalizeTodayFocusSeconds(nextState.todayFocusSeconds),
    })
  )
}

function formatSeconds(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

function emitImmersiveChange(immersive: boolean): void {
  window.dispatchEvent(
    new CustomEvent(LAYOUT_IMMERSIVE_EVENT, {
      detail: { immersive },
    })
  )
}

function useFocusCompanionChat() {
  const [connected, setConnected] = useState(false)
  const [isTyping, setIsTyping] = useState(false)
  const [botName, setBotName] = useState('麦麦')
  const [latestLine, setLatestLine] = useState('今天也一起慢慢来。')

  useEffect(() => {
    let mounted = true

    const unsubscribeSession = chatWsClient.onSessionMessage(FOCUS_SESSION_ID, (message) => {
      if (!mounted) {
        return
      }

      const type = String(message.type ?? '')
      if (type === 'session_info') {
        const nextBotName = String(message.bot_name ?? '').trim()
        if (nextBotName) {
          setBotName(nextBotName)
        }
        return
      }

      if (type === 'typing') {
        setIsTyping(message.is_typing === true)
        return
      }

      if (type === 'bot_message') {
        const content = String(message.content ?? '').trim()
        if (content) {
          setLatestLine(content)
        }
        setIsTyping(false)
        return
      }

      if (type === 'history' && Array.isArray(message.messages)) {
        const latestBotMessage = [...message.messages]
          .reverse()
          .find((item) => item && typeof item === 'object' && 'is_bot' in item && item.is_bot)
        if (latestBotMessage && typeof latestBotMessage === 'object' && 'content' in latestBotMessage) {
          const content = String(latestBotMessage.content ?? '').trim()
          if (content) {
            setLatestLine(content)
          }
        }
        setIsTyping(false)
      }
    })

    const unsubscribeConnection = chatWsClient.onConnectionChange((nextConnected) => {
      if (mounted) {
        setConnected(nextConnected)
      }
    })

    chatWsClient
      .openSession(FOCUS_SESSION_ID, {
        client: { type: 'webui', name: 'MaiBot WebUI' },
        user_id: 'webui_focus_user',
        user_name: '专注中的你',
        platform: 'webui',
        group_name: '麦麦的专注房间',
        group_id: 'webui_focus_room',
      })
      .then(() => {
        if (mounted) {
          setConnected(true)
        }
      })
      .catch((error) => {
        console.error('专注陪伴会话打开失败:', error)
        if (mounted) {
          setConnected(false)
          setLatestLine('麦麦会话暂时没有连上。')
        }
      })

    return () => {
      mounted = false
      unsubscribeSession()
      unsubscribeConnection()
      void chatWsClient.closeSession(FOCUS_SESSION_ID)
    }
  }, [])

  const send = useCallback(
    async (content: string, options: { showTyping?: boolean } = {}) => {
      const showTyping = options.showTyping ?? true
      if (showTyping) {
        setIsTyping(true)
      }
      try {
        await chatWsClient.sendMessage(FOCUS_SESSION_ID, content, '专注中的你')
      } catch (error) {
        console.error('专注陪伴消息发送失败:', error)
        if (showTyping) {
          setIsTyping(false)
          setLatestLine('发送没有成功，先继续专注。')
        }
      }
    },
    []
  )

  const sayLocal = useCallback((content: string) => {
    setLatestLine(content)
    setIsTyping(false)
  }, [])

  return { botName, connected, isTyping, latestLine, sayLocal, send }
}

interface FocusThreeSceneProps {
  mood: CompanionMood
  progress: number
  running: boolean
}

function FocusThreeScene({ mood, progress, running }: FocusThreeSceneProps) {
  const mountRef = useRef<HTMLDivElement | null>(null)
  const stateRef = useRef({ mood, progress, running })

  useEffect(() => {
    stateRef.current = { mood, progress, running }
  }, [mood, progress, running])

  useEffect(() => {
    const mount = mountRef.current
    if (!mount) {
      return
    }

    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(44, 1, 0.1, 100)
    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true })
    const clock = new THREE.Clock()
    const targetPointer = new THREE.Vector2(0, 0)
    const room = new THREE.Group()
    const papers: THREE.Mesh[] = []
    const bars: THREE.Mesh[] = []

    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.shadowMap.enabled = true
    renderer.shadowMap.type = THREE.PCFSoftShadowMap
    renderer.domElement.dataset.focusSceneCanvas = 'true'
    renderer.domElement.className = 'absolute inset-0 h-full w-full'
    mount.appendChild(renderer.domElement)

    camera.position.set(0, 1.25, 7.2)
    scene.add(room)
    scene.add(new THREE.AmbientLight(0xfff4e6, 0.62))

    const keyLight = new THREE.DirectionalLight(0xffd7a4, 1.18)
    keyLight.position.set(-3.5, 5.5, 3)
    keyLight.castShadow = true
    keyLight.shadow.mapSize.set(1024, 1024)
    keyLight.shadow.camera.near = 0.5
    keyLight.shadow.camera.far = 12
    scene.add(keyLight)

    const rimLight = new THREE.DirectionalLight(0x7ec3ba, 0.48)
    rimLight.position.set(4, 3.2, -3)
    scene.add(rimLight)

    const railMaterial = new THREE.MeshStandardMaterial({
      color: 0x0a4550,
      metalness: 0.18,
      roughness: 0.52,
      transparent: true,
      opacity: 0.24,
    })
    for (let index = 0; index < 14; index += 1) {
      const bar = new THREE.Mesh(new THREE.BoxGeometry(0.055, 1, 0.055), railMaterial)
      bar.position.set(-5.2 + index * 0.8, -0.76, -2.85)
      bar.scale.y = 0.5 + (index % 4) * 0.14
      bars.push(bar)
      room.add(bar)
    }

    const paperPalette = [0x0a4550, 0xc24d24, 0xc99a3e, 0x0a4550]
    for (let index = 0; index < 22; index += 1) {
      const material = new THREE.MeshBasicMaterial({
        color: paperPalette[index % paperPalette.length],
        side: THREE.DoubleSide,
        transparent: true,
        opacity: index % 4 === 2 ? 0.82 : 0.72,
      })
      const paper = new THREE.Mesh(
        new THREE.PlaneGeometry(index % 4 === 0 ? 0.72 : 0.44, index % 4 === 0 ? 0.16 : 0.26),
        material
      )
      paper.position.set(
        -5.25 + (index % 8) * 1.52,
        -0.36 + Math.floor(index / 8) * 0.82 + (index % 2) * 0.24,
        -2.7 + (index % 4) * 0.16
      )
      paper.rotation.set(index * 0.17, index * 0.23, index * 0.11)
      papers.push(paper)
      room.add(paper)
    }

    const archMaterial = new THREE.MeshStandardMaterial({
      color: 0x0a4550,
      metalness: 0.12,
      roughness: 0.5,
      transparent: true,
      opacity: 0.22,
    })
    const arch = new THREE.Mesh(new THREE.TorusGeometry(1.7, 0.025, 12, 96, Math.PI), archMaterial)
    arch.position.set(2.75, 0.04, -2.55)
    arch.rotation.z = Math.PI
    room.add(arch)

    const handlePointerMove = (event: PointerEvent) => {
      const rect = mount.getBoundingClientRect()
      targetPointer.x = ((event.clientX - rect.left) / rect.width - 0.5) * 2
      targetPointer.y = ((event.clientY - rect.top) / rect.height - 0.5) * 2
    }

    const resize = () => {
      const { width, height } = mount.getBoundingClientRect()
      camera.aspect = Math.max(width, 1) / Math.max(height, 1)
      camera.updateProjectionMatrix()
      renderer.setSize(width, height, false)
    }

    const resizeObserver = new ResizeObserver(resize)
    resizeObserver.observe(mount)
    mount.addEventListener('pointermove', handlePointerMove)
    resize()

    let animationFrame = 0
    const animate = () => {
      const elapsed = clock.getElapsedTime()
      const current = stateRef.current
      const energy = current.running ? 0.55 + current.progress * 0.35 : 0.28
      const moodLift = current.mood === 'cheer' ? 0.16 : current.mood === 'listening' ? 0.08 : 0

      room.rotation.y += (targetPointer.x * 0.075 - room.rotation.y) * 0.035
      room.rotation.x += (-targetPointer.y * 0.035 - room.rotation.x) * 0.035
      camera.position.x += (targetPointer.x * 0.34 - camera.position.x) * 0.025
      camera.position.y += (1.25 - targetPointer.y * 0.12 - camera.position.y) * 0.025
      camera.lookAt(0, 0.1, -1.2)

      bars.forEach((bar, index) => {
        const wave = 0.56 + Math.sin(elapsed * 1.4 + index * 0.7) * 0.14 + energy * 0.42
        bar.scale.y = wave
        bar.position.y = -1.22 + wave * 0.54 + moodLift
      })

      papers.forEach((paper, index) => {
        paper.position.y += Math.sin(elapsed * 0.62 + index) * 0.0011
        paper.rotation.y += 0.001 + energy * 0.0007
        paper.rotation.z += Math.sin(elapsed + index) * 0.0005
      })

      arch.rotation.y = Math.sin(elapsed * 0.38) * 0.08
      renderer.render(scene, camera)
      animationFrame = requestAnimationFrame(animate)
    }
    animate()

    return () => {
      cancelAnimationFrame(animationFrame)
      resizeObserver.disconnect()
      mount.removeEventListener('pointermove', handlePointerMove)
      mount.removeChild(renderer.domElement)
      scene.traverse((object) => {
        if (object instanceof THREE.Mesh) {
          object.geometry.dispose()
          const material = object.material
          if (Array.isArray(material)) {
            material.forEach((item) => item.dispose())
          } else {
            material.dispose()
          }
        }
      })
      renderer.dispose()
    }
  }, [])

  return <div ref={mountRef} aria-hidden="true" className="absolute inset-0" />
}

interface FocusModelViewerProps {
  kind: ModelKind
  mood: CompanionMood
  modelUrl: string
  onLoadStateChange: (state: ModelLoadState) => void
}

function disposeObject3D(object: THREE.Object3D): void {
  object.traverse((node) => {
    if (node instanceof THREE.Mesh || node instanceof THREE.SkinnedMesh) {
      node.geometry.dispose()
      const material = node.material
      if (Array.isArray(material)) {
        material.forEach((item) => item.dispose())
      } else {
        material.dispose()
      }
    }
  })
}

function createBinaryToonGradient(): THREE.DataTexture {
  const colors = new Uint8Array([84, 84, 84, 255, 255, 255, 255, 255])
  const texture = new THREE.DataTexture(colors, 2, 1, THREE.RGBAFormat)
  texture.magFilter = THREE.NearestFilter
  texture.minFilter = THREE.NearestFilter
  texture.needsUpdate = true
  return texture
}

function createSkinToonGradient(): THREE.DataTexture {
  const colors = new Uint8Array([198, 154, 144, 255, 255, 226, 214, 255])
  const texture = new THREE.DataTexture(colors, 2, 1, THREE.RGBAFormat)
  texture.magFilter = THREE.NearestFilter
  texture.minFilter = THREE.NearestFilter
  texture.needsUpdate = true
  return texture
}

function getMaterialMap(material: THREE.Material): THREE.Texture | null {
  return 'map' in material && material.map instanceof THREE.Texture ? material.map : null
}

function getMaterialColor(material: THREE.Material): THREE.Color {
  return 'color' in material && material.color instanceof THREE.Color
    ? material.color.clone()
    : new THREE.Color(0xffefe2)
}

function getMaterialSignature(mesh: THREE.Mesh | THREE.SkinnedMesh, materials: THREE.Material[]): string {
  return `${mesh.name} ${materials.map((material) => material.name).join(' ')}`.toLowerCase()
}

function shouldSkipOutline(signature: string): boolean {
  return /皮肤|眼|eye|highlight|口腔|舌头|脸/.test(signature)
}

function getStylizedMaterialColor(source: THREE.Material): THREE.Color {
  const name = source.name.toLowerCase()

  if (/叶|leaf|clover|三叶草|四叶草|草/.test(name)) {
    return new THREE.Color(0x63b72f)
  }

  if (/深色毛发|眉|eyebrow|brow/.test(name)) {
    return new THREE.Color(0x5a3424)
  }

  if (/皮肤/.test(name)) {
    return new THREE.Color(0xffc8b6)
  }

  if (/眼白/.test(name)) {
    return new THREE.Color(0xf6eee8)
  }

  if (/eye|眼/.test(name)) {
    return new THREE.Color(0x6f8fd8)
  }

  if (/头发|毛发/.test(name)) {
    return new THREE.Color(0xffa165)
  }

  if (/jkq|罩袍|内衬|束腰|裙|腰带/.test(name)) {
    return getMaterialColor(source).offsetHSL(0, 0.18, -0.08)
  }

  return getMaterialColor(source).offsetHSL(0, 0.1, -0.04)
}

function shouldUseSoftFaceMaterial(signature: string): boolean {
  return /眼白|eye|highlight|口腔|舌头/.test(signature)
}

function shouldUseLeafMaterial(material: THREE.Material): boolean {
  return /叶|leaf|clover|三叶草|四叶草|草/.test(material.name.toLowerCase())
}

function shouldUseSkinMaterial(material: THREE.Material): boolean {
  return /皮肤|脸/.test(material.name.toLowerCase())
}

function createSoftFaceMaterial(source: THREE.Material): THREE.MeshBasicMaterial {
  const softMaterial = new THREE.MeshBasicMaterial({
    color: getStylizedMaterialColor(source).offsetHSL(0, 0.08, 0.08),
    map: getMaterialMap(source),
    side: source.side,
    transparent: source.transparent,
    opacity: source.opacity,
    alphaTest: source.alphaTest,
    toneMapped: false,
  })

  softMaterial.name = `${source.name || 'material'} soft face`
  softMaterial.needsUpdate = true
  return softMaterial
}

function createSkinToonMaterial(source: THREE.Material, gradientMap: THREE.Texture): THREE.MeshToonMaterial {
  const skinMaterial = new THREE.MeshToonMaterial({
    color: new THREE.Color(0xffd4c6),
    emissive: new THREE.Color(0x4a2118),
    emissiveIntensity: 0.08,
    gradientMap,
    map: getMaterialMap(source),
    side: source.side,
    transparent: source.transparent,
    opacity: source.opacity,
    alphaTest: source.alphaTest,
  })

  skinMaterial.name = `${source.name || 'material'} skin toon`
  skinMaterial.needsUpdate = true
  return skinMaterial
}

function createToonMaterial(source: THREE.Material, gradientMap: THREE.Texture): THREE.MeshToonMaterial {
  const toonMaterial = new THREE.MeshToonMaterial({
    color: getStylizedMaterialColor(source),
    emissive: /皮肤/.test(source.name.toLowerCase()) ? new THREE.Color(0x2a120d) : new THREE.Color(0x000000),
    emissiveIntensity: /皮肤/.test(source.name.toLowerCase()) ? 0.06 : 0,
    gradientMap,
    map: getMaterialMap(source),
    side: source.side,
    transparent: source.transparent,
    opacity: source.opacity,
    alphaTest: source.alphaTest,
  })

  toonMaterial.name = `${source.name || 'material'} toon`
  toonMaterial.needsUpdate = true
  return toonMaterial
}

function applyToonAndOutlineStyle(model: THREE.Object3D): void {
  const gradientMap = createBinaryToonGradient()
  const skinGradientMap = createSkinToonGradient()
  const meshes: Array<THREE.Mesh | THREE.SkinnedMesh> = []

  model.traverse((node) => {
    if (node instanceof THREE.Mesh || node instanceof THREE.SkinnedMesh) {
      node.castShadow = true
      node.receiveShadow = true
      meshes.push(node)
    }
  })

  meshes.forEach((mesh) => {
    const sourceMaterials = Array.isArray(mesh.material) ? mesh.material : [mesh.material]
    const materialSignature = getMaterialSignature(mesh, sourceMaterials)
    const nextMaterials = sourceMaterials.map((material) => {
      if (shouldUseLeafMaterial(material)) {
        return createToonMaterial(material, gradientMap)
      }

      if (shouldUseSkinMaterial(material)) {
        return createSkinToonMaterial(material, skinGradientMap)
      }

      if (shouldUseSoftFaceMaterial(material.name.toLowerCase())) {
        return createSoftFaceMaterial(material)
      }

      return createToonMaterial(material, gradientMap)
    })
    mesh.material = Array.isArray(mesh.material) ? nextMaterials : nextMaterials[0]

    if (shouldSkipOutline(materialSignature)) {
      return
    }

    const outlineMaterial = new THREE.MeshBasicMaterial({
      color: 0x2a2526,
      side: THREE.BackSide,
      transparent: true,
      opacity: 0.78,
      depthWrite: false,
    })
    const outline =
      mesh instanceof THREE.SkinnedMesh
        ? new THREE.SkinnedMesh(mesh.geometry, outlineMaterial)
        : new THREE.Mesh(mesh.geometry, outlineMaterial)

    outline.name = `${mesh.name || 'mesh'} stylized outline`
    outline.position.copy(mesh.position)
    outline.rotation.copy(mesh.rotation)
    outline.quaternion.copy(mesh.quaternion)
    outline.scale.copy(mesh.scale).multiplyScalar(1.022)
    outline.renderOrder = mesh.renderOrder - 1
    outline.castShadow = false
    outline.receiveShadow = false
    outline.frustumCulled = mesh.frustumCulled

    if (outline instanceof THREE.SkinnedMesh && mesh instanceof THREE.SkinnedMesh) {
      outline.bind(mesh.skeleton, mesh.bindMatrix)
    }

    mesh.parent?.add(outline)
  })
}

function normalizeModelToStage(model: THREE.Object3D, targetHeight: number): void {
  const box = new THREE.Box3().setFromObject(model)
  const size = new THREE.Vector3()
  const center = new THREE.Vector3()
  box.getSize(size)
  box.getCenter(center)

  const height = size.y || Math.max(size.x, size.z, 1)
  const scale = targetHeight / height
  model.position.sub(center)
  model.scale.setScalar(scale)
  model.position.y += (size.y * scale) / 2
}

function getVRMFromGLTF(gltf: GLTF): VRM | null {
  const vrm = gltf.userData.vrm
  return vrm && typeof vrm === 'object' && 'scene' in vrm ? (vrm as VRM) : null
}

function quaternionFromEuler(x: number, y: number, z: number): [number, number, number, number] {
  const quaternion = new THREE.Quaternion().setFromEuler(new THREE.Euler(x, y, z, 'XYZ'))
  return [quaternion.x, quaternion.y, quaternion.z, quaternion.w]
}

function buildCompanionVRMPose(pointer: THREE.Vector2, elapsed: number, playing: boolean): VRMPose {
  const yaw = THREE.MathUtils.clamp(pointer.x * 0.34, -0.34, 0.34)
  const pitch = THREE.MathUtils.clamp(pointer.y * 0.28 + 0.12, -0.18, 0.34)
  const breath = playing ? Math.sin(elapsed * 1.35) * 0.012 : 0
  const sideYaw = -0.46
  const screenCounterYaw = 0.14

  return {
    [VRMHumanBoneName.Hips]: {
      rotation: quaternionFromEuler(-0.04, sideYaw, 0.04),
    },
    [VRMHumanBoneName.Spine]: {
      rotation: quaternionFromEuler(0.03, 0.14, -0.03),
    },
    [VRMHumanBoneName.Chest]: {
      rotation: quaternionFromEuler(0.035 + breath, 0.12, -0.05),
    },
    [VRMHumanBoneName.UpperChest]: {
      rotation: quaternionFromEuler(0.02 + breath * 0.7, 0.08, -0.03),
    },
    [VRMHumanBoneName.Neck]: {
      rotation: quaternionFromEuler(pitch * 0.3, screenCounterYaw * 0.35 + yaw * 0.24, 0.02),
    },
    [VRMHumanBoneName.Head]: {
      rotation: quaternionFromEuler(pitch * 0.82, screenCounterYaw * 0.65 + yaw * 0.72, -yaw * 0.05),
    },
    [VRMHumanBoneName.LeftShoulder]: {
      rotation: quaternionFromEuler(0, 0, -0.04),
    },
    [VRMHumanBoneName.RightShoulder]: {
      rotation: quaternionFromEuler(0, 0, 0.04),
    },
    [VRMHumanBoneName.LeftUpperArm]: {
      rotation: quaternionFromEuler(0.08, 0.04, -1.05),
    },
    [VRMHumanBoneName.RightUpperArm]: {
      rotation: quaternionFromEuler(0.08, -0.04, 1.05),
    },
    [VRMHumanBoneName.LeftLowerArm]: {
      rotation: quaternionFromEuler(0.08, 0, -0.08),
    },
    [VRMHumanBoneName.RightLowerArm]: {
      rotation: quaternionFromEuler(0.08, 0, 0.08),
    },
    [VRMHumanBoneName.LeftHand]: {
      rotation: quaternionFromEuler(0, 0.02, 0),
    },
    [VRMHumanBoneName.RightHand]: {
      rotation: quaternionFromEuler(0, -0.02, 0),
    },
  }
}

function stabilizeVRMSpringBones(vrm: VRM): void {
  vrm.springBoneManager?.joints.forEach((joint) => {
    joint.settings.gravityDir.set(0, -1, 0)
  })
  vrm.springBoneManager?.reset()
}

function setFirstAvailableExpression(vrm: VRM, expressionNames: string[], value: number): void {
  const manager = vrm.expressionManager
  if (!manager) {
    return
  }

  const expressionName = expressionNames.find((name) => manager.getExpression(name))
  if (expressionName) {
    manager.setValue(expressionName, value)
  }
}

function applyCompanionVRMExpression(vrm: VRM, mood: CompanionMood, elapsed: number, playing: boolean): void {
  const manager = vrm.expressionManager
  if (!manager) {
    return
  }

  const controlledExpressions = [
    'happy',
    'relaxed',
    'surprised',
    'sad',
    'angry',
    'aa',
    'ih',
    'ou',
    'ee',
    'oh',
    'blink',
    'blinkLeft',
    'blinkRight',
    'neutral',
  ]
  controlledExpressions.forEach((name) => {
    if (manager.getExpression(name)) {
      manager.setValue(name, 0)
    }
  })

  const blinkPhase = elapsed % 4.3
  const blink = playing && blinkPhase < 0.18 ? Math.sin((blinkPhase / 0.18) * Math.PI) : 0
  setFirstAvailableExpression(vrm, ['blink'], blink)
  setFirstAvailableExpression(vrm, ['blinkLeft'], blink * 0.75)
  setFirstAvailableExpression(vrm, ['blinkRight'], blink * 0.75)

  if (mood === 'cheer') {
    setFirstAvailableExpression(vrm, ['happy', 'joy', 'fun'], 0.68)
    setFirstAvailableExpression(vrm, ['aa'], 0.12 + Math.sin(elapsed * 5.2) * 0.04)
    return
  }

  if (mood === 'listening') {
    setFirstAvailableExpression(vrm, ['relaxed', 'fun'], 0.34)
    setFirstAvailableExpression(vrm, ['aa'], 0.04 + Math.sin(elapsed * 2.8) * 0.025)
    return
  }

  if (mood === 'focus') {
    setFirstAvailableExpression(vrm, ['relaxed', 'neutral'], 0.16)
    return
  }

  setFirstAvailableExpression(vrm, ['relaxed', 'neutral'], 0.22)
}

function createForegroundStudyScene(): THREE.Group {
  const group = new THREE.Group()
  const deskMaterial = new THREE.MeshStandardMaterial({
    color: 0xf3e3cc,
    metalness: 0.03,
    roughness: 0.7,
  })
  const orangeMaterial = new THREE.MeshStandardMaterial({
    color: 0xc24d24,
    metalness: 0.1,
    roughness: 0.54,
  })
  const greenMaterial = new THREE.MeshStandardMaterial({
    color: 0x0a4550,
    metalness: 0.08,
    roughness: 0.62,
  })
  const glowMaterial = new THREE.MeshStandardMaterial({
    color: 0xf0d2a0,
    emissive: 0xc24d24,
    emissiveIntensity: 0.28,
    roughness: 0.52,
  })
  const tabletop = new THREE.Mesh(new THREE.BoxGeometry(6.2, 0.22, 1.08), deskMaterial)
  tabletop.position.set(0.12, 0.02, 2.38)
  tabletop.castShadow = true
  tabletop.receiveShadow = true
  group.add(tabletop)

  const frontLip = new THREE.Mesh(new THREE.BoxGeometry(6.26, 0.14, 0.12), greenMaterial)
  frontLip.position.set(0.12, 0.18, 3.02)
  frontLip.castShadow = true
  frontLip.receiveShadow = true
  group.add(frontLip)

  for (const x of [-2.55, 2.8]) {
    const leg = new THREE.Mesh(new THREE.BoxGeometry(0.18, 1.08, 0.18), orangeMaterial)
    leg.position.set(x, -0.58, 2.86)
    leg.castShadow = true
    leg.receiveShadow = true
    group.add(leg)
  }

  const lampGroup = new THREE.Group()
  lampGroup.position.set(-2.28, 0.2, 2.86)
  const lampBase = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.39, 0.08, 8), greenMaterial)
  lampBase.castShadow = true
  lampBase.receiveShadow = true
  lampGroup.add(lampBase)

  const lampArm = new THREE.Mesh(new THREE.BoxGeometry(0.08, 0.88, 0.08), orangeMaterial)
  lampArm.position.set(0.08, 0.48, -0.02)
  lampArm.rotation.z = -0.28
  lampArm.castShadow = true
  lampArm.receiveShadow = true
  lampGroup.add(lampArm)

  const lampShade = new THREE.Mesh(new THREE.ConeGeometry(0.45, 0.42, 8, 1, true), glowMaterial)
  lampShade.position.set(0.28, 0.98, -0.04)
  lampShade.rotation.set(0.08, 0, -0.2)
  lampShade.castShadow = true
  lampShade.receiveShadow = true
  lampGroup.add(lampShade)
  group.add(lampGroup)

  const lampLight = new THREE.PointLight(0xffbd7a, 2.7, 4.4, 1.55)
  lampLight.position.set(-1.94, 1.3, 2.86)
  lampLight.castShadow = true
  lampLight.shadow.mapSize.set(1024, 1024)
  lampLight.shadow.bias = -0.0008
  group.add(lampLight)

  const deskButtonMaterial = new THREE.MeshStandardMaterial({
    color: 0xc24d24,
    metalness: 0.02,
    roughness: 0.58,
  })
  for (let index = 0; index < 3; index += 1) {
    const control = new THREE.Mesh(new THREE.BoxGeometry(0.36, 0.08, 0.22), deskButtonMaterial)
    control.position.set(0.5 + index * 0.48, 0.22, 2.1)
    control.rotation.y = -0.16
    control.castShadow = true
    control.receiveShadow = true
    group.add(control)
  }

  group.traverse((object) => {
    object.renderOrder = 2
  })

  return group
}

function FocusModelViewer({ kind, mood, modelUrl, onLoadStateChange }: FocusModelViewerProps) {
  const mountRef = useRef<HTMLDivElement | null>(null)
  const stateRef = useRef({ mood, playing: true })

  useEffect(() => {
    stateRef.current = { mood, playing: true }
  }, [mood])

  useEffect(() => {
    const mount = mountRef.current
    if (!mount) {
      return
    }

    onLoadStateChange('loading')

    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100)
    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true })
    const clock = new THREE.Clock()
    const stage = new THREE.Group()
    const gazeTarget = new THREE.Object3D()
    const foregroundStudyScene = createForegroundStudyScene()
    const pointer = new THREE.Vector2(0, 0)
    const posePointer = new THREE.Vector2(0, 0)

    let animationFrame = 0
    let mixer: THREE.AnimationMixer | null = null
    let activeModel: THREE.Object3D | null = null
    let activeVRM: VRM | null = null
    let modelBaseY = 0
    let modelBaseX = 0
    let disposed = false

    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = 0.98
    renderer.shadowMap.enabled = true
    renderer.shadowMap.type = THREE.PCFSoftShadowMap
    renderer.domElement.dataset.focusModelCanvas = 'true'
    renderer.domElement.className = 'absolute inset-0 h-full w-full'
    mount.appendChild(renderer.domElement)

    camera.position.set(0, 1.5, 9.6)
    gazeTarget.position.set(0, 1.5, 2.2)
    scene.add(stage)
    scene.add(gazeTarget)
    stage.add(foregroundStudyScene)
    scene.add(new THREE.HemisphereLight(0xfff2df, 0x5d7168, 1.42))

    const keyLight = new THREE.DirectionalLight(0xffd2a8, 1.85)
    keyLight.position.set(-2.4, 4.6, 3.5)
    keyLight.castShadow = true
    keyLight.shadow.mapSize.set(1024, 1024)
    keyLight.shadow.camera.near = 0.5
    keyLight.shadow.camera.far = 12
    keyLight.shadow.camera.left = -4
    keyLight.shadow.camera.right = 4
    keyLight.shadow.camera.top = 4
    keyLight.shadow.camera.bottom = -4
    keyLight.shadow.bias = -0.001
    scene.add(keyLight)

    const faceTarget = new THREE.Object3D()
    faceTarget.position.set(0, 1.62, 0)
    scene.add(faceTarget)

    const faceLight = new THREE.DirectionalLight(0xffdbc2, 2.3)
    faceLight.position.set(0, 1.86, 4.4)
    faceLight.target = faceTarget
    scene.add(faceLight)

    const rimLight = new THREE.DirectionalLight(0x93cbc0, 1.35)
    rimLight.position.set(3.2, 2.4, -2.6)
    scene.add(rimLight)

    const characterShadow = new THREE.Mesh(
      new THREE.CircleGeometry(1, 48),
      new THREE.MeshBasicMaterial({
        color: 0x062f36,
        depthWrite: false,
        opacity: 0.28,
        transparent: true,
      })
    )
    characterShadow.position.set(0.18, -1.46, 0.38)
    characterShadow.rotation.x = -Math.PI / 2
    characterShadow.scale.set(1.65, 0.42, 1)
    stage.add(characterShadow)

    const loader = new GLTFLoader()
    if (kind === 'vrm') {
      loader.register((parser) => new VRMLoaderPlugin(parser))
    }

    const handlePointerMove = (event: PointerEvent) => {
      const width = window.innerWidth || document.documentElement.clientWidth || 1
      const height = window.innerHeight || document.documentElement.clientHeight || 1
      pointer.x = THREE.MathUtils.clamp((event.clientX / width - 0.5) * 2, -1, 1)
      pointer.y = THREE.MathUtils.clamp((event.clientY / height - 0.5) * 2, -1, 1)
    }

    const resize = () => {
      const { width, height } = mount.getBoundingClientRect()
      camera.aspect = Math.max(width, 1) / Math.max(height, 1)
      camera.updateProjectionMatrix()
      renderer.setSize(width, height, false)
    }

    const resizeObserver = new ResizeObserver(resize)
    resizeObserver.observe(mount)
    window.addEventListener('pointermove', handlePointerMove, { passive: true })
    resize()

    loader.load(
      modelUrl,
      (gltf) => {
        const vrm = kind === 'vrm' ? getVRMFromGLTF(gltf) : null
        const model = vrm?.scene ?? gltf.scene

        if (disposed) {
          if (vrm) {
            VRMUtils.deepDispose(vrm.scene)
          } else {
            disposeObject3D(model)
          }
          return
        }

        if (vrm) {
          VRMUtils.rotateVRM0(vrm)
        }
        normalizeModelToStage(model, vrm ? 10.65 : 10.0)
        applyToonAndOutlineStyle(model)
        stage.add(model)
        activeModel = model
        activeVRM = vrm
        modelBaseX = model.position.x + (vrm ? 0.42 : 0.34)
        modelBaseY = model.position.y - (vrm ? 11.72 : 8.9)

        if (activeVRM?.lookAt) {
          activeVRM.lookAt.target = gazeTarget
        }
        activeVRM?.humanoid.setNormalizedPose(buildCompanionVRMPose(pointer, 0, true))
        if (activeVRM) {
          applyCompanionVRMExpression(activeVRM, stateRef.current.mood, 0, true)
        }
        if (activeVRM) {
          stabilizeVRMSpringBones(activeVRM)
        }

        if (gltf.animations.length > 0) {
          mixer = new THREE.AnimationMixer(model)
          gltf.animations.forEach((clip) => {
            mixer?.clipAction(clip).play()
          })
        }

        renderer.domElement.dataset.focusModelLoaded = 'true'
        onLoadStateChange('ready')
      },
      undefined,
      (error) => {
        if (disposed) {
          return
        }

        console.error('专注陪伴模型加载失败:', error)
        onLoadStateChange('error')
      }
    )

    const animate = () => {
      const delta = clock.getDelta()
      const elapsed = clock.getElapsedTime()
      const { mood: currentMood, playing: currentPlaying } = stateRef.current

      const bodyTurn = activeVRM ? 0.045 : 0.16
      const bodyPitch = activeVRM ? 0.018 : 0.055
      stage.rotation.y += (pointer.x * bodyTurn - stage.rotation.y) * 0.045
      stage.rotation.x += (-pointer.y * bodyPitch - stage.rotation.x) * 0.04
      posePointer.set(
        pointer.x + Math.sin(elapsed * 0.56) * 0.018,
        pointer.y + Math.sin(elapsed * 0.72 + 1.3) * 0.028
      )
      gazeTarget.position.x += (posePointer.x * 0.9 - gazeTarget.position.x) * 0.04
      gazeTarget.position.y += (1.5 - posePointer.y * 0.34 - gazeTarget.position.y) * 0.04
      camera.position.x += (pointer.x * 0.16 - camera.position.x) * 0.035
      faceLight.position.x += (camera.position.x * 0.4 - faceLight.position.x) * 0.04
      camera.lookAt(0, 1.62, 0)

      if (activeModel) {
        activeModel.position.x += (modelBaseX - activeModel.position.x) * 0.12
        activeModel.position.y += (modelBaseY - activeModel.position.y) * 0.12
      }

      if (activeVRM) {
        activeVRM.humanoid.setNormalizedPose(buildCompanionVRMPose(posePointer, elapsed, currentPlaying))
        applyCompanionVRMExpression(activeVRM, currentMood, elapsed, currentPlaying)
        activeVRM.update(currentPlaying ? delta : 0)
      }

      if (currentPlaying) {
        mixer?.update(delta)
      }

      renderer.render(scene, camera)
      animationFrame = requestAnimationFrame(animate)
    }
    animate()

    return () => {
      disposed = true
      cancelAnimationFrame(animationFrame)
      resizeObserver.disconnect()
      window.removeEventListener('pointermove', handlePointerMove)
      mount.removeChild(renderer.domElement)
      if (activeVRM) {
        VRMUtils.deepDispose(activeVRM.scene)
      } else if (activeModel) {
        disposeObject3D(activeModel)
      }
      disposeObject3D(foregroundStudyScene)
      renderer.dispose()
    }
  }, [kind, modelUrl, onLoadStateChange])

  return (
    <div
      ref={mountRef}
      className="pointer-events-none absolute inset-y-[-18%] inset-x-[-34%] sm:inset-y-[-14%] sm:inset-x-[-28%]"
    />
  )
}

interface TimerRingProps {
  progress: number
  secondsLeft: number
}

function TimerRing({ progress, secondsLeft }: TimerRingProps) {
  const radius = 98
  const accentRadius = 114
  const circumference = 2 * Math.PI * radius
  const offset = circumference * (1 - progress)
  const accentCircumference = 2 * Math.PI * accentRadius

  return (
    <div className="relative grid h-[17rem] w-[17rem] place-items-center">
      <svg viewBox="0 0 244 244" className="absolute inset-0 h-full w-full -rotate-90">
        <circle
          cx="122"
          cy="122"
          r={accentRadius}
          fill="none"
          stroke="#0a4550"
          strokeWidth="2"
          strokeOpacity="0.45"
        />
        <circle
          cx="122"
          cy="122"
          r={accentRadius}
          fill="none"
          stroke="#c24d24"
          strokeWidth="4"
          strokeLinecap="butt"
          strokeDasharray={`8 ${(accentCircumference - 8 * 24) / 24}`}
          strokeDashoffset="4"
        />
        <circle
          cx="122"
          cy="122"
          r={radius}
          fill="#f3e3cc"
          stroke="#0a4550"
          strokeWidth="12"
        />
        <circle
          cx="122"
          cy="122"
          r={radius}
          fill="none"
          stroke="url(#focus-ring)"
          strokeLinecap="square"
          strokeWidth="12"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
        />
        <defs>
          <linearGradient id="focus-ring" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#c24d24" />
            <stop offset="72%" stopColor="#c24d24" />
            <stop offset="100%" stopColor="#c99a3e" />
          </linearGradient>
        </defs>
      </svg>
      <div className="text-center">
        <div className="font-sans text-6xl leading-none font-black text-[#0a4550] tabular-nums">
          {formatSeconds(secondsLeft)}
        </div>
        <div className="mt-3 text-xs font-black tracking-[0.32em] text-[#c24d24] uppercase">
          focus
        </div>
      </div>
    </div>
  )
}

interface MetricPillProps {
  label: string
  value: string
  tone?: 'green' | 'gold' | 'rose'
}

function MetricPill({ label, value, tone = 'green' }: MetricPillProps) {
  const toneClass = {
    green: 'border-[#0a4550] bg-[#f3e3cc] text-[#0a4550] [border-left-width:12px] border-l-[#0a4550]',
    gold: 'border-[#0a4550] bg-[#f3e3cc] text-[#0a4550] [border-left-width:12px] border-l-[#c99a3e]',
    rose: 'border-[#0a4550] bg-[#f3e3cc] text-[#0a4550] [border-left-width:12px] border-l-[#c24d24]',
  }[tone]

  return (
    <div className={cn('rounded-none border-4 py-2 pr-3 pl-4 shadow-none', toneClass)}>
      <div className="text-[10px] font-black tracking-[0.24em] uppercase">{label}</div>
      <div className="mt-1 text-lg font-black tabular-nums">{value}</div>
    </div>
  )
}

interface SaplingGardenProps {
  saplings: SaplingKind[]
  compact?: boolean
  showHoverDescription?: boolean
}

interface SaplingIconProps {
  kind: SaplingKind
  compact: boolean
  showHoverDescription?: boolean
}

function SaplingIcon({ kind, compact, showHoverDescription = false }: SaplingIconProps) {
  const sapling = SAPLING_KINDS[kind]
  const stemClass = cn(
    'absolute bottom-0 left-1/2 block -translate-x-1/2 border-x-2 border-[#0a4550]',
    sapling.stemClass,
    compact ? 'h-4 w-2' : 'h-6 w-2.5'
  )

  return (
    <div
      className={cn('group relative shrink-0', compact ? 'h-8 w-6' : 'h-12 w-8')}
      title={`${sapling.label}：${sapling.description}`}
      aria-label={`${sapling.label}：${sapling.description}`}
    >
      <span className={stemClass} />
      {sapling.shape === 'twin' && (
        <>
          <span
            className={cn(
              'absolute block -rotate-[28deg] border-2 border-[#0a4550]',
              sapling.leftLeafClass,
              compact ? 'top-2 left-0 h-3 w-4' : 'top-3 left-0 h-4 w-5'
            )}
          />
          <span
            className={cn(
              'absolute block rotate-[28deg] border-2 border-[#0a4550]',
              sapling.rightLeafClass,
              compact ? 'top-1.5 right-0 h-3 w-4' : 'top-2 right-0 h-4 w-5'
            )}
          />
          <span
            className={cn(
              'absolute left-1/2 block -translate-x-1/2 border-2 border-[#0a4550]',
              sapling.accentClass,
              compact ? 'top-0 h-2 w-2' : 'top-0 h-2.5 w-2.5'
            )}
          />
        </>
      )}
      {sapling.shape === 'triple' && (
        <>
          <span
            className={cn(
              'absolute left-1/2 block -translate-x-1/2 rotate-45 border-2 border-[#0a4550]',
              sapling.accentClass,
              compact ? 'top-0 h-3 w-3' : 'top-1 h-4 w-4'
            )}
          />
          <span
            className={cn(
              'absolute block -rotate-[42deg] border-2 border-[#0a4550]',
              sapling.leftLeafClass,
              compact ? 'top-3 left-0 h-2.5 w-4' : 'top-4 left-0 h-3 w-5'
            )}
          />
          <span
            className={cn(
              'absolute block rotate-[42deg] border-2 border-[#0a4550]',
              sapling.rightLeafClass,
              compact ? 'top-3 right-0 h-2.5 w-4' : 'top-4 right-0 h-3 w-5'
            )}
          />
        </>
      )}
      {sapling.shape === 'lantern' && (
        <>
          <span
            className={cn(
              'absolute left-1/2 block -translate-x-1/2 border-2 border-[#0a4550]',
              sapling.accentClass,
              compact ? 'top-1 h-3 w-5' : 'top-1 h-4 w-7'
            )}
          />
          <span
            className={cn(
              'absolute block -rotate-[20deg] border-2 border-[#0a4550]',
              sapling.leftLeafClass,
              compact ? 'top-4 left-0 h-2 w-4' : 'top-5 left-0 h-2.5 w-5'
            )}
          />
          <span
            className={cn(
              'absolute block rotate-[20deg] border-2 border-[#0a4550]',
              sapling.rightLeafClass,
              compact ? 'top-4 right-0 h-2 w-4' : 'top-5 right-0 h-2.5 w-5'
            )}
          />
        </>
      )}
      {sapling.shape === 'fruit' && (
        <>
          <span
            className={cn(
              'absolute left-1/2 block -translate-x-1/2 border-2 border-[#0a4550]',
              sapling.accentClass,
              compact ? 'top-0 h-3 w-3' : 'top-0 h-4 w-4'
            )}
          />
          <span
            className={cn(
              'absolute block -rotate-[32deg] border-2 border-[#0a4550]',
              sapling.leftLeafClass,
              compact ? 'top-3 left-0 h-2.5 w-4' : 'top-4 left-0 h-3 w-5'
            )}
          />
          <span
            className={cn(
              'absolute block rotate-[16deg] border-2 border-[#0a4550]',
              sapling.rightLeafClass,
              compact ? 'top-2 right-0 h-2.5 w-3.5' : 'top-3 right-0 h-3 w-4'
            )}
          />
        </>
      )}
      {showHoverDescription && (
        <div className="pointer-events-none absolute bottom-[calc(100%+0.5rem)] left-1/2 z-40 hidden w-44 -translate-x-1/2 border-4 border-[#0a4550] bg-[#f3e3cc] px-3 py-2 text-left text-[#0a4550] group-hover:block">
          <div className="truncate text-sm font-black">{sapling.label}</div>
          <div className="mt-1 text-xs leading-snug font-bold">{sapling.description}</div>
        </div>
      )}
    </div>
  )
}

function SaplingGarden({ saplings, compact = false, showHoverDescription = false }: SaplingGardenProps) {
  const visibleSaplings = saplings.slice(0, compact ? 8 : 14)
  const extraCount = Math.max(0, saplings.length - visibleSaplings.length)

  return (
    <div className={cn('flex items-end gap-1.5', compact ? 'min-h-8' : 'min-h-11')}>
      {visibleSaplings.map((kind, index) => (
        <SaplingIcon
          key={`${kind}-${index}`}
          kind={kind}
          compact={compact}
          showHoverDescription={showHoverDescription}
        />
      ))}
      {extraCount > 0 && (
        <div className="pb-1 text-sm font-black text-[#0a4550] tabular-nums">+{extraCount}</div>
      )}
      {saplings.length === 0 && (
        <div className="h-6 w-16 border-2 border-dashed border-[#0a4550]/60" aria-hidden="true" />
      )}
    </div>
  )
}

function useFocusCompanionEnabled(): boolean {
  const [enabled, setEnabled] = useState(() => getSetting('enableFocusCompanion'))

  useEffect(() => {
    const handleSettingsChange = (event: Event) => {
      const detail = (event as CustomEvent<{ key?: string; value?: unknown }>).detail
      if (detail?.key === 'enableFocusCompanion') {
        setEnabled(Boolean(detail.value))
      }
    }

    const handleSettingsReset = () => {
      setEnabled(DEFAULT_SETTINGS.enableFocusCompanion)
    }

    window.addEventListener('maibot-settings-change', handleSettingsChange)
    window.addEventListener('maibot-settings-reset', handleSettingsReset)
    return () => {
      window.removeEventListener('maibot-settings-change', handleSettingsChange)
      window.removeEventListener('maibot-settings-reset', handleSettingsReset)
    }
  }, [])

  return enabled
}

function FocusCompanionDisabled() {
  return (
    <section className="flex h-full min-h-[520px] items-center justify-center bg-[#f3e3cc] p-6 text-[#0a4550]">
      <div className="max-w-md border-4 border-[#0a4550] bg-[#f3e3cc] p-5">
        <div className="text-sm font-black tracking-[0.24em] text-[#c24d24] uppercase">focus</div>
        <h1 className="mt-2 text-2xl font-black">专注陪伴已隐藏</h1>
        <p className="mt-3 text-sm font-bold text-[#0a4550]/80">
          这个沉浸式番茄钟陪伴功能默认关闭。需要使用时，可以在 WebUI 设置里打开入口。
        </p>
        <Button asChild className="mt-5 rounded-none border-4 border-[#0a4550] bg-[#0a4550] text-[#f3e3cc] shadow-none hover:bg-[#c24d24]">
          <Link to="/settings" search={{ tab: 'other' }}>
            去设置打开
          </Link>
        </Button>
      </div>
    </section>
  )
}

export function FocusCompanionPage() {
  const enabled = useFocusCompanionEnabled()
  return enabled ? <FocusCompanionExperience /> : <FocusCompanionDisabled />
}

function FocusCompanionExperience() {
  const initialStorage = useMemo(() => readFocusCompanionStorage(), [])
  const [mode, setMode] = useState<TimerMode>('focus')
  const [customFocusMinutes, setCustomFocusMinutes] = useState(initialStorage.customFocusMinutes)
  const [secondsLeft, setSecondsLeft] = useState(initialStorage.customFocusMinutes * 60)
  const [running, setRunning] = useState(false)
  const [rounds, setRounds] = useState(0)
  const [saplings, setSaplings] = useState(initialStorage.saplings)
  const [todayFocusSeconds, setTodayFocusSeconds] = useState(initialStorage.todayFocusSeconds)
  const [todayFocusDate, setTodayFocusDate] = useState(initialStorage.todayFocusDate)
  const [mood, setMood] = useState<CompanionMood>('idle')
  const [immersive, setImmersive] = useState(false)
  const [chatDraft, setChatDraft] = useState('')
  const completionHandledRef = useRef(false)
  const activeModel = useMemo<{
    kind: ModelKind
    name: string
    url: string
  }>(() => ({ kind: 'vrm', name: DEFAULT_MODEL_NAME, url: DEFAULT_MODEL_URL }), [])
  const [, setModelLoadState] = useState<ModelLoadState>('idle')
  const companion = useFocusCompanionChat()
  const sendCompanionMessage = companion.send
  const sayCompanionLine = companion.sayLocal
  const isFocusLocked = running && mode === 'focus'

  const chatStreamsQuery = useQuery({
    queryKey: ['focus-chat-streams'],
    queryFn: () => getChatStreams(200),
    refetchInterval: isFocusLocked ? false : 30_000,
  })
  const focusDuration = customFocusMinutes * 60
  const duration = mode === 'focus' ? focusDuration : TIMER_MODE_SECONDS[mode]
  const progress = 1 - secondsLeft / duration
  const talkCount = chatStreamsQuery.data?.length ?? 0
  const companionLine = companion.isTyping ? '麦麦正在想...' : companion.latestLine
  const saplingCount = saplings.length
  const latestSapling = saplings.length > 0 ? SAPLING_KINDS[saplings[saplings.length - 1]] : null
  const todayFocusMinutes = Math.floor(todayFocusSeconds / 60)

  useEffect(() => {
    document.title = '专注陪伴 - MaiBot Dashboard'
  }, [])

  useEffect(() => {
    const today = getTodayStorageDate()
    if (todayFocusDate === today) {
      return
    }

    setTodayFocusDate(today)
    setTodayFocusSeconds(0)
  }, [todayFocusDate])

  useEffect(() => {
    writeFocusCompanionStorage({ customFocusMinutes, saplings, todayFocusDate, todayFocusSeconds })
  }, [customFocusMinutes, saplings, todayFocusDate, todayFocusSeconds])

  useEffect(() => {
    if (!running) {
      return
    }

    const timer = window.setInterval(() => {
      setSecondsLeft((current) => Math.max(0, current - 1))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [running])

  useEffect(() => {
    if (secondsLeft > 0) {
      completionHandledRef.current = false
      return
    }

    if (!running || completionHandledRef.current) {
      return
    }

    completionHandledRef.current = true
    const timeout = window.setTimeout(() => {
      setRunning(false)
      setRounds((current) => current + 1)
      setMood('cheer')
      if (mode === 'focus') {
        const earnedSapling = getRandomSaplingKind()
        const earnedSaplingInfo = SAPLING_KINDS[earnedSapling]
        const encouragement = `${getRandomEncouragement()} 获得 ${earnedSaplingInfo.label}：${earnedSaplingInfo.description}`
        setSaplings((current) => [...current, earnedSapling])
        setTodayFocusSeconds((current) => current + customFocusMinutes * 60)
        sayCompanionLine(encouragement)
        void sendCompanionMessage(
          `我完成了一段专注计时，并获得了${earnedSaplingInfo.label}。用一句很短的话鼓励我。`,
          { showTyping: false }
        )
        setSecondsLeft(customFocusMinutes * 60)
        return
      }
      void sendCompanionMessage(
        '我完成了一段休息计时，用一句很短的话回应我。'
      )
      setSecondsLeft(TIMER_MODE_SECONDS[mode])
    }, 0)

    return () => window.clearTimeout(timeout)
  }, [customFocusMinutes, mode, running, sayCompanionLine, secondsLeft, sendCompanionMessage])

  useEffect(() => {
    emitImmersiveChange(immersive)
    return () => emitImmersiveChange(false)
  }, [immersive])

  useEffect(() => {
    if (!isFocusLocked) {
      return
    }

    const blockEvent = (event: Event) => {
      if (isFocusLockControlTarget(event.target)) {
        return
      }

      event.preventDefault()
      event.stopPropagation()
    }

    const blockKeyboardEvent = (event: KeyboardEvent) => {
      if (isFocusLockControlTarget(event.target)) {
        return
      }

      event.preventDefault()
      event.stopPropagation()
    }

    const lockedEvents = [
      'click',
      'contextmenu',
      'dblclick',
      'mousedown',
      'mouseup',
      'pointerdown',
      'pointerup',
      'touchmove',
      'touchstart',
      'wheel',
    ]

    lockedEvents.forEach((eventName) => {
      document.addEventListener(eventName, blockEvent, { capture: true, passive: false })
    })
    document.addEventListener('keydown', blockKeyboardEvent, { capture: true })

    return () => {
      lockedEvents.forEach((eventName) => {
        document.removeEventListener(eventName, blockEvent, { capture: true })
      })
      document.removeEventListener('keydown', blockKeyboardEvent, { capture: true })
    }
  }, [isFocusLocked])

  const resetTimer = useCallback(
    (nextMode: TimerMode = mode) => {
      setMode(nextMode)
      setSecondsLeft(nextMode === 'focus' ? customFocusMinutes * 60 : TIMER_MODE_SECONDS[nextMode])
      setRunning(false)
      setMood(nextMode === 'focus' ? 'focus' : 'idle')
    },
    [customFocusMinutes, mode]
  )

  const handleModeChange = useCallback(
    (nextMode: TimerMode) => {
      if (isFocusLocked) {
        return
      }

      resetTimer(nextMode)
    },
    [isFocusLocked, resetTimer]
  )

  const toggleRunning = useCallback(() => {
    setRunning((current) => {
      const next = !current
      if (next && mode === 'focus') {
        setImmersive(true)
        if (!document.fullscreenElement) {
          void getFullscreenTarget().requestFullscreen().catch((error) => {
            console.warn('进入专注全屏失败:', error)
          })
        }
      }
      setMood(next ? 'focus' : 'idle')
      return next
    })
  }, [mode])

  const handleModelLoadStateChange = useCallback((state: ModelLoadState) => {
    setModelLoadState(state)
  }, [])

  const handleCharacterTap = useCallback(() => {
    if (isFocusLocked) {
      return
    }

    const nextMood: CompanionMood = mood === 'cheer' ? 'listening' : mood === 'listening' ? 'focus' : 'cheer'
    setMood(nextMood)
  }, [isFocusLocked, mood])

  const handleFocusMinutesChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      if (isFocusLocked) {
        return
      }

      const nextMinutes = clampFocusMinutes(Number(event.target.value))
      setCustomFocusMinutes(nextMinutes)
      if (mode === 'focus') {
        setSecondsLeft((current) => {
          if (running) {
            return Math.min(current, nextMinutes * 60)
          }
          return nextMinutes * 60
        })
      }
    },
    [isFocusLocked, mode, running]
  )

  const handleChatDraftChange = useCallback((event: ChangeEvent<HTMLInputElement>) => {
    setChatDraft(event.target.value)
  }, [])

  const handleChatSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault()
      if (isFocusLocked) {
        return
      }

      const content = chatDraft.trim()
      if (!content) {
        return
      }

      setMood('listening')
      setChatDraft('')
      void sendCompanionMessage(content)
    },
    [chatDraft, isFocusLocked, sendCompanionMessage]
  )

  return (
    <section
      data-focus-companion="true"
      className="relative h-full min-h-[680px] overflow-hidden bg-[#f3e3cc] bg-[linear-gradient(90deg,rgba(10,69,80,0.075)_2px,transparent_2px),linear-gradient(180deg,rgba(10,69,80,0.055)_2px,transparent_2px)] bg-[size:52px_52px] font-sans text-[#0a4550]"
    >
      <FocusThreeScene mood={mood} progress={progress} running={running} />

      <div className="pointer-events-none absolute inset-0 border-[14px] border-[#0a4550]" />
      <div className="pointer-events-none absolute right-7 bottom-5 h-3 w-[42vw] bg-[linear-gradient(to_bottom,#0a4550_0_33.333%,#c99a3e_33.333%_66.666%,#c24d24_66.666%_100%)]" />

      <div className="relative z-10 flex h-full min-h-0 flex-col">
        <div className="flex items-center justify-end px-4 pt-4 sm:px-7">
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className={RETRO_ICON_BUTTON_CLASS}
              title={immersive ? '退出沉浸' : '隐藏边栏'}
              aria-label={immersive ? '退出沉浸' : '隐藏边栏'}
              disabled={isFocusLocked}
              onClick={() => setImmersive((current) => !current)}
            >
              {immersive ? <Minimize2 className="h-4 w-4" /> : <Expand className="h-4 w-4" />}
            </Button>
          </div>
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-1 items-center gap-4 px-4 py-4 sm:px-7 lg:grid-cols-[minmax(260px,0.7fr)_minmax(520px,1.7fr)_minmax(190px,0.46fr)]">
          <div className="flex flex-col items-center justify-center gap-5 lg:-translate-y-10">
            <TimerRing progress={progress} secondsLeft={secondsLeft} />

            <div className={cn('flex items-center gap-2 p-1.5', RETRO_GLASS_SURFACE_CLASS)}>
              {MODE_ITEMS.map((item) => {
                const label = item.mode === 'focus' ? String(customFocusMinutes) : item.label
                return (
                  <button
                    key={item.mode}
                    type="button"
                    className={cn(
                      'h-9 w-12 rounded-none border-2 text-sm font-black tabular-nums transition',
                      mode === item.mode
                        ? 'border-[#0a4550] bg-[#0a4550] text-[#f3e3cc]'
                        : 'border-[#0a4550] bg-[#f3e3cc] text-[#0a4550] hover:bg-[#0a4550] hover:text-[#f3e3cc]',
                      'disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-[#f3e3cc] disabled:hover:text-[#0a4550]'
                    )}
                    title={`${label} 分钟`}
                    aria-label={`${label} 分钟`}
                    disabled={isFocusLocked}
                    onClick={() => handleModeChange(item.mode)}
                  >
                    {label}
                  </button>
                )
              })}
            </div>

            <label className={cn('flex items-center gap-2 px-3 py-2', RETRO_GLASS_SURFACE_CLASS)}>
              <span className="text-[10px] font-black tracking-[0.22em] uppercase">min</span>
              <input
                type="number"
                min={MIN_FOCUS_MINUTES}
                max={MAX_FOCUS_MINUTES}
                step={1}
                value={customFocusMinutes}
                className="focus-local-glass-input h-8 w-16 rounded-none border-0 text-center text-base font-black tabular-nums outline-none focus:bg-[#0a4550] focus:text-[#f3e3cc]"
                aria-label="自定义专注分钟数"
                disabled={isFocusLocked}
                onChange={handleFocusMinutesChange}
              />
            </label>

            <div className="flex items-center gap-3">
              <Button
                type="button"
                size="icon"
                data-focus-lock-control="true"
                className="h-13 w-13 rounded-none border-4 border-[#0a4550] bg-[#c24d24] text-[#f3e3cc] shadow-none hover:bg-[#0a4550] hover:text-[#f3e3cc]"
                title={running ? '暂停' : '开始'}
                aria-label={running ? '暂停' : '开始'}
                onClick={toggleRunning}
              >
                {running ? <Pause className="h-5 w-5" /> : <Play className="h-5 w-5" />}
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                data-focus-lock-control="true"
                className={cn(RETRO_ICON_BUTTON_CLASS, 'h-11 w-11')}
                title="重置"
                aria-label="重置"
                onClick={() => resetTimer()}
              >
                <RotateCcw className="h-4 w-4" />
              </Button>
            </div>

          </div>

          <div className="relative flex h-full min-h-[420px] items-end justify-center overflow-visible">
            <div
              className={cn(
                'relative flex h-full w-full max-w-none items-end justify-center',
                isFocusLocked && 'pointer-events-none'
              )}
              onClick={handleCharacterTap}
              role="button"
              tabIndex={isFocusLocked ? -1 : 0}
              aria-label="和麦麦互动"
              aria-disabled={isFocusLocked}
              onKeyDown={(event) => {
                if (isFocusLocked) {
                  return
                }

                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  handleCharacterTap()
                }
              }}
            >
              <div className="relative z-10 h-[min(88vh,900px)] w-[min(98vw,1180px)] max-w-none origin-bottom">
                <FocusModelViewer
                  key={activeModel.url}
                  kind={activeModel.kind}
                  modelUrl={activeModel.url}
                  mood={mood}
                  onLoadStateChange={handleModelLoadStateChange}
                />
              </div>
            </div>
          </div>

          <div className="hidden flex-col gap-3 lg:flex">
            <MetricPill label="today" value={`${todayFocusMinutes}m`} tone="green" />
            <MetricPill label="chat" value={String(talkCount)} tone="gold" />
            <MetricPill label="grove" value={String(saplingCount)} tone="gold" />

          </div>
        </div>

        <div className="relative z-20 flex shrink-0 flex-col gap-3 px-4 pb-4 sm:px-7 lg:flex-row lg:items-end lg:justify-between">
          <div className={cn('px-4 py-3', RETRO_PANEL_CLASS)}>
            <div className="flex items-center gap-2 text-xs font-black tracking-[0.24em] text-[#0a4550] uppercase">
              <Moon className="h-3.5 w-3.5" />
              {MOOD_LINES[mood]}
            </div>
            <div className="mt-1 max-w-[72ch] truncate text-lg font-black text-[#0a4550]">
              {companionLine}
            </div>
          </div>

          <div className={cn('min-w-[15rem] px-4 py-3', RETRO_PANEL_CLASS)}>
            <div className="mb-2 flex items-center gap-2 text-[10px] font-black tracking-[0.24em] text-[#0a4550] uppercase">
              <Sprout className="h-3.5 w-3.5 text-[#c99a3e]" />
              saplings
            </div>
            <SaplingGarden saplings={saplings} showHoverDescription />
            <div className="mt-2 min-h-9 max-w-64">
              {latestSapling ? (
                <>
                  <div className="truncate text-sm font-black text-[#0a4550]">{latestSapling.label}</div>
                  <div className="truncate text-xs font-bold text-[#0a4550]/80">{latestSapling.description}</div>
                </>
              ) : (
                <div className="text-xs font-bold text-[#0a4550]/70">完成一段专注后，会长出第一棵树苗。</div>
              )}
            </div>
          </div>

          <form
            className={cn('flex items-center gap-2 p-2', RETRO_GLASS_SURFACE_CLASS)}
            onSubmit={handleChatSubmit}
          >
            <input
              type="text"
              value={chatDraft}
              className="focus-local-glass-input h-11 w-44 rounded-none border-0 px-3 text-sm font-black outline-none placeholder:text-[#0a4550]/55 focus:bg-[#f3e3cc]"
              placeholder="和麦麦说"
              aria-label="和麦麦对话"
              disabled={isFocusLocked}
              onChange={handleChatDraftChange}
            />
            <Button
              type="submit"
              variant="ghost"
              size="icon"
              className={cn(RETRO_ICON_BUTTON_CLASS, 'h-11 w-11')}
              title="发送"
              aria-label="发送"
              disabled={isFocusLocked || !chatDraft.trim()}
            >
              <Send className="h-4 w-4" />
            </Button>
            <div className="h-8 w-1 bg-[#c99a3e]" />
            <div className="min-w-11 text-center">
              <div className="text-lg font-black text-[#0a4550] tabular-nums">{rounds}</div>
              <div className="text-[10px] font-black tracking-[0.2em] text-[#c99a3e] uppercase">
                done
              </div>
            </div>
          </form>
        </div>
      </div>
    </section>
  )
}
