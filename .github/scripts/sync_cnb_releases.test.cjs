const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { syncRelease, upload, sha256 } = require('./sync_cnb_releases.cjs');

const bytes = Buffer.from('installer');
const hash = createHash('sha256').update(bytes).digest('hex');
const source = { id: 3, tag_name: 'v1', name: '标题', body: '发布说明', prerelease: false, draft: false };
const asset = { name: 'pet.apk', state: 'uploaded', size: bytes.length,
  browser_download_url: 'https://github.com/asset', digest: `sha256:${hash}` };
const saved = { ...asset, hash_algo: 'sha256', hash_value: hash };

async function directory(t) {
  const result = await fs.mkdtemp(path.join(os.tmpdir(), 'cnb-release-test-'));
  t.after(() => fs.rm(result, { recursive: true, force: true }));
  return result;
}

test('创建缺失 Release，分页读取附件，上传并核对确认结果', async t => {
  const calls = [];
  let uploaded = false;
  const api = async (route, method = 'GET', body) => {
    calls.push([route, method, body]);
    if (method === 'GET' && !uploaded) throw Object.assign(new Error('missing'), { status: 404 });
    return { id: 'cnb1', assets: uploaded ? [saved] : [] };
  };
  const result = await syncRelease(source, 3, api, 'org/repo', await directory(t), async (route, paginate) => {
    assert.equal(route, 'releases/3/assets?per_page=100');
    assert.equal(paginate, true);
    return [asset];
  }, {
    download: async (_url, file) => fs.writeFile(file, bytes), sha256,
    upload: async () => { uploaded = true; },
  });
  assert.deepEqual(result.uploaded, ['pet.apk']);
  assert.deepEqual(calls[1], ['org/repo/-/releases', 'POST', {
    name: '标题', body: '发布说明', draft: false, prerelease: false,
    make_latest: 'true', tag_name: 'v1', target_commitish: 'v1',
  }]);
});

test('重跑时同内容附件跳过，只更新发布说明，旧版本不标记 latest', async t => {
  const calls = [];
  const result = await syncRelease(source, 4, async (...args) => {
    calls.push(args);
    return { id: 'cnb1', assets: [saved] };
  }, 'org/repo', await directory(t), async () => [asset], {
    download: () => assert.fail('无需重新下载'), upload: () => assert.fail('无需重新上传'), sha256,
  });
  assert.deepEqual(result.skipped, ['pet.apk']);
  assert.equal(calls[1][1], 'PATCH');
  assert.equal(calls[1][2].make_latest, 'false');
});

test('同名内容冲突明确报错，不覆盖附件', async t => {
  await assert.rejects(syncRelease(source, 3, async () => ({ id: 'cnb1', assets: [
    { ...saved, hash_value: '0'.repeat(64) },
  ] }), 'org/repo', await directory(t), async () => [asset], {
    download: async (_url, file) => fs.writeFile(file, bytes), sha256,
    upload: () => assert.fail('不得覆盖'),
  }), /同名附件/);
});

test('权限错误不得当成 Release 不存在', async t => {
  let calls = 0;
  await assert.rejects(syncRelease(source, 3, async () => {
    calls++;
    throw Object.assign(new Error('forbidden'), { status: 403 });
  }, 'org/repo', await directory(t)), /forbidden/);
  assert.equal(calls, 1);
});

test('下载内容必须符合 GitHub SHA256', async t => {
  await assert.rejects(syncRelease(source, 3, async () => ({ id: 'cnb1', assets: [] }),
    'org/repo', await directory(t), async () => [{ ...asset, digest: `sha256:${'0'.repeat(64)}` }], {
      download: async (_url, file) => fs.writeFile(file, bytes), sha256,
      upload: () => assert.fail('不得上传损坏文件'),
    }), /GitHub 附件校验失败/);
});

test('上传不转发凭据，成功 PUT 后调用确认，永久保存且不覆盖', async t => {
  const file = path.join(await directory(t), 'package');
  await fs.writeFile(file, bytes);
  const calls = [];
  await upload(async (...args) => {
    calls.push(args);
    return { upload_url: 'https://storage.example/upload',
      verify_url: 'https://api.cnb.cool/org/repo/-/releases/1/asset-upload-confirmation/token/file' };
  }, 'org/repo', '1', asset, file, async (url, options) => {
    assert.equal(url.origin, 'https://storage.example');
    assert.equal(options.headers.Authorization, undefined);
    assert.equal(options.redirect, 'error');
    const chunks = [];
    for await (const chunk of options.body) chunks.push(chunk);
    assert.deepEqual(Buffer.concat(chunks), bytes);
    assert.equal(calls.length, 1);
    return new Response('', { status: 200 });
  });
  assert.deepEqual(calls[0][2], { asset_name: 'pet.apk', size: bytes.length, overwrite: false, ttl: 0 });
  assert.deepEqual(calls[1], ['org/repo/-/releases/1/asset-upload-confirmation/token/file?ttl=0', 'POST']);
});

test('拒绝非 CNB 上传确认地址', async t => {
  await assert.rejects(upload(async () => ({ upload_url: 'https://storage.example/upload',
    verify_url: 'https://other.example/org/repo/-/releases/1/asset-upload-confirmation/token/file' }),
  'org/repo', '1', asset, path.join(await directory(t), 'unused'), () => assert.fail('不得上传')), /CNB API/);
});

test('上传失败不调用确认', async t => {
  let calls = 0;
  const file = path.join(await directory(t), 'package');
  await fs.writeFile(file, bytes);
  await assert.rejects(upload(async () => {
    calls++;
    return { upload_url: 'https://storage.example/upload',
      verify_url: '/org/repo/-/releases/1/asset-upload-confirmation/token/file' };
  }, 'org/repo', '1', asset, file, async (_url, options) => {
    for await (const _chunk of options.body) { /* consume upload stream */ }
    return new Response('', { status: 500 });
  }), /上传失败/);
  assert.equal(calls, 1);
});
