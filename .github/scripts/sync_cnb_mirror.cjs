// Standalone CNB mirror transport; no runtime dependency on plugin-repo.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const exec = promisify(execFile);

const DISABLED_BUILDS = {
  auto_trigger: false, cron_auto_trigger: false,
  auto_trigger_by_npc: false, forked_repo_auto_trigger: false,
};

function slug(value, label) {
  assert.ok(typeof value === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9._-]*$/.test(value)
    && !value.includes('..') && !value.endsWith('.git'), `${label} 必须是有效的 CNB 路径段`);
  return value.toLowerCase();
}

function redact(value, token) {
  let text = String(value);
  if (token) {
    for (const secret of [token, Buffer.from(`cnb:${token}`).toString('base64')]) {
      text = text.split(secret).join('[REDACTED]');
    }
  }
  return text.slice(0, 3000);
}

function gitEnvironment(token) {
  // Scope credentials to CNB. Never store them in remotes, Git config files or argv.
  const env = { ...process.env, GIT_TERMINAL_PROMPT: '0', GIT_LFS_SKIP_SMUDGE: '1' };
  delete env.CNB_TOKEN;
  delete env.GITHUB_TOKEN;
  delete env.GH_TOKEN;
  // Prevent inherited tracing or config from exposing the authorization header.
  for (const key of Object.keys(env)) {
    if (key.startsWith('GIT_TRACE') || key.startsWith('GIT_CONFIG_') || key === 'GIT_CURL_VERBOSE') delete env[key];
  }
  Object.assign(env, {
    GIT_CONFIG_COUNT: '2', GIT_CONFIG_KEY_0: 'http.https://cnb.cool/.extraheader',
    GIT_CONFIG_VALUE_0: `Authorization: Basic ${Buffer.from(`cnb:${token}`).toString('base64')}`,
    GIT_CONFIG_KEY_1: 'credential.helper', GIT_CONFIG_VALUE_1: '',
  });
  return env;
}

function gitClient(token) {
  const env = gitEnvironment(token);
  return async (args, cwd) => {
    try {
      const result = await exec('git', args, { cwd, env, timeout: 15 * 60 * 1000, maxBuffer: 8 * 1024 * 1024 });
      return result.stdout.trim();
    } catch (error) {
      throw new Error(redact(`git ${args[0]} 失败：${error.stderr || error.message}`, token));
    }
  };
}

function apiClient(token, fetcher = fetch) {
  return async (route, method = 'GET', body) => {
    for (let attempt = 0; ; attempt++) {
      let response;
      try {
        response = await fetcher(`https://api.cnb.cool/${route}`, {
          method, redirect: 'error', signal: AbortSignal.timeout(60000),
          headers: { Authorization: `Bearer ${token}`, Accept: 'application/vnd.cnb.api+json',
            'Content-Type': 'application/json' },
          ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        });
      } catch (error) {
        // Do not retry a create with an unknown outcome; a rerun checks existence first.
        if (method === 'POST' || attempt >= 2) throw new Error(redact(error.message, token));
      }
      if ((!response || response.status === 429 || response.status >= 500) && attempt < 2 && method !== 'POST') {
        await new Promise(resolve => setTimeout(resolve, 1000 * 2 ** attempt));
        continue;
      }
      assert.ok(response, 'CNB API 无响应');
      if (!response.ok) {
        const error = new Error(`CNB API ${method} ${route}: HTTP ${response.status}`);
        error.status = response.status;
        throw error;
      }
      const text = await response.text();
      return text ? JSON.parse(text) : null;
    }
  };
}

async function ensureRepository(api, repo, sourceUrl) {
  try {
    await api(repo);
  } catch (error) {
    if (error.status !== 404) throw error;
    const split = repo.lastIndexOf('/');
    await api(`${repo.slice(0, split)}/-/repos`, 'POST', {
      name: repo.slice(split + 1), visibility: 'public', description: `MaiBot mirror of ${sourceUrl}`,
    });
  }
  // Must succeed before any third-party refs are uploaded, including on reruns.
  await api(`${repo}/-/settings/cloud-native-build`, 'PUT', DISABLED_BUILDS);
}

async function mirrorRepository(git, sourceUrl, mirrorUrl, directory) {
  // Bare cloning never checks out or executes source files, hooks or submodules.
  await git(['clone', '--bare', '--', sourceUrl, directory]);
  await git(['remote', 'add', 'cnb', mirrorUrl], directory);
  const branch = await git(['symbolic-ref', '--short', 'HEAD'], directory);
  await git(['lfs', 'fetch', '--all', 'origin'], directory);
  // A source .lfsconfig may point at GitHub or an external LFS service. Override
  // its push endpoint so reporting success always means objects reached CNB.
  const lfsTarget = `${mirrorUrl.replace(/\.git$/, '')}.git/info/lfs`;
  await git(['config', 'lfs.pushurl', lfsTarget], directory);
  await git(['config', 'remote.cnb.lfspushurl', lfsTarget], directory);
  await git(['lfs', 'push', '--all', 'cnb'], directory);
  // Push the default branch first so a new CNB repository can initialize its HEAD.
  await git(['push', '-o', 'ci.skip', 'cnb', `+refs/heads/${branch}:refs/heads/${branch}`], directory);
  // Only heads/tags are managed. Never push GitHub's internal pull-request refs.
  await git(['push', '--prune', '-o', 'ci.skip', 'cnb', '+refs/heads/*:refs/heads/*', '+refs/tags/*:refs/tags/*'], directory);
  return { default_branch: branch, commit: await git(['rev-parse', 'HEAD'], directory) };
}

function configuration(env = process.env) {
  assert.ok(env.CNB_GROUP, '请配置 CNB_GROUP');
  const group = env.CNB_GROUP.split('/').map(segment => slug(segment, 'CNB_GROUP')).join('/');
  const name = slug(env.CNB_MAIBOT_REPO || 'maibot', 'CNB_MAIBOT_REPO');
  return { token: env.CNB_TOKEN, repositoryUrl: 'https://github.com/Mai-with-u/MaiBot',
    sourceUrl: 'https://github.com/Mai-with-u/MaiBot.git',
    mirrorPath: `${group}/${name}`, mirrorUrl: `https://cnb.cool/${group}/${name}` };
}

async function synchronize(config, api, git, directory, now = () => new Date().toISOString()) {
  const info = { repositoryUrl: config.repositoryUrl, mirrorUrl: config.mirrorUrl };
  try {
    // Settings must be disabled before uploading the mirrored .cnb.yml or refs.
    await ensureRepository(api, config.mirrorPath, config.repositoryUrl);
    const result = await mirrorRepository(git, config.sourceUrl, config.mirrorUrl, path.join(directory, 'maibot.git'));
    return { ...info, status: 'synced', synced_at: now(), ...result };
  } catch (error) {
    return { ...info, status: 'failed', error: redact(error.message, config.token) };
  }
}

async function main() {
  const dryRun = process.argv.includes('--dry-run');
  assert.ok(process.argv.slice(2).every(arg => arg === '--dry-run'), '仅支持 --dry-run 参数');
  const config = configuration();
  if (dryRun) {
    const { token, ...plan } = config;
    console.log(JSON.stringify(plan, null, 2));
    return;
  }
  assert.ok(config.token, '请配置 MaiBot 仓库的 CNB_TOKEN Secret');
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'maibot-cnb-'));
  try {
    console.log(`同步 ${config.repositoryUrl} -> ${config.mirrorUrl}`);
    const result = await synchronize(config, apiClient(config.token), gitClient(config.token), directory);
    const report = { schema_version: 1, generated_at: new Date().toISOString(), maibot: result };
    await fs.mkdir('cnb-sync-output', { recursive: true });
    await fs.writeFile('cnb-sync-output/maibot.json', `${JSON.stringify(report, null, 2)}\n`);
    if (process.env.GITHUB_STEP_SUMMARY) {
      await fs.appendFile(process.env.GITHUB_STEP_SUMMARY,
        `MaiBot CNB 同步：${result.status}\n\n[镜像仓库](${config.mirrorUrl})\n\n` +
        (result.status === 'synced' ? `默认分支：\`${result.default_branch}\`；commit：\`${result.commit}\`\n` : '失败原因见同步状态附件与日志。\n'));
    }
    if (result.status !== 'synced') {
      console.error(result.error);
      process.exitCode = 1;
    }
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
}

module.exports = { configuration, redact, gitEnvironment, apiClient, ensureRepository,
  mirrorRepository, synchronize, DISABLED_BUILDS };
if (require.main === module) main().catch(error => {
  console.error(redact(error.message, process.env.CNB_TOKEN));
  process.exitCode = 1;
});
