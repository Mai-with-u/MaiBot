import { describe, expect, it } from 'vitest'

import { getBotDisplayName, isNewerBotNicknameSample, readBotNicknameVersion, resolveBotNickname } from './bot-nickname'

describe('isNewerBotNicknameSample', () => {
  const versioned = { nickname: '春分', request: { startedAt: 100, order: 1 }, version: '2' }
  const legacy = { nickname: '小满', request: { startedAt: 200, order: 2 } }

  it('keeps a versioned nickname when an unversioned response follows it', () => {
    expect(isNewerBotNicknameSample(legacy, versioned)).toBe(false)
    expect(isNewerBotNicknameSample(versioned, legacy)).toBe(true)
  })

  it('uses request order when neither response has a valid version', () => {
    const unversioned = { ...versioned, version: readBotNicknameVersion('2.5') }
    expect(isNewerBotNicknameSample(legacy, unversioned)).toBe(true)
    expect(isNewerBotNicknameSample(unversioned, legacy)).toBe(false)
  })

  it('compares nanosecond versions without losing precision', () => {
    const earlier = { ...legacy, version: readBotNicknameVersion('1795500000000000000') }
    const later = { ...versioned, version: readBotNicknameVersion('1795500000000000100') }
    expect(isNewerBotNicknameSample(later, earlier)).toBe(true)
    expect(isNewerBotNicknameSample(earlier, later)).toBe(false)
    expect(readBotNicknameVersion(2)).toBe('2')
  })
})

describe('resolveBotNickname', () => {
  it('returns the configured nickname after trimming whitespace', () => {
    expect(resolveBotNickname({ bot: { nickname: '  小满  ' } }, '麦麦')).toBe('小满')
  })

  it('returns the fallback when nickname is missing or blank', () => {
    expect(resolveBotNickname({ bot: {} }, '麦麦')).toBe('麦麦')
    expect(resolveBotNickname({ bot: { nickname: '  ' } }, '麦麦')).toBe('麦麦')
    expect(resolveBotNickname(null, '麦麦')).toBe('麦麦')
  })
})

describe('getBotDisplayName', () => {
  it('uses the default name when config loaded successfully with an empty nickname', () => {
    expect(
      getBotDisplayName({ nickname: null, isResolved: true }, '小满', '麦麦')
    ).toBe('麦麦')
  })

  it('uses the known nickname when config could not be loaded', () => {
    expect(
      getBotDisplayName({ nickname: null, isResolved: false }, ' 小满 ', '麦麦')
    ).toBe('小满')
  })

  it('uses the configured nickname when available', () => {
    expect(
      getBotDisplayName({ nickname: '小满', isResolved: true }, '旧昵称', '麦麦')
    ).toBe('小满')
  })
})
