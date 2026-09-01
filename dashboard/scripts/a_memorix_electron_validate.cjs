const { app, BrowserWindow } = require('electron')
const fs = require('fs')
const path = require('path')

const DASHBOARD_URL = process.env.MAIBOT_DASHBOARD_URL || 'http://127.0.0.1:7999'
const OUTPUT_DIR = process.env.MAIBOT_UI_SNAPSHOT_DIR
  || path.resolve(__dirname, '..', '..', 'tmp', 'ui-snapshots', 'a_memorix-electron')
const TOKEN_PATH = process.env.MAIBOT_WEBUI_TOKEN_PATH
  || path.resolve(__dirname, '..', '..', 'data', 'webui.json')
const sampleStamp = String(Date.now())
const sampleSource = process.env.MAIBOT_UI_SAMPLE_SOURCE || `webui-demo:a_memorix-json-${sampleStamp}`
const sampleName = process.env.MAIBOT_UI_SAMPLE_NAME || `webui-json-validation-${sampleStamp}.json`

const DEFAULT_SAMPLE = {
  paragraphs: [
    {
      content: 'Alice 在杭州西湖与 Bob 讨论 A_Memorix 的前端接入与 embedding 调优方案。',
      source: sampleSource,
      entities: ['Alice', 'Bob', '杭州西湖', 'A_Memorix'],
      relations: [
        { subject: 'Alice', predicate: '在', object: '杭州西湖' },
        { subject: 'Alice', predicate: '讨论', object: 'A_Memorix' },
        { subject: 'Bob', predicate: '讨论', object: 'A_Memorix' },
        { subject: 'Bob', predicate: '负责', object: 'embedding 调优' },
      ],
      knowledge_type: 'factual',
    },
  ],
  entities: ['Alice', 'Bob', '杭州西湖', 'A_Memorix', 'embedding 调优'],
  relations: [{ subject: 'Alice', predicate: '认识', object: 'Bob' }],
}

function loadSampleJson() {
  const customPath = String(process.env.MAIBOT_UI_IMPORT_JSON_PATH || '').trim()
  if (!customPath) {
    return JSON.stringify(DEFAULT_SAMPLE, null, 2)
  }
  return fs.readFileSync(customPath, 'utf8')
}

const sampleJson = loadSampleJson()

fs.mkdirSync(OUTPUT_DIR, { recursive: true })

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function exec(win, code) {
  return win.webContents.executeJavaScript(code, true)
}

async function waitFor(win, predicateCode, label, timeout = 30000, interval = 300) {
  const start = Date.now()
  while (Date.now() - start < timeout) {
    try {
      const ok = await exec(win, predicateCode)
      if (ok) {
        return ok
      }
    } catch {
      // keep polling
    }
    await wait(interval)
  }
  throw new Error(`Timeout waiting for ${label}`)
}

async function sendClick(win, x, y) {
  win.webContents.sendInputEvent({ type: 'mouseMove', x, y, movementX: 0, movementY: 0 })
  win.webContents.sendInputEvent({ type: 'mouseDown', x, y, button: 'left', clickCount: 1 })
  win.webContents.sendInputEvent({ type: 'mouseUp', x, y, button: 'left', clickCount: 1 })
}

async function capture(win, name) {
  const image = await win.webContents.capturePage()
  fs.writeFileSync(path.join(OUTPUT_DIR, name), image.toPNG())
  const text = await exec(win, 'document.body ? document.body.innerText : ""')
  fs.writeFileSync(path.join(OUTPUT_DIR, name.replace(/\.png$/, '.txt')), text || '')
}

async function getJson(win, relativePath) {
  return exec(
    win,
    `fetch(${JSON.stringify(relativePath)}, { credentials: 'include' }).then((r) => r.json())`,
  )
}

async function setSessionCookie(win) {
  const raw = fs.readFileSync(TOKEN_PATH, 'utf8')
  const config = JSON.parse(raw)
  const token = String(config.access_token || '').trim()
  if (!token) {
    throw new Error(`No access token found in ${TOKEN_PATH}`)
  }
  const payload = await exec(
    win,
    `fetch('/api/webui/auth/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ token: ${JSON.stringify(token)} }),
    }).then(async (response) => ({
      ok: response.ok,
      status: response.status,
      body: await response.json(),
    }))`,
  )
  if (!payload?.ok || !payload?.body?.valid) {
    throw new Error(`Failed to authenticate WebUI token via /auth/verify: ${JSON.stringify(payload)}`)
  }
}

async function openImportTab(win) {
  await exec(win, `(() => {
    const tab = Array.from(document.querySelectorAll('[role="tab"]')).find((el) => (el.textContent || '').trim() === '导入')
    if (!tab) return false
    tab.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true, pointerId: 1, button: 0, pointerType: 'mouse', isPrimary: true }))
    tab.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0 }))
    tab.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, button: 0 }))
    tab.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }))
    return true
  })()`)
  await waitFor(
    win,
    `document.body && document.body.innerText.includes('粘贴导入') && document.body.innerText.includes('创建导入任务')`,
    'import panel',
  )
}

async function setJsonMode(win) {
  const trigger = await exec(win, `(() => {
    const label = Array.from(document.querySelectorAll('label')).find((node) => (node.textContent || '').includes('输入模式'))
    const root = label?.closest('div')?.parentElement || label?.parentElement
    const button = root?.querySelector('button')
    if (!button) return null
    const rect = button.getBoundingClientRect()
    return { x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2) }
  })()`)
  if (!trigger) {
    throw new Error('select trigger not found')
  }
  await sendClick(win, trigger.x, trigger.y)
  await waitFor(win, `document.querySelectorAll('[role="option"]').length > 0`, 'select options', 5000, 200)

  const option = await exec(win, `(() => {
    const item = Array.from(document.querySelectorAll('[role="option"]')).find((el) => (el.textContent || '').trim() === 'json')
    if (!item) return null
    const rect = item.getBoundingClientRect()
    return { x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2) }
  })()`)
  if (!option) {
    throw new Error('json option not found')
  }
  await sendClick(win, option.x, option.y)
  await waitFor(
    win,
    `(() => {
      const label = Array.from(document.querySelectorAll('label')).find((node) => (node.textContent || '').includes('输入模式'))
      const root = label?.closest('div')?.parentElement || label?.parentElement
      const button = root?.querySelector('button')
      return (button?.textContent || '').trim() === 'json'
    })()`,
    'json mode selected',
    8000,
    300,
  )
}

async function typeIntoLabeled(win, labelText, selector, text) {
  const rect = await exec(win, `(() => {
    const label = Array.from(document.querySelectorAll('label')).find((node) => (node.textContent || '').includes(${JSON.stringify(labelText)}))
    const root = label?.closest('div')?.parentElement || label?.parentElement
    const el = root?.querySelector(${JSON.stringify(selector)})
    if (!el) return null
    const r = el.getBoundingClientRect()
    return { x: Math.round(r.left + 20), y: Math.round(r.top + 20) }
  })()`)
  if (!rect) {
    throw new Error(`field not found: ${labelText}`)
  }
  await sendClick(win, rect.x, rect.y)
  await wait(150)
  await win.webContents.insertText(text)
  await wait(250)
}

async function clickButton(win, text) {
  const ok = await exec(win, `(() => {
    const target = Array.from(document.querySelectorAll('button')).find((el) => (el.textContent || '').includes(${JSON.stringify(text)}))
    if (!target) return false
    target.scrollIntoView({ block: 'center' })
    target.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true, pointerId: 1, button: 0, pointerType: 'mouse', isPrimary: true }))
    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0 }))
    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, button: 0 }))
    target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }))
    return true
  })()`)
  if (!ok) {
    throw new Error(`button not found: ${text}`)
  }
}

async function clickTab(win, text) {
  const ok = await exec(win, `(() => {
    const target = Array.from(document.querySelectorAll('[role="tab"]')).find((el) => (el.textContent || '').includes(${JSON.stringify(text)}))
    if (!target) return false
    target.scrollIntoView({ block: 'center' })
    target.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true, pointerId: 1, button: 0, pointerType: 'mouse', isPrimary: true }))
    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0 }))
    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, button: 0 }))
    target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }))
    return true
  })()`)
  if (!ok) {
    throw new Error(`tab not found: ${text}`)
  }
}

async function clickGraphElement(win, selector, index = 0) {
  const rect = await exec(win, `(() => {
    const targets = Array.from(document.querySelectorAll(${JSON.stringify(selector)}))
    const target = targets[${index}]
    if (!target) return null
    target.scrollIntoView({ block: 'center', inline: 'center' })
    const r = target.getBoundingClientRect()
    return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }
  })()`)
  if (!rect) {
    throw new Error(`graph element not found: ${selector}[${index}]`)
  }
  await sendClick(win, rect.x, rect.y)
}

async function capturePluginFilterState(win) {
  await win.loadURL(`${DASHBOARD_URL}/plugin-config`)
  await waitFor(
    win,
    `document.body && document.body.innerText.includes('插件配置') && document.querySelector('input[placeholder="搜索插件..."]')`,
    'plugin config page',
    30000,
    400,
  )
  await exec(win, `(() => {
    const input = document.querySelector('input[placeholder="搜索插件..."]')
    if (!input) return false
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
    setter?.call(input, 'memorix')
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
    return true
  })()`)
  await wait(500)
  await capture(win, '01-plugin-config-filtered.png')
}

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1600,
    height: 1200,
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  await win.loadURL(`${DASHBOARD_URL}/auth`)
  await waitFor(win, `document.readyState === 'complete'`, 'auth page')
  await capture(win, '00-auth-login.png')
  await setSessionCookie(win)

  await capturePluginFilterState(win)

  await win.loadURL(`${DASHBOARD_URL}/resource/knowledge-base`)
  await waitFor(
    win,
    `document.querySelector('button[aria-label="更多操作"]') && document.querySelector('[role="tab"]')`,
    'memory console ready',
    30000,
    500,
  )
  await capture(win, '02-memory-console-before-import.png')

  const beforeGraph = await getJson(win, '/api/webui/memory/graph?limit=120')
  const beforeTasks = await getJson(win, '/api/webui/memory/import/tasks?limit=20')
  const knownTaskIds = new Set(
    Array.isArray(beforeTasks.items)
      ? beforeTasks.items.map((item) => String(item.task_id || item.taskId || ''))
      : [],
  )

  await openImportTab(win)
  await setJsonMode(win)
  await typeIntoLabeled(win, '名称', 'input', sampleName)
  await typeIntoLabeled(win, '粘贴内容', 'textarea', sampleJson)
  await capture(win, '03-memory-import-json-filled.png')

  await clickButton(win, '创建导入任务')

  let taskId = null
  let taskStatus = null
  const start = Date.now()
  while (Date.now() - start < 120000) {
    const payload = await getJson(win, '/api/webui/memory/import/tasks?limit=20')
    fs.writeFileSync(path.join(OUTPUT_DIR, 'tasks-last.json'), JSON.stringify(payload, null, 2))
    const items = Array.isArray(payload.items) ? payload.items : []
    const task = items.find((item) => !knownTaskIds.has(String(item.task_id || item.taskId || '')))
    if (task) {
      taskId = task.task_id || task.taskId || null
      taskStatus = task.status || null
      if (['completed', 'failed', 'cancelled'].includes(String(taskStatus))) {
        break
      }
    }
    await wait(1500)
  }

  if (!taskId) {
    throw new Error('new json import task not observed')
  }

  const detail = await getJson(
    win,
    `/api/webui/memory/import/tasks/${encodeURIComponent(taskId)}?include_chunks=true`,
  )
  fs.writeFileSync(path.join(OUTPUT_DIR, 'task-detail.json'), JSON.stringify(detail, null, 2))
  fs.writeFileSync(
    path.join(OUTPUT_DIR, 'task-status.txt'),
    `taskId=${taskId}\nstatus=${taskStatus}\nsource=${sampleSource}\n`,
  )

  await clickButton(win, '刷新数据')
  await wait(2000)
  await capture(win, '04-memory-console-after-import.png')

  await win.loadURL(`${DASHBOARD_URL}/resource/knowledge-graph`)
  await waitFor(
    win,
    `document.body && document.body.innerText.includes('长期记忆图谱') && document.body.innerText.includes('实体关系图') && document.body.innerText.includes('证据视图')`,
    'graph page ready',
    30000,
    400,
  )
  await wait(3000)
  const afterGraph = await getJson(win, '/api/webui/memory/graph?limit=120')
  fs.writeFileSync(path.join(OUTPUT_DIR, 'graph-after.json'), JSON.stringify(afterGraph, null, 2))
  await capture(win, '05-memory-graph-after-import.png')

  if (Array.isArray(afterGraph.nodes) && afterGraph.nodes.length > 0) {
    await clickGraphElement(win, '.react-flow__node', 0)
    await waitFor(win, `document.body && document.body.innerText.includes('实体详情')`, 'node detail dialog', 10000, 250)
    await capture(win, '06-memory-node-detail.png')
    try {
      await clickButton(win, '切到证据视图')
      await waitFor(
        win,
        `document.body && document.body.innerText.includes('证据视图') && document.querySelectorAll('.react-flow__node').length > 0`,
        'evidence graph after node click',
        10000,
        250,
      )
      await capture(win, '07-memory-evidence-view.png')
    } catch (error) {
      fs.writeFileSync(path.join(OUTPUT_DIR, '07-memory-evidence-view-error.txt'), String(error?.stack || error))
    }
  }

  if (Array.isArray(afterGraph.edges) && afterGraph.edges.length > 0) {
    try {
      await clickTab(win, '实体关系图')
      await wait(800)
      await clickGraphElement(win, '.react-flow__edge', 0)
      await waitFor(win, `document.body && document.body.innerText.includes('关系详情')`, 'edge detail dialog', 10000, 250)
      await capture(win, '08-memory-edge-detail.png')
    } catch (error) {
      fs.writeFileSync(path.join(OUTPUT_DIR, '08-memory-edge-detail-error.txt'), String(error?.stack || error))
    }
  }

  const summary = {
    before: {
      nodes: beforeGraph.total_nodes,
      edges: beforeGraph.total_edges,
    },
    after: {
      nodes: afterGraph.total_nodes,
      edges: afterGraph.total_edges,
    },
    taskId,
    taskStatus,
    source: sampleSource,
    inputMode: detail?.task?.files?.[0]?.input_mode || null,
    strategyType: detail?.task?.files?.[0]?.detected_strategy_type || null,
    fileStatus: detail?.task?.files?.[0]?.status || null,
    outputDir: OUTPUT_DIR,
  }
  fs.writeFileSync(path.join(OUTPUT_DIR, 'validation-summary.json'), JSON.stringify(summary, null, 2))
  console.log(JSON.stringify(summary, null, 2))

  await win.close()
  app.quit()
}).catch((error) => {
  console.error(error)
  app.exit(1)
})
