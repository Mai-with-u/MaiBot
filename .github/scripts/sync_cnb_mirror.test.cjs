const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { promisify } = require('node:util');
const exec = promisify(require('node:child_process').execFile);
const { configuration, redact, gitEnvironment, apiClient, ensureRepository,
  mirrorRepository, synchronize, DISABLED_BUILDS } = require('./sync_cnb_mirror.cjs');

test('MaiBot 源固定，组织和镜像名称校验，不需要插件仓库文件', () => {
  const config = configuration({ CNB_GROUP: 'Mai-with-u' });
  assert.equal(config.sourceUrl, 'https://github.com/Mai-with-u/MaiBot.git');
  assert.equal(config.mirrorUrl, 'https://cnb.cool/mai-with-u/maibot');
  assert.equal(configuration({ CNB_GROUP: 'Org/Sub', CNB_MAIBOT_REPO: 'Custom' }).mirrorPath, 'org/sub/custom');
  for (const group of ['', 'https://cnb.cool/test', 'test//mirror', '../test', 'test.git']) {
    assert.throws(() => configuration({ CNB_GROUP: group }));
  }
  assert.throws(() => configuration({ CNB_GROUP: 'org', CNB_MAIBOT_REPO: '../bad' }));
});

test('建仓和关闭构建必须在传输前成功，权限错误不能当成仓库不存在', async () => {
  const calls = [];
  await ensureRepository(async (...args) => {
    calls.push(args);
    if (args.length === 1) throw Object.assign(new Error('missing'), { status: 404 });
  }, 'org/maibot', 'source');
  assert.deepEqual(calls, [['org/maibot'], ['org/-/repos', 'POST', {
    name: 'maibot', visibility: 'public', description: 'MaiBot mirror of source',
  }], ['org/maibot/-/settings/cloud-native-build', 'PUT', DISABLED_BUILDS]]);
  const config = configuration({ CNB_GROUP: 'org', CNB_TOKEN: 'secret' });
  const failed = await synchronize(config, async () => { throw new Error('forbidden secret'); },
    () => assert.fail('设置失败不得上传'), 'temp');
  assert.equal(failed.status, 'failed');
  assert.equal(failed.error, 'forbidden [REDACTED]');
  assert.equal(failed.synced_at, undefined);
});

test('LFS 先于引用推送，失败不得发布新引用', async () => {
  const calls = [];
  await assert.rejects(mirrorRepository(async args => {
    calls.push(args);
    if (args[0] === 'symbolic-ref') return 'main';
    if (args[0] === 'lfs' && args[1] === 'push') throw new Error('LFS failed');
  }, 'source', 'mirror', 'temp'), /LFS failed/);
  assert.ok(!calls.some(args => args[0] === 'push'));
  assert.ok(calls.some(args => args[0] === 'config' && args[1] === 'lfs.pushurl' && args[2] === 'mirror.git/info/lfs'));
});

test('凭据限定到 CNB 并脱敏，API 不重定向、不盲目重试建仓', async () => {
  const env = gitEnvironment('secret');
  assert.equal(env.GIT_CONFIG_KEY_0, 'http.https://cnb.cool/.extraheader');
  assert.equal(env.CNB_TOKEN, undefined);
  assert.equal(env.GITHUB_TOKEN, undefined);
  assert.equal(redact(`secret ${Buffer.from('cnb:secret').toString('base64')}`, 'secret'), '[REDACTED] [REDACTED]');
  let calls = 0;
  const api = apiClient('secret', async (url, options) => {
    calls++;
    assert.equal(options.redirect, 'error');
    assert.equal(options.headers.Authorization, 'Bearer secret');
    throw new Error('timeout');
  });
  await assert.rejects(api('org/-/repos', 'POST', { name: 'maibot' }), /timeout/);
  assert.equal(calls, 1);
});

test('真实 Git 保留所有分支、注解 Tag 和 SHA，删除同步；状态记录真实 HEAD', async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'maibot-cnb-test-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const source = path.join(root, 'source');
  const target = path.join(root, 'target.git');
  const run = async (args, cwd) => (await exec('git', args, { cwd, env: { ...process.env,
    GIT_AUTHOR_NAME: 'Test', GIT_AUTHOR_EMAIL: 'test@example.com',
    GIT_COMMITTER_NAME: 'Test', GIT_COMMITTER_EMAIL: 'test@example.com' } })).stdout.trim();
  const git = async (args, cwd) => args[0] === 'lfs' ? '' : run(args, cwd);
  await fs.mkdir(source);
  await run(['init', '--initial-branch', 'main'], source);
  await fs.writeFile(path.join(source, 'test.txt'), 'source');
  await run(['add', '.'], source);
  await run(['commit', '-m', 'initial'], source);
  await run(['branch', 'dev'], source);
  await run(['tag', '-a', 'v1.0.0', '-m', 'release'], source);
  await run(['init', '--bare', '--initial-branch', 'main', target]);
  await run(['config', 'receive.advertisePushOptions', 'true'], target);
  const result = await synchronize({ sourceUrl: source, mirrorUrl: target, repositoryUrl: source, mirrorPath: 'org/maibot' },
    async () => {}, git, root, () => 'now');
  assert.equal(result.status, 'synced');
  assert.equal(result.synced_at, 'now');
  assert.equal(result.commit, await run(['rev-parse', 'main'], source));
  for (const ref of ['main', 'dev', 'v1.0.0']) {
    assert.equal(await run(['rev-parse', ref], target), await run(['rev-parse', ref], source));
  }
  await run(['branch', '-D', 'dev'], source);
  await run(['tag', '-d', 'v1.0.0'], source);
  await mirrorRepository(git, source, target, path.join(root, 'second.git'));
  await assert.rejects(run(['show-ref', '--verify', 'refs/heads/dev'], target));
  await assert.rejects(run(['show-ref', '--verify', 'refs/tags/v1.0.0'], target));
});

test('工作流覆盖分支、Tag、删除和自动提交，运行默认分支脚本且只读 GitHub', async () => {
  const workflow = await fs.readFile(path.join(__dirname, '../workflows/cnb-mirror.yml'), 'utf8');
  assert.match(workflow, /branches: \['\*\*'\]/);
  assert.match(workflow, /tags: \['\*\*'\]/);
  assert.match(workflow, /\n  delete:/);
  assert.match(workflow, /\n  workflow_run:/);
  assert.match(workflow, /'Publish MaiBot'/);
  assert.match(workflow, /ref: main/);
  assert.match(workflow, /persist-credentials: false/);
  assert.match(workflow, /contents: read/);
  assert.match(workflow, /cancel-in-progress: false/);
  assert.ok(!workflow.includes('plugin-repo'));
});
