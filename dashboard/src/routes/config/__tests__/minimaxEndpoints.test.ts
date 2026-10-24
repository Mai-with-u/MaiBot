import { describe, expect, it } from 'vitest'

import { resolveModelChoices, resolveModelFetcherTemplate, resolveThinkingFormatForModel } from '../providerTemplates'

const models = ['MiniMax-M2.7', 'MiniMax-M3', 'speech-02-hd', 'image-01'].map(
  (id) => ({ id, name: id })
)

describe('MiniMax direct endpoints', () => {
  it.each(['api.minimaxi.com', 'api.minimax.cn', 'api.minimax.io', 'api.minimax.chat'])(
    'applies Flash choices and controls for %s',
    (host) => {
      const template = resolveModelFetcherTemplate(`https://${host}/v1/`, 'openai')!
      expect(template.id).toBe('minimax')
      expect(resolveModelChoices(template, models).map((model) => model.id)).toEqual([
        'MiniMax-M3.1-Flash', 'MiniMax-M3', 'speech-02-hd', 'image-01',
      ])
      expect(resolveThinkingFormatForModel(template, 'MiniMax-M3.1-Flash')?.canDisable).toBe(false)
      expect(resolveThinkingFormatForModel(template, 'MiniMax-M3')?.canDisable).toBe(true)
      expect(models[0].id).toBe('MiniMax-M2.7')
    }
  )

  it.each(['custom.invalid', 'api.minimaxi.com.example.org', 'api.openai.com'])(
    'preserves existing choices for non-MiniMax endpoint %s',
    (host) => {
      const template = resolveModelFetcherTemplate(`https://${host}/v1`, 'openai')!
      expect(template.id).not.toBe('minimax')
      expect(resolveModelChoices(template, models)).toBe(models)
    }
  )
})
