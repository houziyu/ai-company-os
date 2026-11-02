/** Local frontend-only QA: built SPA + explicit in-memory API fixture.
 * No database, .env, containers or real credentials are accessed.
 * NODE_PATH: existing Playwright dependency directory.
 * --zoom-200 opens a dedicated window and waits for actual browser zoom;
 * it never substitutes viewport shrinking, CSS zoom or device emulation.
 */
const { chromium, expect } = require('playwright/test')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')

const root = path.resolve(__dirname, '..')
const dist = path.join(root, 'frontend/dist')
const out = path.join(root, 'docs/acceptance/evidence/phase-01-r7-fixes')
const baseURL = 'http://127.0.0.1:18082'
const zoomMode = process.argv.includes('--zoom-200')
let authenticated = false
const owner = { id: 1, username: 'fixture-owner', displayName: '浏览器质检夹具（模拟 API）', role: 'OWNER' }
const session = { user: owner, expiresAt: '2026-10-31T00:00:00Z', csrfToken: 'test-fixture-only' }
const checks = [
  { name: 'database', status: 'ok', detail: '模拟 API，仅用于前端浏览器验证' },
  { name: 'migrations', status: 'ok', detail: '模拟 API' },
  { name: 'worker', status: 'skip', detail: '模拟 API，无 Worker 进程' },
]
const service = {
  appVersion: '0.1.0', environment: 'test-fixture', databaseStatus: 'ok',
  databaseRevision: 'fixture', expectedRevision: 'fixture', workerStatus: 'missing',
  workerLastHeartbeatAt: null, workerVersion: null, sessionTtlHours: 12,
  startedAt: '2026-10-30T00:00:00Z',
}
const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, baseURL).pathname
  function json(status, body) {
    res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-QA-Fixture': 'in-memory-only' })
    res.end(body === undefined ? undefined : JSON.stringify(body))
  }
  if (pathname.startsWith('/api/')) {
    if (pathname.endsWith('/setup-status')) return json(200, { setupComplete: true, passwordMinLength: 12 })
    if (pathname.endsWith('/login')) { authenticated = true; return json(200, session) }
    if (pathname.endsWith('/logout')) { authenticated = false; return json(204) }
    if (pathname.endsWith('/health/ready')) return json(200, { ready: true, checks })
    if (!authenticated) return json(401, { code: 'NOT_AUTHENTICATED', message: '测试会话未登录。' })
    if (pathname.endsWith('/session')) return json(200, session)
    if (pathname.endsWith('/system/status')) return json(200, service)
    return json(404, { code: 'NOT_FOUND', message: '测试夹具未实现此接口' })
  }
  const requested = path.resolve(dist, `.${pathname}`)
  const file = requested.startsWith(`${dist}${path.sep}`) && fs.existsSync(requested) && fs.statSync(requested).isFile()
    ? requested : path.join(dist, 'index.html')
  const type = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[path.extname(file)] ?? 'application/octet-stream'
  res.writeHead(200, { 'Content-Type': type, 'Cache-Control': 'no-store' })
  fs.createReadStream(file).pipe(res)
})

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(18082, '127.0.0.1', resolve) })
  let browser
  try {
    browser = await chromium.launch({ channel: 'chrome', headless: !zoomMode, args: ['--window-size=1440,900'] })
    const context = await browser.newContext({ baseURL, viewport: zoomMode ? null : { width: 1440, height: 900 }, reducedMotion: 'reduce' })
    const page = await context.newPage()
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto('/login')
    await expect(page.locator('input[name=username]')).toBeVisible()
    const baseline = await page.evaluate(() => ({ width: innerWidth, height: innerHeight, dpr: devicePixelRatio, outerWidth }))
    const results = []
    if (zoomMode) {
      console.log('ZOOM_WINDOW_READY', JSON.stringify({ baseURL, baseline }))
      await page.waitForFunction(dpr => devicePixelRatio === dpr * 2, baseline.dpr, { timeout: 60000 })
      const zoomed = await page.evaluate(() => ({ width: innerWidth, height: innerHeight, dpr: devicePixelRatio, outerWidth }))
      assert.equal(zoomed.outerWidth, baseline.outerWidth)
      assert(Math.abs(zoomed.width * 2 - baseline.width) <= 2)
      results.push({ check: 'actual browser zoom 200%', baseline, zoomed })
    }
    const sizes = zoomMode ? [null] : [[1440, 900], [1024, 768], [390, 844], [360, 800]]
    for (const size of sizes) {
      if (size) await page.setViewportSize({ width: size[0], height: size[1] })
      const suffix = size ? `${size[0]}x${size[1]}` : 'browser-zoom-200'
      await page.screenshot({ path: path.join(out, `login-${suffix}.png`), fullPage: true })
      await page.locator('input[name=username]').focus()
      await page.keyboard.type('fixture-owner')
      await page.keyboard.press('Tab')
      await expect(page.locator('input[name=password]')).toBeFocused()
      await page.keyboard.type('test-fixture-password')
      await page.keyboard.press('Enter')
      await expect(page).toHaveURL(/\/overview$/)
      await expect(page.getByText('API 可服务', { exact: true })).toBeVisible()
      await page.screenshot({ path: path.join(out, `overview-${suffix}.png`), fullPage: true })
      const layout = await page.evaluate(() => ({ width: innerWidth, contentWidth: document.documentElement.scrollWidth, dpr: devicePixelRatio }))
      assert(layout.contentWidth <= layout.width)
      results.push({ check: `login keyboard and overview ${suffix}`, layout })
      // Exercise the settings route and a keyboard-accessible confirmation.
      await page.goto('/settings')
      await expect(page.getByRole('button', { name: '注销当前会话', exact: true })).toBeVisible()
      await page.getByRole('button', { name: '注销当前会话', exact: true }).click()
      const dialog = page.getByRole('dialog', { name: '注销当前会话' })
      await expect(dialog).toBeVisible()
      await page.screenshot({ path: path.join(out, `settings-dialog-${suffix}.png`), fullPage: true })
      await page.keyboard.press('Escape')
      await expect(dialog).not.toBeVisible()
      await expect(page.getByRole('button', { name: '注销当前会话', exact: true })).toBeFocused()
      await page.getByRole('button', { name: '注销', exact: true }).click()
      await expect(page).toHaveURL(/\/login$/)
      results.push({ check: `settings Escape focus and logout ${suffix}`, passed: true })
    }
    assert.deepEqual(errors, [])
    const result = { fixture: 'in-memory API, no database or server-auth verification', browser: browser.version(), results, errors }
    fs.writeFileSync(path.join(out, zoomMode ? 'zoom-200-results.json' : 'ui-results.json'), JSON.stringify(result, null, 2))
    console.log(JSON.stringify(result, null, 2))
  } finally {
    if (browser) await browser.close()
    await new Promise(resolve => server.close(resolve))
  }
})().catch(error => { console.error(error); process.exitCode = 1 })
