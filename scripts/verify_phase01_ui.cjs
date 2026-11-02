/** Real Chromium QA against the isolated Phase 01 Compose stack only.
 * NODE_PATH must contain the bundled Playwright package directory.
 * Run after verify_phase01.py has created the disposable fixture owner.
 */
const { chromium, expect } = require('playwright/test')
const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')

const baseURL = 'http://127.0.0.1:18081'
const out = path.resolve(__dirname, '../docs/acceptance/evidence/phase-01-fixes')
const password = 'correct-horse-battery-staple' // disposable QA stack only
const results = []
const pageErrors = []
const report = (check, detail) => { results.push({ check, detail }); console.log(`PASS ${check}: ${JSON.stringify(detail)}`) }
function ratio(foreground, background) {
  const luminance = rgb => {
    const parts = rgb.match(/[\d.]+/g).slice(0, 3).map(Number).map(value => {
      const channel = value / 255
      return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
    })
    return parts[0] * 0.2126 + parts[1] * 0.7152 + parts[2] * 0.0722
  }
  const a = luminance(foreground), b = luminance(background)
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
}

async function contrastMeasurements(page) {
  const measurements = []
  const primary = page.locator('[data-measure=btn-medium]')
  for (const state of ['default', 'hover', 'active']) {
    if (state !== 'default') await primary.hover()
    if (state === 'active') await page.mouse.down()
    const expected = { default: 'rgb(29, 78, 216)', hover: 'rgb(30, 64, 175)', active: 'rgb(30, 58, 138)' }
    await expect(primary).toHaveCSS('background-color', expected[state])
    const colors = await primary.evaluate(node => {
      const style = getComputedStyle(node)
      return { color: style.color, background: style.backgroundColor, border: style.borderTopColor }
    })
    if (state === 'active') await page.mouse.up()
    const contrast = ratio(colors.color, colors.background)
    assert(contrast >= 4.5)
    measurements.push({ element: `primary ${state}`, ...colors, contrast })
  }
  const badges = await page.locator('.showcase .status-badge').evaluateAll(nodes => nodes.map(node => {
    const style = getComputedStyle(node)
    return { element: node.textContent.trim(), color: style.color, background: style.backgroundColor }
  }))
  assert(badges.length > 0)
  for (const badge of badges) {
    const contrast = ratio(badge.color, badge.background)
    assert(contrast >= 4.5, JSON.stringify(badge))
    measurements.push({ ...badge, contrast })
  }
  const input = page.locator('.showcase input').first()
  await input.focus()
  const control = await input.evaluate(node => {
    const style = getComputedStyle(node)
    return { border: style.borderTopColor, outline: style.outlineColor, outlineWidth: style.outlineWidth, background: style.backgroundColor }
  })
  const borderContrast = ratio(control.border, control.background)
  const outlineContrast = ratio(control.outline, control.background)
  assert(borderContrast >= 3 && outlineContrast >= 3)
  assert(parseFloat(control.outlineWidth) >= 2)
  measurements.push({ element: 'input border/focus', ...control, borderContrast, outlineContrast })
  fs.writeFileSync(path.join(out, 'contrast-results.json'), JSON.stringify(measurements, null, 2))
  report('actual rendered contrast combinations', measurements)
}

async function screenshot(page, name) {
  await page.screenshot({ path: path.join(out, `${name}.png`), fullPage: true, animations: 'disabled' })
}
async function noOverflow(page) {
  const widths = await page.evaluate(() => ({ viewport: innerWidth, content: document.documentElement.scrollWidth }))
  assert(widths.content <= widths.viewport, JSON.stringify(widths))
  return widths
}
async function login(page) {
  await page.goto('/login')
  await page.getByRole('textbox', { name: '登录名 （必填）', exact: true }).fill('owner')
  await page.locator('input[name=password]').fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/overview$/)
  await expect(page.getByText('API 可服务', { exact: true })).toBeVisible()
}

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  try {
    const context = await browser.newContext({ baseURL, viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' })
    const page = await context.newPage()
    page.on('pageerror', error => pageErrors.push(error.message))
    for (const [width, height] of [[1440, 900], [1024, 768], [390, 844], [360, 800]]) {
      await page.setViewportSize({ width, height })
      await page.goto('/login')
      await expect(page.getByRole('button', { name: '登录', exact: true })).toBeDisabled()
      await screenshot(page, `login-disabled-${width}x${height}`)
      report(`login narrow/layout ${width}`, await noOverflow(page))
    }
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('about:blank')
    await page.goto('/login')
    await expect(page.locator('input[name=username]')).toBeVisible()
    // Native Tab/Enter path, including wrong-password feedback and preserved username.
    await page.keyboard.press('Tab')
    await expect(page.locator('input[name=username]')).toBeFocused()
    await page.keyboard.type('owner')
    await page.keyboard.press('Tab')
    await expect(page.locator('input[name=password]')).toBeFocused()
    await page.keyboard.type('definitely-wrong-password')
    await page.keyboard.press('Enter')
    await expect(page.getByTestId('login-error')).toBeVisible()
    await expect(page.locator('input[name=username]')).toHaveValue('owner')
    await screenshot(page, 'login-error-1440x900')
    await page.locator('input[name=password]').fill(password)
    let releaseLogin
    const loginGate = new Promise(resolve => { releaseLogin = resolve })
    await page.route('**/api/v1/auth/login', async route => { await loginGate; await route.continue() })
    await page.keyboard.press('Enter')
    await expect(page.getByRole('button', { name: '登录中…' })).toBeDisabled()
    await screenshot(page, 'login-submitting-1440x900')
    releaseLogin()
    await expect(page).toHaveURL(/\/overview$/)
    await page.unroute('**/api/v1/auth/login')
    report('keyboard login, error and pending submit', 'Tab username → Tab password → Enter; successful retry')

    for (const [width, height] of [[1440, 900], [1024, 768], [390, 844], [360, 800]]) {
      await page.setViewportSize({ width, height })
      await expect(page.getByText('API 可服务', { exact: true })).toBeVisible()
      await screenshot(page, `overview-empty-${width}x${height}`)
      report(`overview layout ${width}`, await noOverflow(page))
      if (width === 1024) {
        const nav = page.locator('aside').getByRole('button', { name: '设置', exact: true })
        await nav.focus()
        await expect(nav).toBeFocused()
        await expect(page.getByRole('tooltip', { name: '设置', exact: true })).toBeVisible()
        await screenshot(page, 'tablet-navigation-focus-tooltip-1024x768')
        await page.keyboard.press('Enter')
        await expect(page).toHaveURL(/\/settings$/)
        const names = await page.locator('aside button').evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-label')))
        assert.equal(names.length, 9)
        assert(names.every(Boolean))
        report('tablet accessible names, tooltip and keyboard navigation', names)
        await page.locator('aside').getByRole('button', { name: '公司概览' }).click()
      }
    }
    // Mobile drawer keyboard trap and Escape restoration.
    await page.setViewportSize({ width: 390, height: 844 })
    const menu = page.getByRole('button', { name: '打开导航菜单' })
    await menu.click()
    await expect(page.getByRole('dialog', { name: '导航' })).toBeVisible()
    await screenshot(page, 'mobile-navigation-390x844')
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog', { name: '导航' })).not.toBeVisible()
    await expect(menu).toBeFocused()
    const mobileButtons = await page.locator('.app-shell__topbar button').evaluateAll(nodes => nodes.map(node => ({ name: node.textContent || node.getAttribute('aria-label'), width: node.getBoundingClientRect().width, height: node.getBoundingClientRect().height })))
    assert(mobileButtons.every(button => button.width >= 44 && button.height >= 44), JSON.stringify(mobileButtons))
    report('mobile drawer Escape focus and touch targets', mobileButtons)

    // Controlled UI failures are intercepted explicitly; successful retries hit real API.
    await page.route('**/api/v1/auth/logout', route => route.abort('failed'))
    await page.getByRole('button', { name: '注销', exact: true }).click()
    await expect(page.getByText('注销结果未确认', { exact: true })).toBeVisible()
    await screenshot(page, 'topbar-logout-network-error-390x844')
    await page.unroute('**/api/v1/auth/logout')
    await page.getByRole('button', { name: '重试注销' }).click()
    await expect(page).toHaveURL(/\/login$/)
    report('topbar network logout failure', 'visible uncertainty, real server retry succeeds')

    await login(page)
    await menu.click()
    await page.getByRole('navigation', { name: '移动导航' }).getByRole('button', { name: '设置', exact: true }).click()
    await page.getByRole('button', { name: '注销当前会话', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: '注销当前会话' })
    await expect(dialog).toBeVisible()
    for (let step = 0; step < 6; step++) {
      await page.keyboard.press('Tab')
      assert(await dialog.evaluate(node => node.contains(document.activeElement)))
    }
    await page.keyboard.press('Escape')
    await expect(dialog).not.toBeVisible()
    await expect(page.getByRole('button', { name: '注销当前会话', exact: true })).toBeFocused()
    await page.getByRole('button', { name: '注销当前会话', exact: true }).click()
    await screenshot(page, 'settings-logout-dialog-390x844')
    await page.route('**/api/v1/auth/logout', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ code: 'DATABASE_NOT_READY', message: '数据库尚未就绪。' }) }))
    await dialog.getByRole('button', { name: '注销会话', exact: true }).click()
    await expect(dialog.getByText('注销结果未确认', { exact: true })).toBeVisible()
    await screenshot(page, 'settings-logout-database-error-390x844')
    await noOverflow(page)
    const dialogRect = await dialog.locator('.el-dialog').boundingBox()
    assert(dialogRect.x >= 16 && dialogRect.width <= 358 && dialogRect.height <= 844 * 0.85, JSON.stringify(dialogRect))
    report('mobile logout dialog bounds, Tab trap and Escape focus return', dialogRect)
    await page.unroute('**/api/v1/auth/logout')
    await dialog.getByRole('button', { name: '重试注销' }).click()
    await expect(page).toHaveURL(/\/login$/)
    report('settings database logout failure', 'dialog keeps error and supports real retry')

    await page.setViewportSize({ width: 1440, height: 900 })
    await login(page)
    await page.locator('aside').getByRole('button', { name: '设置', exact: true }).click()
    // Simulate another tab logging out via the real server; current SPA cache remains authenticated.
    const csrf = (await context.cookies()).find(cookie => cookie.name === 'acos_csrf').value
    assert.equal((await context.request.post('/api/v1/auth/logout', { headers: { Origin: baseURL, 'X-CSRF-Token': csrf } })).status(), 204)
    await page.getByRole('button', { name: '刷新服务信息' }).click()
    await expect(page.getByRole('button', { name: '进入登录页' })).toBeVisible()
    await screenshot(page, 'session-invalidated-1440x900')
    await page.getByRole('button', { name: '进入登录页' }).click()
    await expect(page).toHaveURL(/\/login$/)
    report('real revoked session → protected 401 → login without reload', 'passed')

    // A stale first-run tab gets a real 409 from an already initialized server.
    const stale = await browser.newContext({ baseURL, viewport: { width: 1440, height: 900 } })
    const setup = await stale.newPage()
    await setup.route('**/api/v1/auth/setup-status', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ setupComplete: false, passwordMinLength: 12, appVersion: '0.1.0' }) }))
    await setup.goto('/setup')
    await setup.locator('input[name=username]').fill('other-owner')
    await setup.locator('input[name=displayName]').fill('质检测试')
    await setup.locator('input[name=password]').fill(password)
    await setup.locator('input[name=passwordConfirm]').fill(password)
    await setup.getByRole('button', { name: '创建所有者账号' }).click()
    await expect(setup).toHaveURL(/\/login\?setup=completed$/)
    await expect(setup.getByText('所有者账号已存在，请直接登录。', { exact: true })).toBeVisible()
    await screenshot(setup, 'setup-conflict-login-1440x900')
    await stale.close()
    report('stale setup cache + actual server 409', 'login redirect and explanatory message')

    await login(page)
    await page.goto('/showcase')
    await expect(page.getByTestId('measurements')).toBeVisible()
    const sizes = await page.locator('[data-measure^=btn-]').evaluateAll(nodes => nodes.map(node => ({ name: node.getAttribute('data-measure'), height: node.getBoundingClientRect().height })))
    for (const [name, height] of [['btn-small', 32], ['btn-medium', 40], ['btn-large', 48]]) assert.equal(sizes.find(size => size.name === name).height, height)
    await screenshot(page, 'showcase-measurements-1440x900')
    report('button dimensions', sizes)
    await contrastMeasurements(page)

    await page.goto('/overview')
    await expect(page.getByText('API 可服务', { exact: true })).toBeVisible()
    let releaseStatus
    const statusGate = new Promise(resolve => { releaseStatus = resolve })
    await page.route('**/api/v1/system/status', async route => { await statusGate; await route.continue() })
    await page.getByRole('button', { name: '刷新服务状态' }).click()
    await expect(page.locator('.overview__skeleton')).toBeVisible()
    await screenshot(page, 'overview-loading-1440x900')
    releaseStatus()
    await expect(page.getByText('API 可服务', { exact: true })).toBeVisible()
    await page.unroute('**/api/v1/system/status')
    await page.route('**/api/v1/system/status', route => route.abort('failed'))
    await page.getByRole('button', { name: '刷新服务状态' }).click()
    await expect(page.getByText('服务状态读取失败', { exact: true })).toBeVisible()
    await screenshot(page, 'overview-error-1440x900')
    await page.unroute('**/api/v1/system/status')
    await page.getByRole('button', { name: '重新加载' }).click()
    await expect(page.getByText('API 可服务', { exact: true })).toBeVisible()
    report('loading, error and recovery', 'passed')

    // Equivalent reflow only: this does not verify actual browser zoom.
    await page.setViewportSize({ width: 720, height: 450 })
    await screenshot(page, 'overview-reflow-720x450')
    report('200% equivalent reflow (1440/2 × 900/2)', await noOverflow(page))
    assert.deepEqual(pageErrors, [])
    report('uncaught browser errors', pageErrors)
    fs.writeFileSync(path.join(out, 'ui-results.json'), JSON.stringify({ browser: browser.version(), baseURL, results, pageErrors }, null, 2))
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
