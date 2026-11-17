// CNB API contract: https://api.cnb.cool/swagger.json (Releases).
const assert = require('node:assert/strict');
const { execFile } = require('node:child_process');
const { createHash } = require('node:crypto');
const { createReadStream, createWriteStream } = require('node:fs');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { Readable } = require('node:stream');
const { pipeline } = require('node:stream/promises');
const { promisify } = require('node:util');
const { apiClient, configuration, redact } = require('./sync_cnb_mirror.cjs');
const exec = promisify(execFile);
const sourceRepo = 'Mai-with-u/MaiBot';

async function github(route, paginate = false) {
  const { stdout } = await exec('gh', ['api', ...(paginate ? ['--paginate', '--slurp'] : []),
    `repos/${sourceRepo}/${route}`], { maxBuffer: 32 * 1024 * 1024 });
  const result = JSON.parse(stdout);
  return paginate ? result.flat() : result;
}

function httpsUrl(value) {
  const url = new URL(value);
  assert.equal(url.protocol, 'https:', '附件地址必须使用 HTTPS');
  assert.ok(!url.username && !url.password, '附件地址不能包含凭据');
  return url;
}

async function download(url, destination, fetcher = fetch) {
  // Public release downloads need no credential; never forward CNB/GitHub tokens to storage.
  const response = await fetcher(httpsUrl(url), { signal: AbortSignal.timeout(15 * 60 * 1000) });
  assert.ok(response.ok, `附件下载失败：HTTP ${response.status}`);
  await pipeline(Readable.fromWeb(response.body), createWriteStream(destination));
}

async function sha256(file) {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  return hash.digest('hex');
}

async function upload(api, repo, releaseId, asset, file, fetcher = fetch) {
  const route = `${repo}/-/releases/${encodeURIComponent(releaseId)}`;
  const ticket = await api(`${route}/asset-upload-url`, 'POST', {
    asset_name: asset.name, size: asset.size, overwrite: false, ttl: 0,
  });
  const verify = new URL(ticket.verify_url, 'https://api.cnb.cool/');
  assert.equal(verify.origin, 'https://api.cnb.cool', '确认地址必须属于 CNB API');
  assert.ok(verify.pathname.startsWith(`/${route}/asset-upload-confirmation/`), '上传确认地址与目标 Release 不符');
  const response = await fetcher(httpsUrl(ticket.upload_url), {
    method: 'PUT', redirect: 'error', body: createReadStream(file), duplex: 'half',
    headers: { 'Content-Length': String(asset.size), 'Content-Type': 'application/octet-stream' },
    signal: AbortSignal.timeout(15 * 60 * 1000),
  });
  assert.ok(response.ok, `CNB 附件上传失败：HTTP ${response.status}`);
  await response.arrayBuffer();
  verify.searchParams.set('ttl', '0');
  await api(`${verify.pathname.slice(1)}${verify.search}`, 'POST');
}

async function syncRelease(source, latestId, api, repo, directory, gh = github,
  transfer = { download, upload, sha256 }) {
  assert.ok(!source.draft, '不允许同步草稿 Release');
  const base = `${repo}/-/releases`;
  const tagRoute = `${base}/tags/${encodeURIComponent(source.tag_name)}`;
  const metadata = { name: source.name || source.tag_name, body: source.body || '',
    draft: false, prerelease: source.prerelease, make_latest: source.id === latestId ? 'true' : 'false' };
  let target;
  try {
    target = await api(tagRoute);
  } catch (error) {
    if (error.status !== 404) throw error;
  }
  if (!target) {
    // Git refs have already been mirrored; target an existing tag, never a moving branch.
    target = await api(base, 'POST', { ...metadata, tag_name: source.tag_name,
      target_commitish: source.tag_name });
  } else {
    await api(`${base}/${encodeURIComponent(target.id)}`, 'PATCH', metadata);
  }
  const assets = await gh(`releases/${source.id}/assets?per_page=100`, true);
  const existing = new Map((target.assets || []).map(asset => [asset.name, asset]));
  const report = { tag: source.tag_name, uploaded: [], skipped: [] };
  for (const asset of assets) {
    assert.equal(asset.state, 'uploaded', `GitHub 附件尚未上传完成：${asset.name}`);
    const prior = existing.get(asset.name);
    const sourceDigest = /^sha256:[a-f0-9]{64}$/.test(asset.digest) ? asset.digest.slice(7) : null;
    const priorDigest = prior?.hash_algo?.toLowerCase() === 'sha256' ? prior.hash_value.toLowerCase() : null;
    if (prior && sourceDigest && priorDigest === sourceDigest && prior.size === asset.size) {
      report.skipped.push(asset.name);
      continue;
    }
    // Fixed temporary names prevent release filenames from becoming local paths.
    const file = path.join(directory, 'source-asset');
    await transfer.download(asset.browser_download_url, file);
    assert.equal((await fs.stat(file)).size, asset.size, `GitHub 附件大小不符：${asset.name}`);
    const hash = await transfer.sha256(file);
    if (sourceDigest) assert.equal(hash, sourceDigest, `GitHub 附件校验失败：${asset.name}`);
    if (prior) {
      let oldHash = priorDigest;
      if (!oldHash) {
        const oldFile = path.join(directory, 'target-asset');
        await transfer.download(prior.browser_download_url, oldFile);
        oldHash = await transfer.sha256(oldFile);
        await fs.unlink(oldFile);
      }
      assert.ok(prior.size === asset.size && oldHash === hash, `CNB 已有不同内容的同名附件：${asset.name}`);
      report.skipped.push(asset.name);
    } else {
      await transfer.upload(api, repo, target.id, asset, file);
      const confirmed = await api(tagRoute);
      const saved = (confirmed.assets || []).find(item => item.name === asset.name);
      assert.ok(saved && saved.size === asset.size, `CNB 附件确认失败：${asset.name}`);
      if (saved.hash_algo?.toLowerCase() === 'sha256') {
        assert.equal(saved.hash_value.toLowerCase(), hash, `CNB 附件校验失败：${asset.name}`);
      } else {
        const confirmedFile = path.join(directory, 'target-asset');
        await transfer.download(saved.browser_download_url, confirmedFile);
        assert.equal(await transfer.sha256(confirmedFile), hash, `CNB 附件校验失败：${asset.name}`);
        await fs.unlink(confirmedFile);
      }
      report.uploaded.push(asset.name);
    }
    await fs.unlink(file);
  }
  return report;
}

async function main() {
  const config = configuration();
  assert.ok(config.token, '请配置 CNB_TOKEN（需 repo-release:rw 权限）');
  const api = apiClient(config.token);
  const releases = (await github('releases?per_page=100', true)).filter(release => !release.draft);
  const latest = releases.some(release => !release.prerelease) ? await github('releases/latest') : null;
  // Latest is processed last so backfilling older releases cannot change the latest pointer.
  releases.sort((a, b) => Number(a.id === latest?.id) - Number(b.id === latest?.id));
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'maibot-cnb-releases-'));
  const results = [];
  try {
    for (const source of releases) {
      try {
        const result = await syncRelease(source, latest?.id, api, config.mirrorPath, directory);
        results.push({ ...result, status: 'synced' });
        console.log(`${source.tag_name}: 上传 ${result.uploaded.length}，已同步 ${result.skipped.length}`);
      } catch (error) {
        // Record each failure and keep other releases independent; the workflow still fails.
        results.push({ tag: source.tag_name, status: 'failed', error: redact(error.message, config.token) });
        process.exitCode = 1;
      }
    }
    await fs.mkdir('cnb-sync-output', { recursive: true });
    await fs.writeFile('cnb-sync-output/releases.json', JSON.stringify({ results }, null, 2));
    if (process.env.GITHUB_STEP_SUMMARY) {
      await fs.appendFile(process.env.GITHUB_STEP_SUMMARY, `CNB Release 同步：${config.mirrorUrl}\n\n` +
        results.map(item => `- ${item.tag}: ${item.status}`).join('\n') + '\n');
    }
    for (const result of results.filter(item => item.status === 'failed')) console.error(`${result.tag}: ${result.error}`);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
}

module.exports = { syncRelease, upload, download, sha256 };
if (require.main === module) main().catch(error => {
  console.error(redact(error.message, process.env.CNB_TOKEN));
  process.exitCode = 1;
});
