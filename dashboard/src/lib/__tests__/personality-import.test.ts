import { beforeEach, describe, expect, it } from 'vitest'
import {
  clearPersonalityImport, parsePersonalityImport, PERSONALITY_IMPORT_EXAMPLE, PERSONALITY_IMPORT_LIMIT,
  readPersonalityImport, rememberPersonalityImport,
} from '../personality-import'

type ImportFixture = { version: unknown; profile: Record<string, unknown>; config?: unknown }

describe('personality import', () => {
  beforeEach(() => sessionStorage.clear())

  it('accepts Chinese text and JSON code fences; defaults only optional fields', () => {
    const data = JSON.parse(PERSONALITY_IMPORT_EXAMPLE)
    delete data.profile.multiple_reply_style
    delete data.profile.multiple_probability
    const profile = parsePersonalityImport('```json\n' + JSON.stringify(data) + '\n```')
    expect(profile.name).toBe('温柔的麦麦')
    expect(profile.multiple_reply_style).toEqual([])
    expect(profile.multiple_probability).toBe(0)
    expect(profile.group_chat_prompt).toBe(data.profile.group_chat_prompt)
    expect(profile.private_chat_prompts).toBe(data.profile.private_chat_prompts)
  })


  it.each([
    (data: ImportFixture) => { data.version = 2 },
    (data: ImportFixture) => { delete data.profile.group_chat_prompt },
    (data: ImportFixture) => { delete data.profile.private_chat_prompts },
    (data: ImportFixture) => { data.profile.name = ' ' },
    (data: ImportFixture) => { delete data.profile.personality },
    (data: ImportFixture) => { data.profile.multiple_probability = 2 },
    (data: ImportFixture) => { data.profile.multiple_probability = '0.1' },
    (data: ImportFixture) => { data.profile.multiple_reply_style = [false] },
    (data: ImportFixture) => { data.profile.multiple_reply_style = null },
    (data: ImportFixture) => { data.profile.token = 'secret' },
    (data: ImportFixture) => { data.profile.group_chat_prompt = null },
    (data: ImportFixture) => { data.profile.private_chat_prompts = [] },
    (data: ImportFixture) => { data.config = {} },
  ])('rejects invalid or unexpected fields', (modify) => {
    const data = JSON.parse(PERSONALITY_IMPORT_EXAMPLE)
    modify(data)
    expect(() => parsePersonalityImport(JSON.stringify(data))).toThrow()
  })

  it('retains link content across authentication until explicitly dismissed', () => {
    const hash = new URLSearchParams({ 'personality-import': PERSONALITY_IMPORT_EXAMPLE }).toString()
    expect(rememberPersonalityImport('#' + hash)).toBe(true)
    expect(readPersonalityImport()).toBe(PERSONALITY_IMPORT_EXAMPLE)
    expect(rememberPersonalityImport('unrelated=1')).toBe(false)
    expect(parsePersonalityImport(readPersonalityImport()!).name).toBe('温柔的麦麦')
    clearPersonalityImport()
    expect(readPersonalityImport()).toBeNull()
  })

  it('rejects oversized content from both paste and external links', () => {
    const text = 'x'.repeat(PERSONALITY_IMPORT_LIMIT + 100)
    expect(() => parsePersonalityImport(text)).toThrow('65536')
    rememberPersonalityImport(new URLSearchParams({ 'personality-import': text }).toString())
    expect(readPersonalityImport()!.length).toBe(PERSONALITY_IMPORT_LIMIT + 1)
    expect(() => parsePersonalityImport(readPersonalityImport()!)).toThrow('65536')
  })
})
