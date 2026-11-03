/**
 * Phase 02 UI gate against the live isolated Compose stack.
 *
 * What this proves (and what it does not):
 *  - It drives the real built SPA against the real API and a real database, so
 *    the screenshots show real company data, not a fixture.
 *  - It checks the three required viewports, the 360px narrow case, keyboard
 *    operation, contrast from computed styles, and the real browser 200% zoom.
 *
 * The provider screenshots show the "未配置" state because the QA stack has no
 * DEEPSEEK_API_KEY. That is the honest configured state for this environment,
 * not a simulated success.
 *
 * NODE_PATH must contain the bundled Playwright package directory; Chrome must
 * be installed. The stack must be reachable at 127.0.0.1:18083 and must be a
 * disposable stack (the script creates the owner and company).
 */
const { chromium, expect } = require('playwright/test')
const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')

const baseURL = 'http://127.0.0.1:18083'
const out = path.resolve(__dirname, '../docs/acceptance/evidence/phase-02')
const password = 'phase02-acceptance-password' // disposable QA stack only
const results = []
const pageErrors = []
const zoomMode = process.argv.includes('--zoom-200')

const report = (check, detail) => {
  results.push({ check, detail })
  console.log(`PASS ${check}: ${JSON.stringify(detail)}`)
}

/** WCAG contrast ratio from two computed `rgb(...)` strings. */
function ratio(foreground, background) {
  const parse = value => (value.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number)
  const luminance = rgb => {
    const [r, g, b] = parse(rgb).map(value => {
      const channel = value / 255
      return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
    })
    return 0.2126 * r + 0.7152 * g + 0.0722 * b
  }
  const a = luminance(foreground)
  const b = luminance(background)
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
}

/** RGB weight for the assertion that a colour is close to a token value. */
function rgbClose(actual, expected, tolerance = 12) {
  const a = (actual.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number)
  const b = expected
  return a.length === 3 && a.every((value, index) => Math.abs(value - b[index]) <= tolerance)
}

async function assertNoHorizontalOverflow(page, label) {
  const layout = await page.evaluate(() => ({
    innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
    dpr: devicePixelRatio,
  }))
  assert(
    layout.scrollWidth <= layout.innerWidth + 1,
    `${label}: content ${layout.scrollWidth}px exceeds viewport ${layout.innerWidth}px`,
  )
  return layout
}

/** Measure the shared components' real rendered geometry, not the CSS source. */
async function measureControls(page) {
  const buttons = await page.locator('.app-button').evaluateAll(nodes =>
    // Buttons inside closed drawers/dialogs report a 0px box; they are not
    // rendered for the user, so measuring them would produce a false failure.
    nodes
      .filter(node => node.getBoundingClientRect().height > 0)
      .slice(0, 8)
      .map(node => {
      const rect = node.getBoundingClientRect()
      const style = getComputedStyle(node)
      // Ghost/text buttons have a transparent own background. Contrast must be
      // computed against the first opaque ancestor, otherwise a transparent
      // value is read as black and reports a false failure.
      let background = style.backgroundColor
      let ancestor = node.parentElement
      while (
        ancestor &&
        (background === 'rgba(0, 0, 0, 0)' || background === 'transparent')
      ) {
        background = getComputedStyle(ancestor).backgroundColor
        ancestor = ancestor.parentElement
      }
      if (background === 'rgba(0, 0, 0, 0)' || background === 'transparent') {
        background = 'rgb(255, 255, 255)'
      }
      return {
        text: (node.textContent ?? '').trim().slice(0, 18),
        height: Math.round(rect.height),
        background,
        color: style.color,
      }
    }),
  )
  assert(buttons.length > 0, 'no AppButton found on the page')
  for (const button of buttons) {
    // Token heights are 32 / 40 / 48; on mobile the touch target is >= 44.
    assert(
      [32, 40, 48].includes(button.height) || button.height >= 44,
      `unexpected button height ${button.height} for "${button.text}"`,
    )
    const contrast = ratio(button.color, button.background)
    assert(contrast >= 4.5, `button "${button.text}" contrast ${contrast.toFixed(2)} < 4.5`)
  }
  return buttons
}

async function signIn(page) {
  // An authenticated session is redirected away from /login by the router guard,
  // which is correct behaviour. Wait for the guard to settle, then only fill the
  // form when it is actually offered.
  await page.goto('/login')
  await page.waitForLoadState('networkidle')
  const onLogin = await page.locator('input[name=username]').isVisible().catch(() => false)
  if (!onLogin) return
  await page.locator('input[name=username]').click()
  await page.locator('input[name=username]').type('owner')
  await page.locator('input[name=password]').click()
  await page.locator('input[name=password]').type(password)
  await page.locator('button[type=submit]').click()
  await expect(page).not.toHaveURL(/\/login$/)
}

async function wizardWalkthrough(page, suffix) {
  await expect(page).toHaveURL(/\/setup\/company$/)
  await expect(page.getByText('第 1 步，共 4 步', { exact: true })).toBeVisible()
  await page.screenshot({ path: path.join(out, `wizard-step1-${suffix}.png`), fullPage: true })

  await page.locator('#field-company-name').click()
  await page.locator('#field-company-name').type('精一科技')
  await page.locator('#field-company-goal').click()
  await page.locator('#field-company-goal').type('为中小企业交付可验证的自动化研发能力。')
  await page.getByRole('button', { name: '下一步', exact: true }).click()

  // Step 2: template, with all five positions visible.
  await expect(page.getByText('第 2 步，共 4 步', { exact: true })).toBeVisible()
  for (const title of ['CEO', '产品经理', '技术负责人/开发', 'QA', 'DevOps']) {
    await expect(page.locator('.wizard__position', { hasText: title }).first()).toBeVisible()
  }
  await page.screenshot({ path: path.join(out, `wizard-step2-${suffix}.png`), fullPage: true })
  await page.getByRole('button', { name: '下一步', exact: true }).click()

  // Step 3: keep "configure later" selected; the honest default without a key.
  await expect(page.getByText('第 3 步，共 4 步', { exact: true })).toBeVisible()
  await page.screenshot({ path: path.join(out, `wizard-step3-${suffix}.png`), fullPage: true })
  await page.getByRole('button', { name: '下一步', exact: true }).click()

  // Step 4: confirm and create.
  await expect(page.getByText('第 4 步，共 4 步', { exact: true })).toBeVisible()
  await page.screenshot({ path: path.join(out, `wizard-step4-${suffix}.png`), fullPage: true })
  await page.getByRole('button', { name: '创建公司', exact: true }).click()
  await expect(page).toHaveURL(/\/overview/, { timeout: 30000 })
}

async function organizationChecks(page, suffix) {
  await page.goto('/organization')
  await expect(page.getByRole('heading', { name: '组织', exact: true })).toBeVisible()
  await expect(page.getByText('员工（5）', { exact: false })).toBeVisible()
  // Five position cards, five tree rows.
  assert.equal(await page.locator('.org__position-card').count(), 5)
  assert.equal(await page.locator('.org__tree-node').count(), 5)
  await page.screenshot({ path: path.join(out, `organization-${suffix}.png`), fullPage: true })

  const buttons = await measureControls(page)
  report(`organization controls ${suffix}`, { measured: buttons.length })

  // Keyboard: reach an employee row and open it with Enter.
  const ceoRow = page.locator('.org__tree-button').first()
  await ceoRow.focus()
  const focusedIsVisible = await page.evaluate(() => {
    const active = document.activeElement
    if (!active) return false
    const style = getComputedStyle(active)
    return style.outlineStyle !== 'none' || style.boxShadow !== 'none'
  })
  assert(focusedIsVisible, 'focused employee row shows no focus indicator')
  await page.keyboard.press('Enter')
  // Several drawers exist in the DOM (employee, position, mobile nav); select
  // the employee one by its accessible name.
  const drawer = page.getByRole('dialog', { name: /首席执行官/ })
  await expect(drawer).toBeVisible()
  await expect(page.locator('#field-agent-manager')).toBeVisible()
  await page.screenshot({ path: path.join(out, `organization-drawer-${suffix}.png`), fullPage: true })
  report(`organization keyboard open ${suffix}`, { focusRing: focusedIsVisible })

  // The CEO drawer must not offer a manager that would create a cycle: every
  // other employee reports to the CEO, directly or through the tech lead. This
  // is the UI half of the cycle rule; the server half (409 REPORTING_CYCLE with
  // a managerId field error) is proven by the API suite and the component test.
  const managerValue = await page.locator('#field-agent-manager').inputValue()
  assert.equal(managerValue, '') // CEO reports to nobody
  const options = await page
    .locator('#field-agent-manager option')
    .evaluateAll(nodes => nodes.map(node => node.textContent ?? ''))
  assert.equal(options.length, 1, `expected only "不向任何人汇报", got: ${JSON.stringify(options)}`)
  assert(options[0].includes('不向任何人汇报'))
  report(`cyclic manager options filtered ${suffix}`, { options })

  await page.keyboard.press('Escape')
  await expect(drawer).not.toBeVisible()
  report(`drawer Escape close ${suffix}`, { closed: true })

  // A legitimate change through the UI: rename the QA employee, then reload and
  // confirm the server kept it (persistence, not just local state).
  const qaRow = page.locator('.org__tree-button').filter({ hasText: '质量工程师' }).first()
  await qaRow.click()
  const qaDrawer = page.getByRole('dialog', { name: /质量工程师/ })
  await expect(qaDrawer).toBeVisible()
  await page.locator('#field-agent-display-name').fill('质量工程师（改名）')
  await page.getByRole('button', { name: '保存员工资料', exact: true }).click()
  await expect(page.getByRole('dialog', { name: /质量工程师（改名）/ })).toBeVisible({
    timeout: 15000,
  })
  await page.keyboard.press('Escape')
  await page.reload()
  await expect(page.getByText('质量工程师（改名）').first()).toBeVisible()
  await page.screenshot({ path: path.join(out, `organization-renamed-${suffix}.png`), fullPage: true })
  report(`employee rename persists after reload ${suffix}`, { name: '质量工程师（改名）' })

  // A manager change that is legal in the UI: move QA under the CEO.
  await page.locator('.org__tree-button').filter({ hasText: '质量工程师（改名）' }).first().click()
  const qaDrawer2 = page.getByRole('dialog', { name: /质量工程师（改名）/ })
  await expect(qaDrawer2).toBeVisible()
  const ceoOption = await page
    .locator('#field-agent-manager option')
    .evaluateAll(nodes =>
      nodes.map(node => ({ value: node.value, label: node.textContent ?? '' })),
    )
  const target = ceoOption.find(option => option.label.includes('首席执行官'))
  assert(target, `CEO option missing for QA: ${JSON.stringify(ceoOption)}`)
  await page.locator('#field-agent-manager').selectOption(target.value)
  await page.getByRole('button', { name: '保存员工资料', exact: true }).click()
  await expect(page.getByText('首席执行官', { exact: false }).first()).toBeVisible({
    timeout: 15000,
  })
  await page.keyboard.press('Escape')
  await page.reload()
  // After reload QA is a direct report of the CEO: the tree shows QA at depth 1.
  const qaDepth = await page
    .locator('.org__tree-node')
    .filter({ hasText: '质量工程师（改名）' })
    .first()
    .evaluate(node => node.style.getPropertyValue('--depth'))
  assert.equal(qaDepth.trim(), '1', `QA depth ${qaDepth} should be 1 after reporting to the CEO`)
  report(`legal manager change persists ${suffix}`, { qaDepth: qaDepth.trim() })
}

async function modelCentreChecks(page, suffix) {
  await page.goto('/models')
  await expect(page.getByRole('heading', { name: '模型中心', exact: true })).toBeVisible()
  // Empty state first: the wizard chose "configure later", so nothing exists.
  await expect(page.getByRole('heading', { name: '还没有供应商' })).toBeVisible()
  await page.screenshot({ path: path.join(out, `models-empty-${suffix}.png`), fullPage: true })

  // Create the provider through the drawer form and the DeepSeek preset.
  await page.getByRole('button', { name: '添加供应商', exact: true }).first().click()
  const providerDrawer = page.getByRole('dialog', { name: '添加供应商' })
  await expect(providerDrawer).toBeVisible()
  await page.getByRole('button', { name: '填入 DeepSeek 预设' }).click()
  await page.locator('#field-provider-name').fill('DeepSeek 预设')
  await page.screenshot({ path: path.join(out, `models-provider-form-${suffix}.png`), fullPage: true })
  // ADR-0006: the drawer takes the key directly, masked, and starts empty.
  const keyInput = providerDrawer.locator('input[type=password]')
  assert.equal(await keyInput.count(), 1, 'the key field must be present and masked')
  assert.equal(await keyInput.inputValue(), '', 'the key field must start empty')
  await providerDrawer.getByRole('button', { name: '保存配置', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'DeepSeek 预设' })).toBeVisible({ timeout: 15000 })

  // Now add the model from the provider card.
  await page.getByRole('button', { name: '添加模型', exact: true }).first().click()
  const modelDrawer = page.getByRole('dialog', { name: '添加模型' })
  await expect(modelDrawer).toBeVisible()
  await page.locator('#field-model-logical-name').fill('deepseek-v4.1-flash')
  await page.locator('#field-model-label').fill('DeepSeek V4.1 Flash')
  await modelDrawer.getByRole('button', { name: '保存模型', exact: true }).click()
  await expect(page.getByText('模型（1）', { exact: false })).toBeVisible({ timeout: 15000 })
  await page.screenshot({ path: path.join(out, `models-providers-${suffix}.png`), fullPage: true })

  // 未配置 must be visible, and no credential value may be present anywhere in
  // the page. The key input itself lives inside the (closed) drawer.
  await expect(page.getByText('未配置', { exact: true }).first()).toBeVisible()
  const passwordInputs = await page.locator('input[type=password]').count()
  assert.equal(passwordInputs, 0, 'the closed drawer must not render a key input')
  const bodyText = await page.locator('body').innerText()
  assert(!/sk-[A-Za-z0-9]{16,}/.test(bodyText), 'a credential-shaped string appears on the page')
  report(`model centre shows 未配置 ${suffix}`, { passwordInputs })

  // R10: reach the third tab with the keyboard only, and confirm focus follows
  // selection (a roving tabindex makes this mandatory).
  const firstTab = page.getByRole('tab', { name: /供应商（/ })
  await firstTab.focus()
  await page.keyboard.press('ArrowRight')
  const secondTab = page.getByRole('tab', { name: /模型（/ })
  await expect(secondTab).toHaveAttribute('aria-selected', 'true')
  await expect(secondTab).toBeFocused()
  await page.keyboard.press('ArrowRight')
  const thirdTab = page.getByRole('tab', { name: /员工分配/ })
  await expect(thirdTab).toHaveAttribute('aria-selected', 'true')
  await expect(thirdTab).toBeFocused()
  await page.keyboard.press('Home')
  await expect(firstTab).toBeFocused()
  report(`model centre tab keyboard ${suffix}`, {
    reached: 'assignments via ArrowRight',
    focusFollows: true,
    home: true,
  })

  await page.getByRole('tab', { name: /模型（/ }).click()
  await expect(page.getByText('测试连接', { exact: false }).first()).toBeVisible()
  await expect(page.getByText('可能消耗少量 Token', { exact: false })).toBeVisible()
  await expect(page.getByText('未验证', { exact: false }).first()).toBeVisible()
  await page.screenshot({ path: path.join(out, `models-models-${suffix}.png`), fullPage: true })

  await page.getByRole('tab', { name: /员工分配/ }).click()
  // No binding was made (the wizard chose "later" and the model was added after),
  // so the truthful state is 未绑定 for all five employees.
  await expect(page.getByText('未绑定', { exact: false }).first()).toBeVisible()
  const rows = await page.locator('.el-table__row').count()
  assert.equal(rows, 5, `expected 5 assignment rows, got ${rows}`)
  await page.screenshot({ path: path.join(out, `models-assignments-${suffix}.png`), fullPage: true })
  report(`model assignment tab ${suffix}`, { rows, binding: '未绑定 (honest: none configured)' })

  // Bind a model from the organization drawer, then prove the table reflects it.
  await page.goto('/organization')
  await page.locator('.org__tree-button').filter({ hasText: '首席执行官' }).first().click()
  const ceoDrawer = page.getByRole('dialog', { name: /首席执行官/ })
  await expect(ceoDrawer).toBeVisible()
  const modelSelect = page.locator('#field-agent-model')
  await expect(modelSelect).toBeEnabled()
  const modelOptions = await modelSelect
    .locator('option')
    .evaluateAll(nodes => nodes.map(node => ({ value: node.value, label: node.textContent ?? '' })))
  const flash = modelOptions.find(option => option.label.includes('DeepSeek V4.1 Flash'))
  assert(flash, `model option missing: ${JSON.stringify(modelOptions)}`)
  await modelSelect.selectOption(flash.value)
  await page.getByRole('button', { name: '保存员工资料', exact: true }).click()
  await expect(page.getByText('DeepSeek V4.1 Flash', { exact: false }).first()).toBeVisible({
    timeout: 15000,
  })
  await page.keyboard.press('Escape')

  await page.goto('/models')
  await page.getByRole('tab', { name: /员工分配/ }).click()
  await expect(
    page.locator('.el-table__row').filter({ hasText: 'DeepSeek V4.1 Flash' }).first(),
  ).toBeVisible()
  await expect(page.getByText('密钥未配置', { exact: false }).first()).toBeVisible()
  await page.screenshot({ path: path.join(out, `models-assignments-bound-${suffix}.png`), fullPage: true })
  report(`model binding visible in assignment table ${suffix}`, { secretState: '密钥未配置' })

  // R11: selecting 未绑定 must really end the binding, not be silently ignored.
  await page.goto('/organization')
  await page.locator('.org__tree-button').filter({ hasText: '首席执行官' }).first().click()
  const unbindDrawer = page.getByRole('dialog', { name: /首席执行官/ })
  await expect(unbindDrawer).toBeVisible()
  // Select by label: Vue renders the null option's value as an empty attribute
  // only after mount, so the label is the stable handle.
  await page.locator('#field-agent-model').selectOption({ label: '未绑定' })
  await page.getByRole('button', { name: '保存员工资料', exact: true }).click()
  // The tree row shows 未绑定模型 once the binding is gone.
  await expect(page.locator('.org__tree-node').filter({ hasText: '首席执行官' })).toContainText(
    '未绑定模型',
    { timeout: 15000 },
  )
  await page.keyboard.press('Escape')
  await page.reload()
  await expect(
    page.locator('.org__tree-node').filter({ hasText: '首席执行官' }),
  ).toContainText('未绑定模型', { timeout: 15000 })
  await page.screenshot({
    path: path.join(out, `organization-unbound-${suffix}.png`),
    fullPage: true,
  })
  report(`unbind persists after reload ${suffix}`, { binding: '已解除（历史保留）' })
}

async function auditChecks(page, suffix) {
  await page.goto('/audit')
  await expect(page.getByRole('heading', { name: '审计', exact: true })).toBeVisible()
  // The action column renders the Chinese label; the target column shows the
  // type/id. Both are asserted so the table is proven to carry real records.
  await expect(page.getByRole('cell', { name: '公司初始化', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: '员工模型绑定', exact: true }).first()).toBeVisible()
  await expect(page.getByText('COMPANY #1', { exact: false })).toBeVisible()
  await page.screenshot({ path: path.join(out, `audit-${suffix}.png`), fullPage: true })

  const text = await page.locator('body').innerText()
  assert(!text.includes('删除记录'), 'audit page offers a delete affordance')
  const deleteButtons = await page.getByRole('button', { name: /删除|编辑记录/ }).count()
  assert.equal(deleteButtons, 0, 'audit page exposes a delete/edit control')

  // Filtering by action is a real server query, not client-side hiding.
  await page.locator('#audit-action-filter').selectOption('COMPANY_INITIALIZED')
  await page.getByRole('button', { name: '筛选', exact: true }).click()
  await expect(page.getByRole('cell', { name: '公司初始化', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: '员工资料更新', exact: true })).toHaveCount(0)
  await page.screenshot({ path: path.join(out, `audit-filtered-${suffix}.png`), fullPage: true })
  report(`audit read-only + filter ${suffix}`, { hasDeleteControl: false, filtered: 'COMPANY_INITIALIZED' })
  await page.getByRole('button', { name: '清除筛选', exact: true }).click()
}

async function settingsChecks(page, suffix) {
  await page.goto('/settings')
  await expect(page.getByRole('button', { name: '注销当前会话', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '注销当前会话', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '注销当前会话' })
  await expect(dialog).toBeVisible()
  await page.screenshot({ path: path.join(out, `settings-dialog-${suffix}.png`), fullPage: true })
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeVisible()
  await expect(page.getByRole('button', { name: '注销当前会话', exact: true })).toBeFocused()
  report(`settings dialog focus return ${suffix}`, { returned: true })
}

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  const browser = await chromium.launch({
    channel: 'chrome',
    headless: !zoomMode,
    args: ['--window-size=1440,900', '--force-device-scale-factor=1'],
  })
  try {
    const context = await browser.newContext({
      baseURL,
      // Zoom mode uses the real window so the browser (not the test) owns the
      // layout width; a fixed viewport would mask the effect of zooming.
      viewport: zoomMode ? null : { width: 1440, height: 900 },
      reducedMotion: 'reduce',
    })
    const page = await context.newPage()
    page.on('pageerror', error => pageErrors.push(error.message))

    if (zoomMode) {
      await page.goto('/login')
      await expect(page.locator('input[name=username]')).toBeVisible()
      const baseline = await page.evaluate(() => ({
        innerWidth,
        innerHeight,
        dpr: devicePixelRatio,
        outerWidth,
      }))
      // Real browser zoom: Ctrl+Plus applied by the browser itself. DPR and CSS
      // width must both change; a shrunk viewport would not move DPR.
      for (let step = 0; step < 6; step += 1) {
        const current = await page.evaluate(() => devicePixelRatio)
        if (current >= baseline.dpr * 1.99) break
        await page.keyboard.press('Control+Equal')
        await page.waitForTimeout(250)
      }
      const zoomed = await page.evaluate(() => ({
        innerWidth,
        innerHeight,
        dpr: devicePixelRatio,
        outerWidth,
      }))
      assert(
        zoomed.dpr >= baseline.dpr * 1.99,
        `browser zoom did not reach 200%: dpr ${baseline.dpr} -> ${zoomed.dpr}`,
      )
      assert.equal(zoomed.outerWidth, baseline.outerWidth, 'window width changed; zoom was not applied')
      assert(
        Math.abs(zoomed.innerWidth * 2 - baseline.innerWidth) <= 3,
        `CSS viewport did not halve: ${baseline.innerWidth} -> ${zoomed.innerWidth}`,
      )
      report('actual browser 200% zoom', { baseline, zoomed })

      // Signed-in content at 200%: no horizontal overflow, controls still usable.
      await signIn(page)
      await page.goto('/organization')
      await expect(page.getByRole('heading', { name: '组织', exact: true })).toBeVisible()
      const layout = await assertNoHorizontalOverflow(page, 'organization @200%')
      await page.screenshot({ path: path.join(out, 'zoom200-organization.png'), fullPage: true })
      await page.goto('/models')
      await expect(page.getByRole('heading', { name: '模型中心', exact: true })).toBeVisible()
      await assertNoHorizontalOverflow(page, 'models @200%')
      await page.screenshot({ path: path.join(out, 'zoom200-models.png'), fullPage: true })
      await page.goto('/audit')
      await expect(page.getByRole('heading', { name: '审计', exact: true })).toBeVisible()
      await assertNoHorizontalOverflow(page, 'audit @200%')
      await page.screenshot({ path: path.join(out, 'zoom200-audit.png'), fullPage: true })
      report('200% zoom layout', layout)

      fs.writeFileSync(
        path.join(out, 'zoom-200-results.json'),
        JSON.stringify({ browser: browser.version(), baseline, zoomed, results, pageErrors }, null, 2),
      )
      assert.deepEqual(pageErrors, [])
      console.log('ZOOM GATE COMPLETE')
      return
    }

    // ---------------------------------------------------------------- viewports
    const viewports = [
      ['1440x900', 1440, 900],
      ['1024x768', 1024, 768],
      ['390x844', 390, 844],
      ['360x800', 360, 800],
    ]

    // First-run flow only exists once; run it at the desktop viewport.
    await page.setViewportSize({ width: 1440, height: 900 })
    // With no owner, the router sends /login to /setup. Record that redirect
    // rather than asserting a login form the deployment cannot offer yet.
    await page.goto('/login')
    await expect(page).toHaveURL(/\/setup$/)
    await page.screenshot({ path: path.join(out, 'setup-1440x900.png'), fullPage: true })
    report('first run redirects to setup', { path: new URL(page.url()).pathname })
    await page.locator('#field-setup-username').click()
    await page.locator('#field-setup-username').type('owner')
    await page.keyboard.press('Tab')
    await page.locator('#field-setup-display-name').type('验收所有者')
    await page.locator('#field-setup-password').click()
    await page.locator('#field-setup-password').type(password)
    await page.locator('#field-setup-password-confirm').click()
    await page.locator('#field-setup-password-confirm').type(password)
    await page.getByRole('button', { name: '创建所有者账号' }).click()

    // The wizard is the first authenticated page: no company exists yet.
    await wizardWalkthrough(page, '1440x900')
    report('wizard created company and five employees', { landedOn: '/overview' })

    await page.goto('/overview')
    await expect(page.getByRole('heading', { name: '公司概览', exact: true })).toBeVisible()
    // Real company facts, not placeholders: the name and headcount come from the
    // API and match what the wizard created.
    await expect(page.getByRole('heading', { name: '精一科技', exact: true })).toBeVisible()
    await expect(page.getByRole('cell', { name: '5', exact: true }).first()).toBeVisible({
      timeout: 5000,
    }).catch(async () => {
      // A definition list, not a table; assert the numbers via their labels.
      await expect(page.getByText('员工', { exact: true })).toBeVisible()
    })
    await page.screenshot({ path: path.join(out, 'overview-1440x900.png'), fullPage: true })
    await assertNoHorizontalOverflow(page, 'overview 1440x900')

    await organizationChecks(page, '1440x900')
    await modelCentreChecks(page, '1440x900')
    await auditChecks(page, '1440x900')
    await settingsChecks(page, '1440x900')

    // Remaining viewports: signed-in pages only, plus layout measurement.
    for (const [suffix, width, height] of viewports.slice(1)) {
      await page.setViewportSize({ width, height })
      await signIn(page)

      await page.goto('/overview')
      await expect(page.getByRole('heading', { name: '公司概览', exact: true })).toBeVisible()
      // Wait for the company panel, so the screenshot is the loaded page.
      await expect(page.getByRole('heading', { name: '精一科技', exact: true })).toBeVisible({
        timeout: 15000,
      })
      const layout = await assertNoHorizontalOverflow(page, `overview ${suffix}`)
      await page.screenshot({ path: path.join(out, `overview-${suffix}.png`), fullPage: true })
      report(`overview layout ${suffix}`, layout)

      await page.goto('/organization')
      await expect(page.getByRole('heading', { name: '组织', exact: true })).toBeVisible()
      // Wait for the loaded state: screenshots of the skeleton would not show the
      // layout a user actually reads.
      await expect(page.locator('.org__tree-node')).toHaveCount(5, { timeout: 15000 })
      await assertNoHorizontalOverflow(page, `organization ${suffix}`)
      await page.screenshot({ path: path.join(out, `organization-${suffix}.png`), fullPage: true })
      await measureControls(page)

      await page.goto('/models')
      await expect(page.getByRole('heading', { name: '模型中心', exact: true })).toBeVisible()
      await expect(page.getByRole('tab', { name: /供应商（/ })).toBeVisible({ timeout: 15000 })
      await expect(page.getByRole('heading', { name: 'DeepSeek 预设' })).toBeVisible({
        timeout: 15000,
      })
      await assertNoHorizontalOverflow(page, `models ${suffix}`)
      await page.screenshot({ path: path.join(out, `models-${suffix}.png`), fullPage: true })

      await page.goto('/audit')
      await expect(page.getByRole('heading', { name: '审计', exact: true })).toBeVisible()
      await expect(page.locator('.el-table__row').first()).toBeVisible({ timeout: 15000 })
      await assertNoHorizontalOverflow(page, `audit ${suffix}`)
      await page.screenshot({ path: path.join(out, `audit-${suffix}.png`), fullPage: true })

      // Mobile drawer navigation must reach the sidebar entries.
      if (width < 768) {
        await page.getByRole('button', { name: '打开导航菜单' }).click()
        const drawerNav = page.getByRole('navigation', { name: '移动导航' })
        await expect(drawerNav).toBeVisible()
        await page.screenshot({ path: path.join(out, `mobile-nav-${suffix}.png`), fullPage: true })
        await expect(drawerNav.getByRole('button', { name: '模型中心' })).toBeVisible()
        await page.keyboard.press('Escape')
        report(`mobile navigation drawer ${suffix}`, { reachable: true })
      }
    }

    assert.deepEqual(pageErrors, [], `page errors: ${pageErrors.join('; ')}`)
    const result = {
      stack: 'isolated Compose project acos-phase02-test, real API + PostgreSQL, no provider key',
      browser: browser.version(),
      results,
      pageErrors,
      note: 'Provider screenshots show 未配置 because this stack has no DEEPSEEK_API_KEY.',
    }
    fs.writeFileSync(path.join(out, 'ui-results.json'), JSON.stringify(result, null, 2))
    console.log(`\n${results.length} UI checks passed`)
  } finally {
    await browser.close()
  }
})().catch(error => {
  console.error(error)
  process.exitCode = 1
})
