/* eslint-disable react-refresh/only-export-components -- 本文件导出示例目录，内部组件随目录整体重新加载。 */
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Calendar } from '@/components/ui/calendar'
import { ComboboxInput } from '@/components/ui/combobox-input'
import { DateTimePicker } from '@/components/ui/date-time-picker'
import { DiscreteSlider } from '@/components/ui/discrete-slider'
import { DraftNumberInput } from '@/components/ui/draft-number-input'
import { ExtraParamsDialog } from '@/components/ui/extra-params-dialog'
import { FilterPanel, FilterPanelToggle } from '@/components/ui/filter-panel'
import {
  FoldSection,
  FormColumn,
  FormColumns,
  FormRow,
  FormSection,
  FormUnit,
} from '@/components/ui/form-section'
import { Input } from '@/components/ui/input'
import { KeyValueEditor } from '@/components/ui/key-value-editor'
import { Label } from '@/components/ui/label'
import { MultiSelect } from '@/components/ui/multi-select'
import { NestedKeyValueEditor } from '@/components/ui/nested-key-value-editor'
import { PasswordInput } from '@/components/ui/password-input'
import { SplitDropdownSelect } from '@/components/ui/split-dropdown-select'
import { TimeRangeTicks, TimeRangeTrack } from '@/components/ui/time-range-track'
import { formatTalkTimeRange, getTimelineSegments } from '@/lib/time-range'

import type { ComponentExample } from './component-gallery-types'

const options = [
  { value: 'one', label: '选项一' },
  { value: 'two', label: '选项二' },
  { value: 'three', label: '选项三' },
]

function CalendarExample() {
  const [date, setDate] = useState<Date | undefined>(new Date(2026, 10, 5))
  return <Calendar mode="single" selected={date} onSelect={setDate} defaultMonth={date} />
}

function ComboboxExample() {
  const [value, setValue] = useState('')
  return (
    <div className="space-y-2">
      <Label htmlFor="gallery-combobox">选择或输入模型</Label>
      <ComboboxInput
        id="gallery-combobox"
        value={value}
        onValueChange={setValue}
        options={[
          { value: 'model-a', description: '快速模型' },
          { value: 'model-b', description: '推理模型' },
        ]}
        placeholder="选择或自由输入"
      />
    </div>
  )
}

function DateTimeExample() {
  const [value, setValue] = useState<number>()
  return (
    <DateTimePicker id="gallery-date-time" label="示例日期时间" value={value} onChange={setValue} />
  )
}

function DiscreteSliderExample() {
  const [value, setValue] = useState('two')
  return (
    <DiscreteSlider label="示例档位" value={value} onValueChange={setValue} options={options} />
  )
}

function NumberExample() {
  const [value, setValue] = useState(10)
  return (
    <div className="space-y-2">
      <DraftNumberInput aria-label="数字输入示例" value={value} onValueChange={setValue} />
      <p className="text-sm">已提交数值：{value}（失焦或回车提交）</p>
    </div>
  )
}

function ParamsExample({ nested = false, dialog = false }: { nested?: boolean; dialog?: boolean }) {
  const [value, setValue] = useState<Record<string, unknown>>({ temperature: 0.7, enabled: true })
  const [open, setOpen] = useState(false)
  if (dialog)
    return (
      <div className="space-y-2">
        <Button variant="outline" onClick={() => setOpen(true)}>
          编辑示例参数
        </Button>
        <ExtraParamsDialog open={open} onOpenChange={setOpen} value={value} onChange={setValue} />
        <pre className="overflow-auto text-xs">{JSON.stringify(value, null, 2)}</pre>
      </div>
    )
  return nested ? (
    <NestedKeyValueEditor value={value} onChange={setValue} />
  ) : (
    <KeyValueEditor value={value} onChange={setValue} />
  )
}

function FilterExample() {
  const [open, setOpen] = useState(true)
  const [selected, setSelected] = useState(new Set(['one', 'two']))
  return (
    <div className="space-y-3">
      <FilterPanelToggle
        label="筛选"
        open={open}
        onOpenChange={setOpen}
        selectedCount={selected.size}
        total={options.length}
        controls="gallery-filter"
      />
      {open && (
        <FilterPanel
          id="gallery-filter"
          title="选择范围"
          options={options.map((option) => ({ id: option.value, label: option.label }))}
          selected={selected}
          onToggle={(id) =>
            setSelected((current) => {
              const next = new Set(current)
              if (next.has(id)) next.delete(id)
              else next.add(id)
              return next
            })
          }
          onSelectAll={() => setSelected(new Set(options.map((option) => option.value)))}
          onSelectNone={() => setSelected(new Set())}
        />
      )}
    </div>
  )
}

function FormExample() {
  const [open, setOpen] = useState(true)
  return (
    <FormColumns>
      <FormColumn>
        <FormSection title="基础设置">
          <FormRow label="名称" htmlFor="gallery-form-name" required>
            <Input id="gallery-form-name" placeholder="示例名称" />
          </FormRow>
        </FormSection>
      </FormColumn>
      <FormColumn>
        <FoldSection title="高级设置" open={open} onOpenChange={setOpen}>
          <FormRow label="间隔" htmlFor="gallery-form-time">
            <Input id="gallery-form-time" type="number" defaultValue={10} />
            <FormUnit>秒</FormUnit>
          </FormRow>
        </FoldSection>
      </FormColumn>
    </FormColumns>
  )
}

function MultiSelectExample() {
  const [selected, setSelected] = useState(['one'])
  return (
    <MultiSelect
      options={options}
      selected={selected}
      onChange={setSelected}
      placeholder="选择多个选项"
    />
  )
}

function SplitSelectExample() {
  const [value, setValue] = useState('one')
  return (
    <SplitDropdownSelect
      label="示例分栏选择"
      value={value}
      onValueChange={setValue}
      options={options}
      actions={[{ id: 'reset', label: '重置选择', onSelect: () => setValue('one') }]}
    />
  )
}

function TimeRangeExample() {
  const [range, setRange] = useState({ start: 540, end: 1080 })
  return (
    <div className="space-y-2 px-3">
      <TimeRangeTicks />
      <TimeRangeTrack
        range={range}
        segments={getTimelineSegments(range)}
        colorClassName="bg-primary"
        label="示例时段"
        onChange={setRange}
      />
      <p className="text-sm">{formatTalkTimeRange(range)}</p>
    </div>
  )
}

export const formExamples: ComponentExample[] = [
  {
    file: 'calendar',
    name: 'Calendar / CalendarDayButton',
    category: '表单',
    description: '日历：月份切换与日期选择。',
    preview: <CalendarExample />,
  },
  {
    file: 'combobox-input',
    name: 'ComboboxInput',
    category: '表单',
    description: '可搜索候选项，也可自由输入。',
    preview: <ComboboxExample />,
  },
  {
    file: 'date-time-picker',
    name: 'DateTimePicker',
    category: '表单',
    description: '日期和时间选择器。',
    preview: <DateTimeExample />,
  },
  {
    file: 'discrete-slider',
    name: 'DiscreteSlider',
    category: '表单',
    description: '离散档位滑块。',
    preview: <DiscreteSliderExample />,
  },
  {
    file: 'draft-number-input',
    name: 'DraftNumberInput',
    category: '表单',
    description: '允许暂存编辑内容的数字输入。',
    preview: <NumberExample />,
  },
  {
    file: 'extra-params-dialog',
    name: 'ExtraParamsDialog',
    category: '表单',
    description: '在对话框中编辑参数，确认后更新本地示例。',
    preview: <ParamsExample dialog />,
  },
  {
    file: 'filter-panel',
    name: 'FilterPanel / FilterPanelToggle',
    category: '表单',
    description: '多选筛选面板与展开按钮。',
    preview: <FilterExample />,
  },
  {
    file: 'form-section',
    name: 'FormColumns / FormColumn / FormSection / FormRow / FormUnit / FoldSection',
    category: '布局',
    description: '分栏、分组、标签、单位和可折叠表单。',
    preview: <FormExample />,
  },
  {
    file: 'key-value-editor',
    name: 'KeyValueEditor',
    category: '表单',
    description: '键值参数编辑，支持列表与 JSON 模式。',
    preview: <ParamsExample />,
  },
  {
    file: 'label',
    name: 'Label',
    category: '基础',
    description: '与输入控件关联的表单标签。',
    preview: (
      <div className="space-y-2">
        <Label htmlFor="gallery-label-input">点击标签聚焦输入框</Label>
        <Input id="gallery-label-input" />
      </div>
    ),
  },
  {
    file: 'multi-select',
    name: 'MultiSelect',
    category: '表单',
    description: '可搜索多选框与可排序标签。',
    preview: <MultiSelectExample />,
  },
  {
    file: 'nested-key-value-editor',
    name: 'NestedKeyValueEditor',
    category: '表单',
    description: '编辑对象、数组及嵌套参数。',
    preview: <ParamsExample nested />,
  },
  {
    file: 'password-input',
    name: 'PasswordInput',
    category: '表单',
    description: '切换密码显示与隐藏。',
    preview: (
      <PasswordInput
        aria-label="示例密码"
        defaultValue="demo-password"
        showLabel="显示示例密码"
        hideLabel="隐藏示例密码"
      />
    ),
  },
  {
    file: 'split-dropdown-select',
    name: 'SplitDropdownSelect',
    category: '表单',
    description: '将操作与选项分栏排列的下拉选择器。',
    preview: <SplitSelectExample />,
  },
  {
    file: 'time-range-track',
    name: 'TimeRangeTrack / TimeRangeTicks',
    category: '表单',
    description: '全天时间刻度，拖动两端调整范围。',
    preview: <TimeRangeExample />,
  },
]
