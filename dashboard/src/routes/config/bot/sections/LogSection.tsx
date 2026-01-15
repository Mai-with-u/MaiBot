import React, { useState } from 'react'
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
import { Plus, Trash2 } from 'lucide-react'
import type { LogConfig } from '../types'

interface LogSectionProps {
  config: LogConfig
  onChange: (config: LogConfig) => void
}

export const LogSection = React.memo(function LogSection({ config, onChange }: LogSectionProps) {
  const [newLibrary, setNewLibrary] = useState('')
  const [newLogLevel, setNewLogLevel] = useState('WARNING')

  const addSuppressedLibrary = () => {
    if (newLibrary && !config.suppress_libraries.includes(newLibrary)) {
      onChange({
        ...config,
        suppress_libraries: [...config.suppress_libraries, newLibrary],
      })
      setNewLibrary('')
    }
  }

  const removeSuppressedLibrary = (library: string) => {
    onChange({
      ...config,
      suppress_libraries: config.suppress_libraries.filter((l) => l !== library),
    })
  }

  const addLibraryLogLevel = () => {
    if (newLibrary && !config.library_log_levels[newLibrary]) {
      onChange({
        ...config,
        library_log_levels: { ...config.library_log_levels, [newLibrary]: newLogLevel },
      })
      setNewLibrary('')
      setNewLogLevel('WARNING')
    }
  }

  const removeLibraryLogLevel = (library: string) => {
    const newLevels = { ...config.library_log_levels }
    delete newLevels[library]
    onChange({ ...config, library_log_levels: newLevels })
  }

  const logLevels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
  const logLevelStyles = ['FULL', 'compact', 'lite']
  const colorTextOptions = ['none', 'title', 'full']

  return (
    <div className="rounded-lg border bg-card p-4 sm:p-6 space-y-6">
      <div>
        <h3 className="text-lg font-semibold mb-4">日志配置</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="grid gap-2">
            <Label>日期格式</Label>
            <Input
              value={config.date_style}
              onChange={(e) => onChange({ ...config, date_style: e.target.value })}
              placeholder="例如: m-d H:i:s"
            />
            <p className="text-xs text-muted-foreground">m=月, d=日, H=时, i=分, s=秒</p>
          </div>

          <div className="grid gap-2">
            <Label>日志级别样式</Label>
            <Select
              value={config.log_level_style}
              onValueChange={(value) => onChange({ ...config, log_level_style: value })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {logLevelStyles.map((style) => (
                  <SelectItem key={style} value={style}>
                    {style}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-2">
            <Label>日志文本颜色</Label>
            <Select
              value={config.color_text}
              onValueChange={(value) => onChange({ ...config, color_text: value })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {colorTextOptions.map((option) => (
                  <SelectItem key={option} value={option}>
                    {option}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-2">
            <Label>全局日志级别</Label>
            <Select
              value={config.log_level}
              onValueChange={(value) => onChange({ ...config, log_level: value })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {logLevels.map((level) => (
                  <SelectItem key={level} value={level}>
                    {level}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-2">
            <Label>控制台日志级别</Label>
            <Select
              value={config.console_log_level}
              onValueChange={(value) => onChange({ ...config, console_log_level: value })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {logLevels.map((level) => (
                  <SelectItem key={level} value={level}>
                    {level}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-2">
            <Label>文件日志级别</Label>
            <Select
              value={config.file_log_level}
              onValueChange={(value) => onChange({ ...config, file_log_level: value })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {logLevels.map((level) => (
                  <SelectItem key={level} value={level}>
                    {level}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      </div>

      {/* 屏蔽的库 */}
      <div>
        <Label className="mb-2 block">完全屏蔽的库</Label>
        <div className="flex gap-2 mb-2">
          <Input
            value={newLibrary}
            onChange={(e) => setNewLibrary(e.target.value)}
            placeholder="输入库名"
            className="flex-1"
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                addSuppressedLibrary()
              }
            }}
          />
          <Button onClick={addSuppressedLibrary} size="sm" className="flex-shrink-0">
            <Plus className="h-4 w-4" strokeWidth={2} fill="none" />
          </Button>
        </div>
        <div className="flex flex-wrap gap-2">
          {config.suppress_libraries.map((library) => (
            <div
              key={library}
              className="flex items-center gap-1 bg-secondary px-3 py-1 rounded-md"
            >
              <span className="text-sm">{library}</span>
              <Button
                variant="ghost"
                size="sm"
                className="h-5 w-5 p-0"
                onClick={() => removeSuppressedLibrary(library)}
              >
                <Trash2 className="h-3 w-3" strokeWidth={2} fill="none" />
              </Button>
            </div>
          ))}
        </div>
      </div>

      {/* 特定库日志级别 */}
      <div>
        <Label className="mb-2 block">特定库的日志级别</Label>
        <div className="flex gap-2 mb-2">
          <Input
            value={newLibrary}
            onChange={(e) => setNewLibrary(e.target.value)}
            placeholder="输入库名"
            className="flex-1"
          />
          <Select value={newLogLevel} onValueChange={setNewLogLevel}>
            <SelectTrigger className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {logLevels.map((level) => (
                <SelectItem key={level} value={level}>
                  {level}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button onClick={addLibraryLogLevel} size="sm">
            <Plus className="h-4 w-4" strokeWidth={2} fill="none" />
          </Button>
        </div>
        <div className="space-y-2">
          {Object.entries(config.library_log_levels).map(([library, level]) => (
            <div
              key={library}
              className="flex items-center justify-between bg-secondary px-3 py-2 rounded-md"
            >
              <span className="text-sm font-medium">{library}</span>
              <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground">{level}</span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 w-6 p-0"
                  onClick={() => removeLibraryLogLevel(library)}
                >
                  <Trash2 className="h-3 w-3" strokeWidth={2} fill="none" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
})
