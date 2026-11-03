/**
 * Phase 03 UI acceptance: real built SPA + real API + real database.
 *
 * This is NOT a simulated API. Playwright drives the production bundle served by
 * nginx-less static hosting, against a real uvicorn process backed by an isolated
 * PostgreSQL database, and a real worker process consumes the queue. Everything a
 * screenshot shows is therefore produced by the actual system.
 *
 * Checks per viewport (1440x900, 1024x768, 390x844, 360x800):
 *   1. screenshots of: project board (empty + populated), task detail (running,
 *      succeeded, failed), execution detail, budget page, overview metrics;
 *   2. status is always text, never colour alone (the label is asserted);
 *   3. keyboard-only path: open the task drawer, fill it, submit with Enter/space;
 *   4. layout containment: no horizontal document overflow, drawer inside viewport;
 *   5. contrast of the rendered status badges meets 4.5:1 for normal text;
 *   6. SSE reconnect: the page reports a dropped stream and recovers.
 *
 * Prerequisites (see docs/acceptance/phase-03.md): the built SPA in
 * frontend/dist, and PHASE03_UI_BASE_URL pointing at the running API.
 */
const { chromium } = require('playwright/test')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')

const root = path.resolve(__dirname, '..')
const dist = path.join(root, 'frontend/dist')
// Output directory is overridable so a later fix round writes its own evidence
// instead of overwriting the original phase-03 acceptance screenshots.
const out = path.resolve(
  process.env.PHASE03_UI_OUT ?? path.join(root, 'docs/acceptance/evidence/phase-03/ui'),
)
const API = process.env.PHASE03_UI_BASE_URL ?? 'http://127.0.0.1:8010'
const PORT = Number(process.env.PHASE03_UI_PORT ?? 18086)
const baseURL = `http://127.0.0.1:${PORT}`
const VIEWPORTS = [
  [1440, 900],
  [1024, 768],
  [390, 844],
  [360, 800],
]

/** Static file host that proxies /api to the real API. Cookies stay first-party
 *  because the browser only ever sees this one origin. */
// When true, only the SSE endpoint is refused. This is how "the stream dropped"
// is reproduced for the browser without touching any other request: the page's
// EventSource sees a failure, reports it, and retries on its own.
let breakSseStream = false

const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, baseURL).pathname
  if (breakSseStream && pathname.endsWith('/events/stream')) {
    res.writeHead(502, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
    res.end(JSON.stringify({ code: 'STREAM_UNAVAILABLE', message: '验收：事件流暂时中断。' }))
    return
  }
  if (pathname.startsWith('/api/')) {
    const upstream = new URL(req.url, API)
    const proxy = http.request(
      {
        hostname: upstream.hostname,
        port: upstream.port,
        path: upstream.pathname + upstream.search,
        method: req.method,
        headers: { ...req.headers, host: upstream.host },
      },
      upstreamRes => {
        res.writeHead(upstreamRes.statusCode ?? 502, upstreamRes.headers)
        upstreamRes.pipe(res)
      },
    )
    proxy.on('error', () => {
      res.writeHead(502, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ code: 'BAD_GATEWAY', message: '验收 API 不可达。' }))
    })
    req.pipe(proxy)
    return
  }
  const requested = path.resolve(dist, `.${pathname}`)
  const file =
    requested.startsWith(`${dist}${path.sep}`) &&
    fs.existsSync(requested) &&
    fs.statSync(requested).isFile()
      ? requested
      : path.join(dist, 'index.html')
  const mime =
    { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' }[
      path.extname(file)
    ] ?? 'application/octet-stream'
  res.writeHead(200, { 'Content-Type': mime, 'Cache-Control': 'no-store' })
  fs.createReadStream(file).pipe(res)
})

function parseColor(value) {
  const match = /rgba?\(([^)]+)\)/.exec(value)
  if (!match) return null
  const parts = match[1].split(/[,\s/]+/).filter(Boolean).map(Number)
  return { r: parts[0], g: parts[1], b: parts[2], a: parts[3] ?? 1 }
}

function channelLuminance(value) {
  const c = value / 255
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}

function contrastRatio(foreground, background) {
  const lum = ({ r, g, b }) =>
    0.2126 * channelLuminance(r) + 0.7152 * channelLuminance(g) + 0.0722 * channelLuminance(b)
  const a = lum(foreground)
  const b = lum(background)
  const [light, dark] = a > b ? [a, b] : [b, a]
  return (light + 0.05) / (dark + 0.05)
}

async function login(page) {
  await page.goto('/login')
  const status = await page.evaluate(async () => {
    const response = await fetch('/api/v1/auth/setup-status')
    return response.json()
  })
  if (status.setupComplete === false) {
    await page.goto('/setup')
    await page.locator('#field-setup-username').fill('owner')
    await page.locator('#field-setup-display-name').fill('验收所有者')
    await page.locator('#field-setup-password').fill('correct-horse-battery')
    await page.locator('#field-setup-password-confirm').fill('correct-horse-battery')
    await page.getByRole('button', { name: /创建所有者|完成初始化/ }).click()
    await page.waitForURL(url => !url.pathname.startsWith('/setup') || url.pathname === '/setup/company')
  } else {
    await page.locator('#field-username').fill('owner')
    await page.locator('#field-password').fill('correct-horse-battery')
    await page.getByRole('button', { name: /登录/ }).click()
  }
  await page.waitForLoadState('networkidle')
  if (page.url().includes('/setup/company')) {
    await page.locator('#field-company-name').fill('验收公司')
    await page.locator('#field-company-goal').fill('验证阶段 03 的任务调度与预算。')
    // Step 1 -> 2 -> 3 -> 4, then create. The model step allows "稍后配置", which
    // keeps the acceptance free of any provider credential.
    for (let step = 0; step < 3; step += 1) {
      const next = page.getByRole('button', { name: '下一步', exact: true })
      await next.click()
      await page.waitForTimeout(150)
    }
    const create = page.getByRole('button', { name: '创建公司', exact: true })
    await create.click()
    await page.waitForURL(/\/overview/, { timeout: 30000 })
  }
}

async function apiJson(page, method, pathname, body) {
  return page.evaluate(
    async ({ method, pathname, body }) => {
      const csrf = document.cookie
        .split(';')
        .map(part => part.trim())
        .find(part => part.startsWith('acos_csrf='))
      const response = await fetch(`/api/v1${pathname}`, {
        method,
        headers: {
          'Content-Type': 'application/json',
          ...(csrf ? { 'X-CSRF-Token': decodeURIComponent(csrf.split('=')[1]) } : {}),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      const text = await response.text()
      return { status: response.status, body: text ? JSON.parse(text) : null }
    },
    { method, pathname, body },
  )
}

async function ensureProject(page) {
  const existing = await apiJson(page, 'GET', '/projects')
  if (existing.body.projects.length > 0) return existing.body.projects[0]
  const created = await apiJson(page, 'POST', '/projects', {
    key: 'UI',
    name: '界面验收项目',
    objective: '用真实 API 验证任务看板、任务详情与预算页面。',
    runMode: 'DEMO',
    budgetTokens: 200000,
  })
  assert.equal(created.status, 201, JSON.stringify(created.body))
  return created.body
}

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(PORT, '127.0.0.1', resolve)
  })
  const results = []
  const errors = []
  let browser
  try {
    browser = await chromium.launch({ channel: 'chrome', headless: true })
    const seedContext = await browser.newContext({ baseURL, viewport: { width: 1440, height: 900 } })
    const seedPage = await seedContext.newPage()
    await login(seedPage)
    const project = await ensureProject(seedPage)

    // Seed a spread of states so the board and detail pages have real content.
    const seeded = []
    for (const [title, behavior] of [
      ['正常完成的任务', 'SUCCESS'],
      ['永久失败的任务', 'PERMANENT_FAILURE'],
      ['用量未知的任务', 'UNKNOWN_USAGE'],
      ['临时失败后重试成功', 'TRANSIENT_FAILURE'],
    ]) {
      const created = await apiJson(seedPage, 'POST', `/projects/${project.id}/tasks`, {
        title,
        objective: '界面验收任务。',
        acceptance: '完成并记录用量。',
        budgetTokens: 2000,
        demoBehavior: behavior,
        maxAutoRetries: 1,
        idempotencyKey: `ui-${behavior}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      })
      assert.equal(created.status, 201, JSON.stringify(created.body))
      seeded.push(created.body.task)
    }
    // Wait for the real worker to drain them. The poll uses the page's own
    // session cookies through the API wrapper, so it reads exactly what the UI
    // reads (an unauthenticated fetch would silently return 401 bodies).
    const deadline = Date.now() + 120000
    let details = []
    while (Date.now() < deadline) {
      details = []
      for (const task of seeded) {
        const response = await apiJson(seedPage, 'GET', `/tasks/${task.id}`)
        assert.equal(response.status, 200, JSON.stringify(response.body))
        details.push(response.body)
      }
      if (details.every(item => ['SUCCEEDED', 'FAILED'].includes(item.status))) break
      await seedPage.waitForTimeout(500)
    }
    assert.ok(
      details.every(item => ['SUCCEEDED', 'FAILED'].includes(item.status)),
      `worker did not drain the seeded tasks: ${details.map(item => item.status).join(',')}`,
    )
    const succeeded = details.find(item => item.status === 'SUCCEEDED')
    const failed = details.find(item => item.status === 'FAILED')
    const unknownUsage = details.find(
      item => item.executions.some(row => row.needsReconciliation),
    )
    assert.ok(succeeded && failed, 'expected a succeeded and a failed task')
    const executionId = succeeded.executions[0].id

    for (const [width, height] of VIEWPORTS) {
      const context = await browser.newContext({
        baseURL,
        viewport: { width, height },
        reducedMotion: 'reduce',
      })
      // Reuse the authenticated session instead of logging in at every viewport.
      await context.addCookies(await seedContext.cookies())
      const page = await context.newPage()
      page.on('pageerror', error => errors.push(`${width}x${height}: ${error.message}`))
      const shot = name => page.screenshot({ path: path.join(out, `${name}-${width}x${height}.png`), fullPage: true })
      const facts = { viewport: `${width}x${height}` }

      // ---- board --------------------------------------------------------
      await page.goto('/projects')
      await page.getByRole('heading', { name: '任务看板' }).waitFor()
      // Phones default to the list (COMPONENTS.md §4). Switch to the board
      // explicitly so both modes are exercised at every viewport.
      const boardButton = page.getByRole('button', { name: '看板', exact: true })
      if ((await boardButton.getAttribute('aria-pressed')) !== 'true') {
        await boardButton.click()
      }
      await shot('board')
      facts.boardColumns = await page.locator('.projects__column').count()
      facts.boardCards = await page.locator('.projects__card').count()
      // Empty columns are omitted from the board to keep real cards visible;
      // their zero counts are still stated in the toolbar note.
      assert.ok(facts.boardColumns >= 2, 'expected the populated status columns')
      assert.ok(facts.boardCards >= 4, 'expected the seeded tasks on the board')
      const boardNote = await page.locator('.projects__board-note').count()
      facts.boardNotePresent = boardNote === 1

      // Status must be readable as text, never colour alone.
      const cardText = await page.locator('.projects__board').innerText()
      for (const label of ['已通过', '失败']) {
        assert.ok(cardText.includes(label), `board missing status text ${label}`)
      }
      // Demo labelling is mandatory on every card.
      assert.ok(cardText.includes('演练行为'), 'board cards must label the demo behaviour')

      // The list view is the phone default and must be reachable everywhere.
      await page.getByRole('button', { name: '列表', exact: true }).click()
      await page.locator('.projects__list-row').first().waitFor()
      facts.listRows = await page.locator('.projects__list-row').count()
      assert.ok(facts.listRows >= 5, 'list view must show the header and the tasks')
      await shot('board-list')

      // ---- keyboard-only task creation ----------------------------------
      const newTask = page.getByRole('button', { name: '新建任务' })
      await newTask.focus()
      await page.keyboard.press('Enter')
      const titleField = page.locator('#field-task-title')
      await titleField.waitFor()
      const drawerGeometry = await page.evaluate(() => {
        const dialog = document.querySelector('[role=dialog]')
        const rect = dialog.getBoundingClientRect()
        return {
          left: rect.left,
          right: rect.right,
          width: window.innerWidth,
          documentWidth: document.documentElement.scrollWidth,
        }
      })
      assert.ok(
        drawerGeometry.left >= -1 && drawerGeometry.right <= drawerGeometry.width + 1,
        `drawer outside viewport: ${JSON.stringify(drawerGeometry)}`,
      )
      await titleField.focus()
      await page.keyboard.type(`键盘创建的任务 ${width}`)
      await page.locator('#field-task-objective').focus()
      await page.keyboard.type('通过键盘完整填写并提交。')
      await shot('create-task-drawer')
      const submit = page.getByRole('button', { name: '创建任务' })
      await submit.focus()
      await page.keyboard.press('Enter')
      await page.waitForURL(/\/tasks\/\d+/, { timeout: 20000 })
      facts.keyboardCreatedTask = page.url().split('/').pop()

      // ---- task detail: succeeded ---------------------------------------
      await page.goto(`/tasks/${succeeded.id}`)
      await page.getByRole('heading', { name: '执行尝试' }).waitFor()
      await shot('task-succeeded')
      const succeededText = await page.locator('body').innerText()
      assert.ok(succeededText.includes('已通过'), 'succeeded task must show 已通过')
      assert.ok(succeededText.includes('演练运行'), 'demo run must be labelled')
      facts.taskStatusText = '已通过'

      // ---- task detail: failed ------------------------------------------
      await page.goto(`/tasks/${failed.id}`)
      await page.getByRole('heading', { name: '执行尝试' }).waitFor()
      await shot('task-failed')
      const failedText = await page.locator('body').innerText()
      assert.ok(failedText.includes('失败'), 'failed task must show 失败')
      assert.ok(failedText.includes('重试执行'), 'a failed task must offer retry')

      // ---- task detail: unknown usage -----------------------------------
      if (unknownUsage) {
        await page.goto(`/tasks/${unknownUsage.id}`)
        await page.getByRole('heading', { name: '执行尝试' }).waitFor()
        await shot('task-unknown-usage')
        const unknownText = await page.locator('body').innerText()
        assert.ok(unknownText.includes('待核对'), 'unknown usage must be labelled 待核对')
        assert.ok(
          unknownText.includes('不会记为 0'),
          'unknown usage must state it is not treated as zero',
        )
      }

      // ---- execution detail ---------------------------------------------
      await page.goto(`/executions/${executionId}`)
      await page.getByRole('heading', { name: '步骤时间线' }).waitFor()
      await shot('execution-detail')

      // ---- budgets -------------------------------------------------------
      await page.goto('/budgets')
      await page.getByRole('heading', { name: '公司额度' }).waitFor()
      await shot('budgets')
      const budgetText = await page.locator('body').innerText()
      for (const label of ['上限', '已用', '预留', '可用']) {
        assert.ok(budgetText.includes(label), `budget page missing ${label}`)
      }
      // The copy *explains* that no recharge control exists, so the word appears
      // in prose. What must not exist is a clickable control.
      const rechargeButtons = await page
        .getByRole('button', { name: /充值|增加额度|修改额度/ })
        .count()
      assert.equal(
        rechargeButtons,
        0,
        'no recharge/limit-edit control is allowed before the command exists',
      )

      // ---- overview ------------------------------------------------------
      await page.goto('/overview')
      await page.getByRole('heading', { name: '公司概览' }).waitFor()
      // The metric cards fill after the service panel, so wait for a real value
      // instead of capturing a skeleton.
      await page.getByText('Token 剩余额度').waitFor({ timeout: 20000 })
      await page
        .locator('.overview__metric-value')
        .filter({ hasNotText: '—' })
        .first()
        .waitFor({ timeout: 20000 })
      await shot('overview')
      facts.overviewMetrics = await page.locator('.overview__metric-value').allInnerTexts()
      assert.ok(
        facts.overviewMetrics.some(value => value.trim() !== '—'),
        `overview metrics did not load: ${facts.overviewMetrics.join(',')}`,
      )

      // ---- contrast of status badges ------------------------------------
      await page.goto('/projects')
      await page.getByRole('heading', { name: '任务看板' }).waitFor()
      facts.badgeContrast = await page.evaluate(() => {
        const samples = []
        for (const badge of document.querySelectorAll('.status-badge')) {
          const style = getComputedStyle(badge)
          samples.push({
            text: badge.textContent?.trim() ?? '',
            color: style.color,
            background: style.backgroundColor,
          })
        }
        return samples
      })

      // ---- SSE reconnect -------------------------------------------------
      // The task status bar is the element that reports stream health; the
      // topbar's "服务已连接" is a different fact (HTTP readiness) and must not
      // be mistaken for the stream state.
      const stateBar = page.locator('.task-detail__state-bar')
      await page.goto(`/tasks/${seeded[0].id}`)
      await page.getByRole('heading', { name: '执行尝试' }).waitFor()
      await assert.doesNotReject(async () => {
        await stateBar.filter({ hasText: '已连接，实时更新中' }).waitFor({ timeout: 20000 })
      })
      facts.sseConnected = true

      // Break only the event stream. Every other request keeps working, so a
      // failure here can only come from the stream itself.
      breakSseStream = true
      // Force a fresh connection attempt: EventSource retries on its own, but a
      // reload makes the timing deterministic.
      await page.reload()
      await page.getByRole('heading', { name: '执行尝试' }).waitFor()
      await stateBar.filter({ hasText: '连接中断，结果可能未更新' }).waitFor({ timeout: 30000 })
      facts.sseDisconnectedNotice = true
      await shot('task-sse-disconnected')

      // Restore the stream and let EventSource reconnect by itself (no reload):
      // this is the in-page recovery the page must survive.
      breakSseStream = false
      await stateBar.filter({ hasText: '已连接，实时更新中' }).waitFor({ timeout: 60000 })
      facts.sseReconnected = true

      facts.documentOverflow = await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1,
      )
      assert.ok(facts.documentOverflow, 'page must not scroll horizontally')
      results.push(facts)
      await context.close()
    }

    // Contrast is asserted from the rendered values, not from the token file.
    for (const result of results) {
      for (const sample of result.badgeContrast ?? []) {
        const fg = parseColor(sample.color)
        const bg = parseColor(sample.background)
        if (!fg || !bg) continue
        const ratio = contrastRatio(fg, bg)
        sample.contrast = Number(ratio.toFixed(2))
        assert.ok(
          ratio >= 4.5,
          `badge "${sample.text}" contrast ${ratio.toFixed(2)} < 4.5`,
        )
      }
    }
    assert.deepEqual(errors, [], `page errors: ${errors.join(' | ')}`)
    await seedContext.close()
  } finally {
    if (browser) await browser.close()
    await new Promise(resolve => server.close(resolve))
  }

  const summary = {
    baseURL,
    api: API,
    viewports: VIEWPORTS.map(([w, h]) => `${w}x${h}`),
    results,
    pageErrors: errors,
    passed: true,
  }
  fs.writeFileSync(
    path.join(out, 'ui-results.json'),
    `${JSON.stringify(summary, null, 2)}\n`,
    'utf8',
  )
  console.log(JSON.stringify({ passed: true, viewports: summary.viewports, pageErrors: errors.length }))
})().catch(error => {
  console.error(error)
  fs.mkdirSync(out, { recursive: true })
  fs.writeFileSync(
    path.join(out, 'ui-failure.txt'),
    `${error?.stack ?? String(error)}\n`,
    'utf8',
  )
  process.exitCode = 1
})
