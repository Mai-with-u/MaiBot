import { describe, expect, it } from 'vitest'

import { getBotDisplayName, resolveBotNickname } from './bot-nickname'

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
      getBotDisplayName({ nickname: null, isConfigLoaded: true }, '小满', '麦麦')
    ).toBe('麦麦')
  })

  it('uses the known nickname when config could not be loaded', () => {
    expect(
      getBotDisplayName({ nickname: null, isConfigLoaded: false }, ' 小满 ', '麦麦')
    ).toBe('小满')
  })

  it('uses the configured nickname when available', () => {
    expect(
      getBotDisplayName({ nickname: '小满', isConfigLoaded: true }, '旧昵称', '麦麦')
    ).toBe('小满')
  })
})
