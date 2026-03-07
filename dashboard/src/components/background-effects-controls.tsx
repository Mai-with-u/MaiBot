import { RotateCcw } from 'lucide-react'
import * as React from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Slider } from '@/components/ui/slider'
import { hexToHSL } from '@/lib/theme/palette'
import {
  type BackgroundEffects,
  defaultBackgroundEffects,
} from '@/lib/theme/tokens'

// ============================================================================
// Helper Functions
// ============================================================================

/**
 * 将 HSL 字符串转换为 HEX 格式
 * (从 settings.tsx 移植)
 */
function hslToHex(hsl: string): string {
  if (!hsl) return '#000000'

  // 解析 "221.2 83.2% 53.3%" 格式
  const parts = hsl.split(' ').filter(Boolean)
  if (parts.length < 3) return '#000000'

  const h = parseFloat(parts[0])
  const s = parseFloat(parts[1].replace('%', ''))
  const l = parseFloat(parts[2].replace('%', ''))

  const sDecimal = s / 100
  const lDecimal = l / 100

  const c = (1 - Math.abs(2 * lDecimal - 1)) * sDecimal
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1))
  const m = lDecimal - c / 2

  let r = 0,
    g = 0,
    b = 0

  if (h >= 0 && h < 60) {
    r = c
    g = x
    b = 0
  } else if (h >= 60 && h < 120) {
    r = x
    g = c
    b = 0
  } else if (h >= 120 && h < 180) {
    r = 0
    g = c
    b = x
  } else if (h >= 180 && h < 240) {
    r = 0
    g = x
    b = c
  } else if (h >= 240 && h < 300) {
    r = x
    g = 0
    b = c
  } else if (h >= 300 && h < 360) {
    r = c
    g = 0
    b = x
  }

  const toHex = (n: number) => {
    const hex = Math.round((n + m) * 255).toString(16)
    return hex.length === 1 ? '0' + hex : hex
  }

  return `#${toHex(r)}${toHex(g)}${toHex(b)}`
}

// ============================================================================
// Component
// ============================================================================

type BackgroundEffectsControlsProps = {
  effects: BackgroundEffects
  onChange: (effects: BackgroundEffects) => void
}

export function BackgroundEffectsControls({
  effects,
  onChange,
}: BackgroundEffectsControlsProps) {
  // 处理数值变更
  const handleValueChange = (key: keyof BackgroundEffects, value: number) => {
    onChange({
      ...effects,
      [key]: value,
    })
  }

  // 处理颜色变更
  const handleColorChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const hex = e.target.value
    const hsl = hexToHSL(hex)
    onChange({
      ...effects,
      overlayColor: hsl,
    })
  }

  // 处理位置变更
  const handlePositionChange = (value: string) => {
    onChange({
      ...effects,
      position: value as BackgroundEffects['position'],
    })
  }

  // 处理渐变变更
  const handleGradientChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    onChange({
      ...effects,
      gradientOverlay: e.target.value,
    })
  }

  // 重置为默认值
  const handleReset = () => {
    onChange(defaultBackgroundEffects)
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">背景效果调节</h3>
        <Button
          variant="outline"
          size="sm"
          onClick={handleReset}
          className="h-8 px-2 text-xs"
        >
          <RotateCcw className="mr-2 h-3.5 w-3.5" />
          重置默认
        </Button>
      </div>

      <div className="grid gap-6">
        {/* 1. Blur (模糊) */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Label>模糊程度 (Blur)</Label>
            <span className="text-xs text-muted-foreground">
              {effects.blur}px
            </span>
          </div>
          <Slider
            value={[effects.blur]}
            min={0}
            max={50}
            step={1}
            onValueChange={(vals) => handleValueChange('blur', vals[0])}
          />
        </div>

        {/* 2. Overlay Color (遮罩颜色) */}
        <div className="space-y-3">
          <Label>遮罩颜色 (Overlay Color)</Label>
          <div className="flex items-center gap-3">
            <div className="h-9 w-9 overflow-hidden rounded-md border shadow-sm">
              <input
                type="color"
                value={hslToHex(effects.overlayColor)}
                onChange={handleColorChange}
                className="h-[150%] w-[150%] -translate-x-1/4 -translate-y-1/4 cursor-pointer border-0 p-0"
              />
            </div>
            <Input
              value={hslToHex(effects.overlayColor)}
              readOnly
              className="flex-1 font-mono uppercase"
            />
          </div>
        </div>

        {/* 3. Overlay Opacity (遮罩不透明度) */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Label>遮罩不透明度 (Opacity)</Label>
            <span className="text-xs text-muted-foreground">
              {Math.round(effects.overlayOpacity * 100)}%
            </span>
          </div>
          <Slider
            value={[effects.overlayOpacity * 100]}
            min={0}
            max={100}
            step={1}
            onValueChange={(vals) =>
              handleValueChange('overlayOpacity', vals[0] / 100)
            }
          />
        </div>

        {/* 4. Position (位置) */}
        <div className="space-y-3">
          <Label>背景位置 (Position)</Label>
          <Select value={effects.position} onValueChange={handlePositionChange}>
            <SelectTrigger>
              <SelectValue placeholder="选择位置" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="cover">覆盖 (Cover)</SelectItem>
              <SelectItem value="contain">包含 (Contain)</SelectItem>
              <SelectItem value="center">居中 (Center)</SelectItem>
              <SelectItem value="stretch">拉伸 (Stretch)</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {/* 5. Brightness (亮度) */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Label>亮度 (Brightness)</Label>
            <span className="text-xs text-muted-foreground">
              {effects.brightness}%
            </span>
          </div>
          <Slider
            value={[effects.brightness]}
            min={0}
            max={200}
            step={1}
            onValueChange={(vals) => handleValueChange('brightness', vals[0])}
          />
        </div>

        {/* 6. Contrast (对比度) */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Label>对比度 (Contrast)</Label>
            <span className="text-xs text-muted-foreground">
              {effects.contrast}%
            </span>
          </div>
          <Slider
            value={[effects.contrast]}
            min={0}
            max={200}
            step={1}
            onValueChange={(vals) => handleValueChange('contrast', vals[0])}
          />
        </div>

        {/* 7. Saturate (饱和度) */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Label>饱和度 (Saturate)</Label>
            <span className="text-xs text-muted-foreground">
              {effects.saturate}%
            </span>
          </div>
          <Slider
            value={[effects.saturate]}
            min={0}
            max={200}
            step={1}
            onValueChange={(vals) => handleValueChange('saturate', vals[0])}
          />
        </div>

        {/* 8. Gradient Overlay (渐变叠加) */}
        <div className="space-y-3">
          <Label>CSS 渐变叠加 (Gradient Overlay)</Label>
          <Input
            value={effects.gradientOverlay || ''}
            onChange={handleGradientChange}
            placeholder="e.g. linear-gradient(to bottom, transparent, black)"
            className="font-mono text-xs"
          />
          <p className="text-[10px] text-muted-foreground">
            可选：输入有效的 CSS gradient 字符串
          </p>
        </div>
      </div>
    </div>
  )
}
