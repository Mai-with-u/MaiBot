/**
 * 发布版本兼容性文案转换。
 *
 * 两个来源的原文都是面向开发的调试文本，直接铺到界面上不可读：
 * - 上游版本索引把同步工具的 assert 断言 diff 原样写进 `rejected_releases`
 *   （如 `Tag 与 manifest.version 不一致\n+ actual - expected\n\n+ '1.4.6'\n- '0.11.0'`）；
 * - Manifest 校验器的原因带冗余前缀与重复的当前版本
 *   （如 `Host 版本不兼容: 版本 1.3.0 高于最大支持 1.0.0 (当前 Host: 1.3.0)`）。
 *
 * 这里统一转成可读中文；识别不了的文案按原样透传（只取首行），不掩盖未知问题。
 */

/** 断言 diff 里的 `+ actual - expected`：第一个带引号的是实际值，第二个是期望值 */
const ACTUAL_EXPECTED_PATTERN = /\+\s*'([^']*)'[\s\S]*?-\s*'([^']*)'/

/** `...?ref=<sha>` 里的 commit */
const GITHUB_REF_PATTERN = /ref=([0-9a-f]{7,40})/

/** 被版本索引驳回的发布版本：原始 error → 可读原因 */
export function describeRejectedReleaseError(error: string): string {
  const actualExpected = ACTUAL_EXPECTED_PATTERN.exec(error)

  if (error.includes('Tag 与 manifest.version 不一致')) {
    return actualExpected
      ? `Git Tag ${actualExpected[1]} 与插件清单声明的版本 ${actualExpected[2]} 不一致`
      : 'Git Tag 与插件清单声明的版本不一致'
  }

  if (error.includes('发布版本改变了插件 ID')) {
    return actualExpected
      ? `该发布版本把插件 ID 从 ${actualExpected[2]} 改成了 ${actualExpected[1]}`
      : '该发布版本改变了插件 ID'
  }

  if (error.includes('不支持的 manifest 协议')) {
    return '该版本使用了不受支持的 manifest 协议版本'
  }

  const commitRef = GITHUB_REF_PATTERN.exec(error)
  if (commitRef) {
    const shortCommit = commitRef[1].slice(0, 7)
    return `该版本对应的 commit（${shortCommit}）里找不到插件清单，可能已被删除或仓库改写过历史`
  }

  // 未识别的错误保持原样，仅取首行，避免把多行断言输出摊在界面上
  return error.split('\n')[0].trim() || '未知原因'
}

/** 版本下拉选项用的短原因：压缩校验器的长文案，无法识别时原样返回 */
export function shortenReleaseReason(reason: string): string {
  const hostAboveMax = /^Host 版本不兼容: 版本 \S+ 高于最大支持 (\S+) \(当前 Host: \S+\)$/.exec(reason)
  if (hostAboveMax) {
    return `仅支持麦麦 ≤ ${hostAboveMax[1]}`
  }

  const hostBelowMin = /^Host 版本不兼容: 版本 \S+ 低于最小要求 (\S+) \(当前 Host: \S+\)$/.exec(reason)
  if (hostBelowMin) {
    return `需要麦麦 ≥ ${hostBelowMin[1]}`
  }

  const sdkAboveMax = /^SDK 版本不兼容: 版本 \S+ 高于最大支持 (\S+) \(当前 SDK: \S+\)$/.exec(reason)
  if (sdkAboveMax) {
    return `仅支持 SDK ≤ ${sdkAboveMax[1]}`
  }

  const sdkBelowMin = /^SDK 版本不兼容: 版本 \S+ 低于最小要求 (\S+) \(当前 SDK: \S+\)$/.exec(reason)
  if (sdkBelowMin) {
    return `需要 SDK ≥ ${sdkBelowMin[1]}`
  }

  const manifestVersion = /^Manifest 版本不兼容: manifest_version=.+，仅支持 (.+)$/.exec(reason)
  if (manifestVersion) {
    return `清单协议版本过旧（需 ${manifestVersion[1]}）`
  }

  return reason
}

/** 版本下拉选项的原因整段：逐条压缩后用「；」连接 */
export function shortenReleaseReasons(reasons: string[]): string {
  return reasons.map(shortenReleaseReason).join('；')
}
