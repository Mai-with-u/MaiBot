import type { ComponentProps, FocusEvent } from 'react'
import { useState } from 'react'

import { Input } from '@/components/ui/input'

type DraftNumberInputProps = Omit<
  ComponentProps<typeof Input>,
  'defaultValue' | 'onChange' | 'type' | 'value'
> & {
  defaultValue?: unknown
  integer?: boolean
  onValueChange: (value: number) => void
  value: unknown
}

function normalizeNumericValue(value: number, integer: boolean) {
  return integer ? Math.trunc(value) : value
}

function parseNumericValue(value: unknown, defaultValue: unknown, integer: boolean): number {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return normalizeNumericValue(value, integer)
  }

  if (typeof value === 'string' && value.trim()) {
    const parsedValue = Number(value)
    if (Number.isFinite(parsedValue)) {
      return normalizeNumericValue(parsedValue, integer)
    }
  }

  if (defaultValue !== undefined && defaultValue !== value) {
    return parseNumericValue(defaultValue, undefined, integer)
  }

  return 0
}

function parseDraftValue(draftValue: string, integer: boolean) {
  if (!draftValue.trim()) {
    return undefined
  }

  const parsedValue = Number(draftValue)
  if (!Number.isFinite(parsedValue)) {
    return undefined
  }

  return normalizeNumericValue(parsedValue, integer)
}

export function DraftNumberInput({
  defaultValue,
  integer = false,
  onBlur,
  onFocus,
  onValueChange,
  value,
  ...props
}: DraftNumberInputProps) {
  const numericValue = parseNumericValue(value, defaultValue, integer)
  const [draftValue, setDraftValue] = useState(() => String(numericValue))
  const [seenNumericValue, setSeenNumericValue] = useState(numericValue)
  const [focused, setFocused] = useState(false)
  if (numericValue !== seenNumericValue) {
    setSeenNumericValue(numericValue)
    if (!focused) {
      setDraftValue(String(numericValue))
    }
  }

  const commitDraftValue = (nextDraftValue: string) => {
    setDraftValue(nextDraftValue)

    const nextValue = parseDraftValue(nextDraftValue, integer)
    if (nextValue === undefined) {
      return
    }

    onValueChange(nextValue)
  }

  const canonicalizeDraftValue = (event: FocusEvent<HTMLInputElement>) => {
    setFocused(false)

    const nextValue = parseDraftValue(draftValue, integer)
    if (nextValue === undefined) {
      setDraftValue(String(numericValue))
      onBlur?.(event)
      return
    }

    if (nextValue !== numericValue) {
      onValueChange(nextValue)
    }
    setDraftValue(String(nextValue))
    onBlur?.(event)
  }

  const handleFocus = (event: FocusEvent<HTMLInputElement>) => {
    setFocused(true)
    onFocus?.(event)
  }

  return (
    <Input
      {...props}
      type="number"
      value={draftValue}
      onBlur={canonicalizeDraftValue}
      onChange={(event) => commitDraftValue(event.target.value)}
      onFocus={handleFocus}
    />
  )
}
