import { describe, expect, it } from 'vitest'

import { describeRejectedReleaseError, shortenReleaseReason, shortenReleaseReasons } from '../release-reasons'

describe('describeRejectedReleaseError', () => {
  it('Tag 与 manifest.version 不一致：提取实际 Tag 与清单声明版本', () => {
    expect(
      describeRejectedReleaseError(
        "Tag 与 manifest.version 不一致\n+ actual - expected\n\n+ '1.4.6'\n- '0.11.0'"
      )
    ).toBe('Git Tag 1.4.6 与插件清单声明的版本 0.11.0 不一致')
  })

  it('Tag 与 manifest.version 不一致：断言 diff 带缩进指示行时同样可解析', () => {
    expect(
      describeRejectedReleaseError(
        "Tag 与 manifest.version 不一致\n+ actual - expected\n\n+ '0.1.0'\n- '0.1.1'\n       ^"
      )
    ).toBe('Git Tag 0.1.0 与插件清单声明的版本 0.1.1 不一致')
  })

  it('发布版本改变了插件 ID：按「原 ID → 新 ID」描述', () => {
    expect(
      describeRejectedReleaseError(
        "发布版本改变了插件 ID\n+ actual - expected\n\n+ 'taiyang.custom-commands-plugin'\n- 'TAIY2020.custom-commands-plugin'"
      )
    ).toBe('该发布版本把插件 ID 从 TAIY2020.custom-commands-plugin 改成了 taiyang.custom-commands-plugin')
  })

  it('不支持的 manifest 协议转为可读描述', () => {
    expect(describeRejectedReleaseError('不支持的 manifest 协议')).toBe(
      '该版本使用了不受支持的 manifest 协议版本'
    )
  })

  it('GitHub 404：指出缺失清单的 commit（短哈希）', () => {
    expect(
      describeRejectedReleaseError(
        'GitHub 404：/repos/Mai-with-u/MaiBot-Napcat-Adapter/contents/_manifest.json?ref=53dbbbd7a78400a820f23729318e5f47bf400f14'
      )
    ).toBe('该版本对应的 commit（53dbbbd）里找不到插件清单，可能已被删除或仓库改写过历史')
  })

  it('未识别的错误取首行透传，不掩盖问题', () => {
    expect(describeRejectedReleaseError('某个新错误\n第二行调试细节')).toBe('某个新错误')
  })

  it('空错误串给出占位说明', () => {
    expect(describeRejectedReleaseError('   ')).toBe('未知原因')
  })
})

describe('shortenReleaseReason', () => {
  it('Host 高于最大支持压缩为「仅支持麦麦 ≤ x」', () => {
    expect(shortenReleaseReason('Host 版本不兼容: 版本 1.3.0 高于最大支持 1.0.0 (当前 Host: 1.3.0)')).toBe(
      '仅支持麦麦 ≤ 1.0.0'
    )
  })

  it('Host 低于最小要求压缩为「需要麦麦 ≥ x」', () => {
    expect(shortenReleaseReason('Host 版本不兼容: 版本 1.3.0 低于最小要求 1.4.0 (当前 Host: 1.3.0)')).toBe(
      '需要麦麦 ≥ 1.4.0'
    )
  })

  it('SDK 高于最大支持与低于最小要求分别压缩', () => {
    expect(shortenReleaseReason('SDK 版本不兼容: 版本 2.8.2 高于最大支持 2.7.1 (当前 SDK: 2.8.2)')).toBe(
      '仅支持 SDK ≤ 2.7.1'
    )
    expect(shortenReleaseReason('SDK 版本不兼容: 版本 2.8.2 低于最小要求 2.9.0 (当前 SDK: 2.8.2)')).toBe(
      '需要 SDK ≥ 2.9.0'
    )
  })

  it('manifest 协议版本过旧', () => {
    expect(shortenReleaseReason('Manifest 版本不兼容: manifest_version=1，仅支持 2')).toBe(
      '清单协议版本过旧（需 2）'
    )
  })

  it('已撤回等短文案与未识别长文案原样返回', () => {
    expect(shortenReleaseReason('该版本已撤回')).toBe('该版本已撤回')

    const raw = '缺少必需字段: dependencies.0.python_package.version_spec'
    expect(shortenReleaseReason(raw)).toBe(raw)
  })
})

describe('shortenReleaseReasons', () => {
  it('多条原因逐条压缩后用「；」连接', () => {
    expect(
      shortenReleaseReasons([
        'Host 版本不兼容: 版本 1.3.0 高于最大支持 1.0.0 (当前 Host: 1.3.0)',
        'SDK 版本不兼容: 版本 2.8.2 高于最大支持 2.7.1 (当前 SDK: 2.8.2)',
      ])
    ).toBe('仅支持麦麦 ≤ 1.0.0；仅支持 SDK ≤ 2.7.1')
  })

  it('空数组返回空串', () => {
    expect(shortenReleaseReasons([])).toBe('')
  })
})
