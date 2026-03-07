import { useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

import { Clock } from 'lucide-react'

interface TimeRangePickerProps {
  value: string
  onChange: (value: string) => void
}

// 时间选择组件
export function TimeRangePicker({ value, onChange }: TimeRangePickerProps) {
  // 解析初始值
  const parsedValue = useMemo(() => {
    const parts = value.split('-')
    if (parts.length === 2) {
      const [start, end] = parts
      const [sh, sm] = start.split(':')
      const [eh, em] = end.split(':')
      return {
        startHour: sh ? sh.padStart(2, '0') : '00',
        startMinute: sm ? sm.padStart(2, '0') : '00',
        endHour: eh ? eh.padStart(2, '0') : '23',
        endMinute: em ? em.padStart(2, '0') : '59',
      }
    }
    return {
      startHour: '00',
      startMinute: '00',
      endHour: '23',
      endMinute: '59',
    }
  }, [value])

  const [startHour, setStartHour] = useState(parsedValue.startHour)
  const [startMinute, setStartMinute] = useState(parsedValue.startMinute)
  const [endHour, setEndHour] = useState(parsedValue.endHour)
  const [endMinute, setEndMinute] = useState(parsedValue.endMinute)

  // 当value变化时同步状态
  useEffect(() => {
    setStartHour(parsedValue.startHour)
    setStartMinute(parsedValue.startMinute)
    setEndHour(parsedValue.endHour)
    setEndMinute(parsedValue.endMinute)
  }, [parsedValue])

  const updateTime = (
    newStartHour: string,
    newStartMinute: string,
    newEndHour: string,
    newEndMinute: string
  ) => {
    const newValue = `${newStartHour}:${newStartMinute}-${newEndHour}:${newEndMinute}`
    onChange(newValue)
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" className="w-full justify-start font-mono text-sm">
          <Clock className="h-4 w-4 mr-2" />
          {value || '选择时间段'}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-72 sm:w-80">
        <div className="space-y-4">
          <div>
            <h4 className="font-medium text-sm mb-3">开始时间</h4>
            <div className="grid grid-cols-2 gap-2 sm:gap-3">
              <div>
                <Label className="text-xs">小时</Label>
                <Select
                  value={startHour}
                  onValueChange={(v) => {
                    setStartHour(v)
                    updateTime(v, startMinute, endHour, endMinute)
                  }}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Array.from({ length: 24 }, (_, i) => i).map((h) => (
                      <SelectItem key={h} value={h.toString().padStart(2, '0')}>
                        {h.toString().padStart(2, '0')}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label className="text-xs">分钟</Label>
                <Select
                  value={startMinute}
                  onValueChange={(v) => {
                    setStartMinute(v)
                    updateTime(startHour, v, endHour, endMinute)
                  }}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Array.from({ length: 60 }, (_, i) => i).map((m) => (
                      <SelectItem key={m} value={m.toString().padStart(2, '0')}>
                        {m.toString().padStart(2, '0')}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>
          <div>
            <h4 className="font-medium text-sm mb-3">结束时间</h4>
            <div className="grid grid-cols-2 gap-2 sm:gap-3">
              <div>
                <Label className="text-xs">小时</Label>
                <Select
                  value={endHour}
                  onValueChange={(v) => {
                    setEndHour(v)
                    updateTime(startHour, startMinute, v, endMinute)
                  }}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Array.from({ length: 24 }, (_, i) => i).map((h) => (
                      <SelectItem key={h} value={h.toString().padStart(2, '0')}>
                        {h.toString().padStart(2, '0')}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label className="text-xs">分钟</Label>
                <Select
                  value={endMinute}
                  onValueChange={(v) => {
                    setEndMinute(v)
                    updateTime(startHour, startMinute, endHour, v)
                  }}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Array.from({ length: 60 }, (_, i) => i).map((m) => (
                      <SelectItem key={m} value={m.toString().padStart(2, '0')}>
                        {m.toString().padStart(2, '0')}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}
