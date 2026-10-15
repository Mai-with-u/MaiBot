import { zhCN } from 'date-fns/locale'
import { CalendarDays } from 'lucide-react'
import { useState } from 'react'

import { Button } from './button'
import { Calendar } from './calendar'
import { Label } from './label'
import { Popover, PopoverContent, PopoverTrigger } from './popover'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './select'

interface DateTimePickerProps {
  id: string
  label: string
  value?: number
  onChange: (value: number | undefined) => void
}

const HOURS = Array.from({ length: 24 }, (_, index) => String(index).padStart(2, '0'))
const MINUTES = Array.from({ length: 60 }, (_, index) => String(index).padStart(2, '0'))

/** 使用页面主题的日期时间弹窗，值以秒级时间戳传递，显示与选择均使用本地时区。 */
export function DateTimePicker({ id, label, value, onChange }: DateTimePickerProps) {
  const [open, setOpen] = useState(false)
  const selected = value === undefined ? undefined : new Date(value * 1000)
  const hour = String(selected?.getHours() ?? 0).padStart(2, '0')
  const minute = String(selected?.getMinutes() ?? 0).padStart(2, '0')

  const selectDay = (day: Date | undefined) => {
    if (!day) return
    const next = new Date(day)
    next.setHours(Number(hour), Number(minute), 0, 0)
    onChange(Math.floor(next.getTime() / 1000))
  }

  const selectTime = (part: 'hour' | 'minute', nextValue: string) => {
    const next = selected ? new Date(selected) : new Date()
    next.setHours(
      part === 'hour' ? Number(nextValue) : Number(hour),
      part === 'minute' ? Number(nextValue) : Number(minute),
      0,
      0
    )
    onChange(Math.floor(next.getTime() / 1000))
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          id={id}
          type="button"
          aria-label={label}
          data-dashboard-date-time-trigger="true"
          className="border-input bg-background focus-visible:ring-ring flex h-9 w-full cursor-pointer items-center justify-between gap-3 rounded-md border px-3 py-2 text-left text-sm shadow-sm focus-visible:ring-1 focus-visible:outline-none"
        >
          <span className={selected ? 'tabular-nums' : 'text-muted-foreground'}>
            {selected
              ? selected.toLocaleString('zh-CN', {
                  year: 'numeric',
                  month: '2-digit',
                  day: '2-digit',
                  hour: '2-digit',
                  minute: '2-digit',
                  hour12: false,
                })
              : '选择日期和时间'}
          </span>
          <CalendarDays className="h-4 w-4 shrink-0" aria-hidden="true" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        aria-label={`${label}选择器`}
        data-date-time-picker="true"
        className="w-[min(20rem,calc(100vw-2rem))] space-y-3 p-3"
      >
        <div className="text-sm font-semibold">{label}</div>
        <Calendar
          mode="single"
          selected={selected}
          defaultMonth={selected}
          onSelect={selectDay}
          locale={zhCN}
          autoFocus
          className="w-full p-2"
          classNames={{
            root: 'w-full',
            month_caption: 'flex h-8 items-center justify-center px-8',
            button_previous: 'h-8 w-8 p-0',
            button_next: 'h-8 w-8 p-0',
          }}
        />
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor={`${id}-hour`}>小时</Label>
            <Select value={hour} onValueChange={(next) => selectTime('hour', next)}>
              <SelectTrigger id={`${id}-hour`} aria-label={`${label}的小时`}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {HOURS.map((item) => (
                  <SelectItem key={item} value={item}>
                    {item}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`${id}-minute`}>分钟</Label>
            <Select value={minute} onValueChange={(next) => selectTime('minute', next)}>
              <SelectTrigger id={`${id}-minute`} aria-label={`${label}的分钟`}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MINUTES.map((item) => (
                  <SelectItem key={item} value={item}>
                    {item}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="flex-1"
            onClick={() => onChange(undefined)}
          >
            清空
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="flex-1"
            onClick={() => onChange(Math.floor(Date.now() / 60000) * 60)}
          >
            现在
          </Button>
          <Button type="button" size="sm" className="flex-1" onClick={() => setOpen(false)}>
            完成
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
