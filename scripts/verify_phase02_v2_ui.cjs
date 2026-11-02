/** Built SPA + explicitly simulated API, for V2-5 refresh-failure recovery.
 * No real auth/database/provider validation; backend tests cover persistence.
 * Screenshots are isolated from all previous acceptance evidence.
 */
const { chromium, expect } = require('playwright/test')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')

const root = path.resolve(__dirname, '..')
const dist = path.join(root, 'frontend/dist')
const out = path.join(root, 'docs/acceptance/evidence/phase-02-v2-fixes')
const baseURL = 'http://127.0.0.1:18084'
const timestamp = '2026-11-02T00:00:00Z'
const company = { id: 1, name: '模拟 API · V2 修复验证', goal: '验证失败恢复', status: 'ACTIVE', version: 1,
  templateKey: 'tech-company', templateVersion: 1, initializedAt: timestamp, positionCount: 1, agentCount: 1 }
const position = { id: 1, code: 'CEO', title: 'CEO', duty: '测试职责', version: 1, rowVersion: 1,
  sortOrder: 1, departmentName: '模拟组织' }
const model = { id: 1, providerId: 1, providerName: '模拟供应商', providerStatus: 'ENABLED',
  providerSecretConfigured: true, version: 1, logicalName: 'fixture', label: '模拟模型', status: 'ENABLED',
  maxOutputTokens: null, supportsJsonOutput: null, probeEvidenceStale: false, resolvedName: null,
  lastProbeOutcome: null, lastProbeAt: null, lastProbeDetail: null, createdAt: timestamp, updatedAt: timestamp }
const centre = { providers: [], models: [model], assignments: [], recentProbes: [],
  usage: { probeCalls: 0, measuredCalls: 0, partiallyMeasuredCalls: 0, unmeasuredCalls: 0,
    promptTokens: null, completionTokens: null, promptTokensUnreportedCalls: 0, completionTokensUnreportedCalls: 0, pendingProbes: 0 },
  deepseekPreset: { kind: 'DEEPSEEK', baseUrl: 'https://api.example.test', secretRef: 'TEST_KEY', suggestedModel: 'fixture', notes: '模拟 API' } }
let current
let versions
let failRefresh
function reset() {
  current = { id: 1, employeeCode: 'CEO-001', displayName: '模拟员工', status: 'ACTIVE', positionId: 1,
    positionCode: 'CEO', positionTitle: 'CEO', managerId: null, managerName: null,
    dutyOverride: null, effectiveDuty: '测试职责', model: null, version: 1, statusChangedAt: timestamp, archivedAt: null }
  versions = []
  failRefresh = true
}
const server = http.createServer(async (req, res) => {
  const pathname = new URL(req.url, baseURL).pathname
  const json = (status, body) => {
    res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-QA-Fixture': 'simulated-api' })
    res.end(JSON.stringify(body))
  }
  if (pathname.startsWith('/api/')) {
    if (pathname.endsWith('/setup-status')) return json(200, { setupComplete: true, companyInitialized: true, companyName: company.name, passwordMinLength: 12 })
    if (pathname.endsWith('/session')) return json(200, { user: { id: 1, username: 'fixture', displayName: '模拟 API', role: 'OWNER' }, expiresAt: '2099-01-01T00:00:00Z', csrfToken: 'fixture-csrf' })
    if (pathname.endsWith('/health/ready')) return json(200, { ready: true, checks: [] })
    if (pathname.endsWith('/organization')) return json(200, { company, departmentName: '模拟组织', departmentPurpose: '故障注入', positions: [position], agents: [current] })
    if (pathname.endsWith('/models/centre')) {
      if (failRefresh && versions.length) return json(503, { code: 'UNAVAILABLE', message: '模拟：模型中心暂时读取失败。' })
      return json(200, centre)
    }
    if (pathname.endsWith('/history')) return json(200, { agentId: 1, entries: [] })
    if (pathname.endsWith('/model-assignment')) return json(409, { code: 'MODEL_BINDING_UNAVAILABLE', message: '模拟：模型不可用。', fieldErrors: [] })
    if (pathname.endsWith('/agents/1') && req.method === 'PATCH') {
      const chunks = []
      for await (const chunk of req) chunks.push(chunk)
      const payload = JSON.parse(Buffer.concat(chunks).toString())
      versions.push(payload.expectedVersion)
      if (payload.expectedVersion !== current.version) return json(409, { code: 'CONCURRENT_UPDATE', message: '版本冲突，请刷新。', fieldErrors: [] })
      current = { ...current, displayName: payload.displayName, version: current.version + 1 }
      return json(200, current)
    }
    return json(404, { code: 'FIXTURE_NOT_IMPLEMENTED', message: '模拟 API 未提供该端点。' })
  }
  const requested = path.resolve(dist, `.${pathname}`)
  const file = requested.startsWith(`${dist}${path.sep}`) && fs.existsSync(requested) && fs.statSync(requested).isFile()
    ? requested : path.join(dist, 'index.html')
  const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[path.extname(file)] ?? 'application/octet-stream'
  res.writeHead(200, { 'Content-Type': mime, 'Cache-Control': 'no-store' })
  fs.createReadStream(file).pipe(res)
})

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(18084, '127.0.0.1', resolve) })
  let browser
  const results = []
  const errors = []
  try {
    browser = await chromium.launch({ channel: 'chrome', headless: true })
    for (const [width, height] of [[1440, 900], [1024, 768], [390, 844], [360, 800]]) {
      reset()
      const context = await browser.newContext({ baseURL, viewport: { width, height }, reducedMotion: 'reduce' })
      await context.addCookies([{ name: 'acos_csrf', value: 'fixture-csrf', url: baseURL }])
      const page = await context.newPage()
      page.on('pageerror', error => errors.push(error.message))
      await page.goto('/organization')
      const employee = page.locator('.org__tree-button').first()
      await expect(employee).toBeVisible()
      await page.screenshot({ path: path.join(out, `organization-${width}x${height}.png`), fullPage: true })
      await employee.focus()
      await page.keyboard.press('Enter')
      await page.locator('#field-agent-display-name').fill('已保存的模拟姓名')
      await page.locator('#field-agent-model').selectOption('1')
      const save = page.getByRole('button', { name: '保存员工资料', exact: true })
      await save.focus()
      await page.keyboard.press('Enter')
      const notice = page.locator('[data-testid=agent-partial-save]')
      await expect(notice).toContainText('资料已保存（版本 2）')
      await expect(page.locator('#field-agent-display-name')).toHaveValue('已保存的模拟姓名')
      await expect(page.locator('#field-agent-model')).toHaveValue('1')
      await notice.scrollIntoViewIfNeeded()
      // Element Plus animates the drawer; wait for its final geometry before
      // checking containment (visibility alone does not finish the transition).
      await expect.poll(() => page.evaluate(() => {
        const rect = document.querySelector('[role=dialog]').getBoundingClientRect()
        return rect.left >= 0 && rect.right <= innerWidth + 1
      })).toBe(true)
      const geometry = await page.evaluate(() => {
        const rect = document.querySelector('[role=dialog]').getBoundingClientRect()
        return { width: innerWidth, contentWidth: document.documentElement.scrollWidth, left: rect.left, right: rect.right }
      })
      await page.screenshot({ path: path.join(out, `partial-save-${width}x${height}.png`), fullPage: true })
      assert(geometry.contentWidth <= width && geometry.left >= 0 && geometry.right <= width + 1, JSON.stringify(geometry))
      await save.click()
      await expect(notice).toContainText('资料已保存（版本 3）')
      assert.deepEqual(versions, [1, 2])
      assert.equal(current.displayName, '已保存的模拟姓名')
      failRefresh = false
      await page.keyboard.press('Escape')
      await page.reload()
      await expect(page.locator('.org__tree-button').first()).toContainText('已保存的模拟姓名')
      results.push({ viewport: `${width}x${height}`, versions: [...versions], persistedName: current.displayName, geometry })
      await context.close()
    }
    assert.deepEqual(errors, [])
    const report = { fixture: 'simulated API; frontend browser test only', browser: browser.version(), results, errors }
    fs.writeFileSync(path.join(out, 'ui-results.json'), JSON.stringify(report, null, 2))
    console.log(JSON.stringify(report, null, 2))
  } finally {
    if (browser) await browser.close()
    await new Promise(resolve => server.close(resolve))
  }
})().catch(error => { console.error(error); process.exitCode = 1 })
