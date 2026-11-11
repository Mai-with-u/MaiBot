import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { MultiSelect } from '@/components/ui/multi-select'
import { Switch } from '@/components/ui/switch'
import { formatChatDisplayName } from '@/lib/chat-display'
import {
  getJargonInferenceSettings,
  rescanJargonInference,
  saveJargonInferenceSettings,
} from '@/lib/jargon-inference-api'
import type { JargonChatInfo } from '@/types/jargon'
import type { JargonInferenceJob, JargonInferenceSettings } from '@/types/jargon-inference'

import { InferenceChoice } from './JargonInferenceControls'

const numberFields = [
  { key: 'max_concurrency', label: '同时处理词条数', min: 1, max: 16, step: 1 },
  {
    key: 'request_interval_seconds',
    label: '模型请求最小间隔（秒）',
    min: 0,
    max: 3600,
    step: 'any',
  },
  { key: 'max_retries', label: '请求失败的最大重试次数', min: 0, max: 5, step: 1 },
  {
    key: 'scan_interval_hours',
    label: '定时扫描周期（小时）',
    min: 1 / 60,
    max: 8760,
    step: 'any',
  },
  {
    key: 'refresh_interval_hours',
    label: '单词条更新间隔（小时）',
    min: 0,
    max: 8760,
    step: 'any',
  },
  { key: 'max_items_per_scan', label: '每轮扫描处理上限', min: 1, max: 10000, step: 1 },
  { key: 'evidence_limit', label: '每个词条保留的证据组数', min: 1, max: 200, step: 1 },
  { key: 'history_limit', label: '每个词条保留的历史版本数', min: 1, max: 200, step: 1 },
  { key: 'job_retention_days', label: '已结束任务保留天数', min: 1, max: 365, step: 1 },
] as const

type NumericKey = (typeof numberFields)[number]['key']

/** Load the server settings before mounting the form, and offer a retry if the initial fetch fails. */
export function JargonInferenceSettingsPanel({
  chats,
  onRescan,
}: {
  chats: JargonChatInfo[]
  onRescan: (job: JargonInferenceJob) => void
}) {
  const { t } = useTranslation()
  const settings = useQuery({
    queryKey: ['jargon-inference', 'settings'],
    queryFn: getJargonInferenceSettings,
    refetchOnWindowFocus: false,
  })
  if (settings.isPending) return <p role="status">{t('jargonInference.loading', '正在加载…')}</p>
  if (settings.error)
    return (
      <div className="space-y-2">
        <p role="alert" className="text-destructive text-sm">
          {settings.error.message}
        </p>
        <Button variant="outline" onClick={() => void settings.refetch()}>
          {t('jargonInference.refresh', '刷新')}
        </Button>
      </div>
    )
  return <SettingsForm initial={settings.data} chats={chats} onRescan={onRescan} />
}

/** Merge remote changes into untouched inputs and block saves when the server and draft conflict. */
function SettingsForm({
  initial,
  chats,
  onRescan,
}: {
  initial: JargonInferenceSettings
  chats: JargonChatInfo[]
  onRescan: (job: JargonInferenceJob) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const baseline = useRef(initial)
  const remoteSnapshot = useRef(initial)
  const [conflict, setConflict] = useState(false)
  const [settings, setSettings] = useState(initial)
  const [thresholds, setThresholds] = useState(initial.thresholds.join(', '))
  const [numbers, setNumbers] = useState(
    () =>
      Object.fromEntries(numberFields.map(({ key }) => [key, String(initial[key])])) as Record<
        NumericKey,
        string
      >
  )
  const [validationError, setValidationError] = useState('')
  const [saved, setSaved] = useState(false)
  // Compare edits with the baseline before adopting each field from a newer server snapshot.
  useEffect(() => {
    const previous = baseline.current
    const equal = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)
    if (equal(initial, remoteSnapshot.current)) return
    remoteSnapshot.current = initial
    if (equal(initial, previous)) return
    baseline.current = initial
    let hasConflict = false
    const nextSettings = { ...settings }
    for (const key of Object.keys(initial) as Array<keyof JargonInferenceSettings>) {
      if (equal(settings[key], previous[key])) Object.assign(nextSettings, { [key]: initial[key] })
      else if (!equal(initial[key], previous[key])) hasConflict = true
    }
    let nextThresholds = thresholds
    if (thresholds === previous.thresholds.join(', '))
      nextThresholds = initial.thresholds.join(', ')
    else if (!equal(previous.thresholds, initial.thresholds)) hasConflict = true
    const nextNumbers = { ...numbers }
    for (const { key } of numberFields) {
      if (numbers[key] === String(previous[key])) nextNumbers[key] = String(initial[key])
      else if (previous[key] !== initial[key]) hasConflict = true
    }
    setSettings(nextSettings)
    setThresholds(nextThresholds)
    setNumbers(nextNumbers)
    if (hasConflict) setConflict(true)
    setSaved(false)
  }, [initial, settings, thresholds, numbers])
  const save = useMutation({
    mutationFn: saveJargonInferenceSettings,
    onSuccess: (value) => {
      baseline.current = value
      setConflict(false)
      setSettings(value)
      setThresholds(value.thresholds.join(', '))
      setNumbers(
        Object.fromEntries(numberFields.map(({ key }) => [key, String(value[key])])) as Record<
          NumericKey,
          string
        >
      )
      queryClient.setQueryData(['jargon-inference', 'settings'], value)
      setSaved(true)
      void queryClient.invalidateQueries({ queryKey: ['jargon-inference', 'preview'] })
    },
  })
  const reload = useMutation({
    mutationFn: getJargonInferenceSettings,
    onSuccess: (latest) => {
      baseline.current = latest
      setSettings(latest)
      setThresholds(latest.thresholds.join(', '))
      setNumbers(
        Object.fromEntries(numberFields.map(({ key }) => [key, String(latest[key])])) as Record<
          NumericKey,
          string
        >
      )
      setConflict(false)
      setSaved(false)
      save.reset()
      queryClient.setQueryData(['jargon-inference', 'settings'], latest)
    },
  })
  const rescan = useMutation({
    mutationFn: rescanJargonInference,
    /** Open progress only when the rescan actually enqueued work. */
    onSuccess: (job) => {
      if (job.total > 0) onRescan(job)
    },
  })
  const switchFields = [
    { key: 'stop_after_last_threshold', label: '达到最高阈值后停止按次数推断' },
    { key: 'scheduled_enabled', label: '启用定期重新推断' },
    { key: 'require_new_evidence', label: '定期更新仅处理有新证据的词条' },
  ] as const

  return (
    <form
      className="space-y-4"
      onChange={() => setSaved(false)}
      onSubmit={(event) => {
        event.preventDefault()
        const values = thresholds
          .trim()
          .split(/[,，\s]+/)
          .map(Number)
        if (
          !thresholds.trim() ||
          values.length > 64 ||
          values.some(
            (value, index) =>
              !Number.isSafeInteger(value) || value < 1 || (index > 0 && value <= values[index - 1])
          )
        ) {
          setValidationError(
            t(
              'jargonInference.thresholdValidation',
              '阈值须为 1 至 64 个严格递增的正整数，例如 1, 4, 8, 25, 100。'
            )
          )
          return
        }
        const numericSettings = { ...settings }
        for (const field of numberFields) {
          const value = Number(numbers[field.key])
          if (
            !numbers[field.key].trim() ||
            !Number.isFinite(value) ||
            value < field.min ||
            value > field.max ||
            (field.step === 1 && !Number.isInteger(value))
          ) {
            setValidationError(t('jargonInference.numberValidation', '请检查数值设置的范围。'))
            return
          }
          numericSettings[field.key] = value
        }
        setValidationError('')
        // Send the form's baseline so a concurrent server update cannot be silently overwritten.
        if (!conflict && !save.isPending)
          save.mutate({
            ...numericSettings,
            thresholds: values,
            expected_settings: baseline.current,
          })
      }}
    >
      <fieldset disabled={save.isPending} className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="inference-thresholds">
            {t('jargonInference.thresholds', '累计命中次数阈值')}
          </Label>
          <Input
            id="inference-thresholds"
            value={thresholds}
            onChange={(event) => setThresholds(event.target.value)}
            required
          />
          <p className="text-muted-foreground text-xs">
            {t(
              'jargonInference.thresholdHint',
              '次数指学习流程合并到词条的累计命中数，不是聊天消息条数。首次阈值为 1 时，新词条即可推断；降低阈值可补处理已有词条。'
            )}
          </p>
        </div>
        {switchFields.map(({ key, label }) => (
          <div key={key} className="flex items-center justify-between gap-3">
            <Label htmlFor={`inference-${key}`}>
              {t(`jargonInference.settingsFields.${key}`, label)}
            </Label>
            <Switch
              id={`inference-${key}`}
              disabled={save.isPending}
              checked={settings[key]}
              onCheckedChange={(value) => {
                setSettings({ ...settings, [key]: value })
                setSaved(false)
              }}
            />
          </div>
        ))}
        <p className="text-muted-foreground text-xs">
          {t(
            'jargonInference.repeatHint',
            '关闭最高阈值停止后，将继续按最后一个阈值的增量推断，例如 100、200、300。手动与定时任务独立于次数触发。'
          )}
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          {numberFields.map(({ key, label, min, max, step }) => (
            <div key={key} className="space-y-2">
              <Label htmlFor={`inference-${key}`}>
                {t(`jargonInference.settingsFields.${key}`, label)}
              </Label>
              <Input
                id={`inference-${key}`}
                type="number"
                min={min}
                max={max}
                step={step}
                required
                value={numbers[key]}
                onChange={(event) => setNumbers({ ...numbers, [key]: event.target.value })}
              />
            </div>
          ))}
        </div>
        <InferenceChoice
          id="inference-scheduled-scope"
          disabled={save.isPending}
          label={t('jargonInference.scheduledScope', '定期处理范围')}
          value={settings.scheduled_scope}
          onChange={(value) => {
            setSettings({
              ...settings,
              scheduled_scope: value as JargonInferenceSettings['scheduled_scope'],
            })
            setSaved(false)
          }}
          options={[
            { value: 'pending', label: t('jargonInference.pendingScope', '含义为空的词条') },
            {
              value: 'failed',
              label: t('jargonInference.failedScope', '上次失败或信息不足的词条'),
            },
            { value: 'all', label: t('jargonInference.allScope', '全部 AI 词条，包括已完成词条') },
          ]}
        />
        <div className="space-y-2">
          <Label>{t('jargonInference.scheduledChats', '定期处理的聊天（留空表示全部）')}</Label>
          <MultiSelect
            disabled={save.isPending}
            options={chats.map((chat) => ({
              value: chat.session_id,
              label: formatChatDisplayName(chat.chat_name, chat.account_id),
            }))}
            selected={settings.scheduled_session_ids}
            onChange={(values) => {
              setSettings({ ...settings, scheduled_session_ids: values })
              setSaved(false)
            }}
            placeholder={t('jargonInference.chooseChats', '选择聊天')}
          />
        </div>
        <p className="text-muted-foreground text-xs">
          {t(
            'jargonInference.evidenceHint',
            '关闭“仅处理有新证据”后可复用保留的聊天证据，适合更换模型后重新评估。人工维护和锁定的词条仍会跳过。'
          )}
        </p>
      </fieldset>
      {conflict && (
        <p role="alert" className="text-destructive text-sm">
          {t(
            'jargonInference.settingsConflict',
            '其他管理员修改了你正在编辑的设置。请重新加载并核对后再保存。'
          )}
        </p>
      )}
      {(conflict || save.error) && (
        <Button
          type="button"
          variant="outline"
          disabled={reload.isPending}
          onClick={() => reload.mutate()}
        >
          {t('jargonInference.reloadSettings', '放弃本地编辑并重新加载')}
        </Button>
      )}
      {(validationError || save.error || rescan.error || reload.error) && (
        <p role="alert" className="text-destructive text-sm">
          {validationError || save.error?.message || rescan.error?.message || reload.error?.message}
        </p>
      )}
      {rescan.isSuccess && rescan.data.total === 0 && (
        <p role="status">{t('jargonInference.noEligible', '当前没有符合阈值的待处理词条。')}</p>
      )}
      <p className="text-muted-foreground text-xs">
        {t(
          'jargonInference.retentionHint',
          '已结束任务到期后清理；活动任务保留。需要时可重新发起推断，清理本身不会调用模型。'
        )}
      </p>
      {saved && (
        <p role="status" className="text-sm text-green-600">
          {t('jargonInference.saved', '设置已保存并生效。')}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={save.isPending || conflict}>
          {t('jargonInference.saveSettings', '保存设置')}
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={rescan.isPending || save.isPending}
          onClick={() => rescan.mutate()}
        >
          {t('jargonInference.rescan', '按已保存阈值检查存量词条')}
        </Button>
      </div>
    </form>
  )
}
