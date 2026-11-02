/**
 * 200% zoom gate: two explicitly separated checks.
 *
 * `--method=equiv` (default)
 *   Reproduces the *CSS conditions* of Chrome page zoom at 200% — layout viewport
 *   halved, devicePixelRatio doubled — with CDP `Emulation.setDeviceMetricsOverride`,
 *   then verifies the Phase 02 pages at that size. This is emulation. It proves the
 *   layout survives a 200% layout viewport with DPR 2; it is NOT the browser's zoom
 *   feature and must never be reported as such.
 *
 * `--method=os-input`
 *   The real thing: launch a real Chrome window, send the OS's Ctrl+'+' and require
 *   DPR to double while the window width stays fixed. On this host it cannot pass:
 *   the automated window can be made foreground (`GetForegroundWindow()` matches),
 *   yet OS-injected input never reaches its renderer — a page-installed keydown
 *   listener records zero events. The run writes that diagnosis to evidence instead
 *   of silently substituting emulation.
 *
 * Both methods write separate result files so a reader can never confuse them.
 */
const { chromium } = require('playwright/test')
const { spawn, execFileSync } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')
const os = require('node:os')
const assert = require('node:assert/strict')

const baseURL = 'http://127.0.0.1:18083'
const out = path.resolve(__dirname, '../docs/acceptance/evidence/phase-02')
const password = 'phase02-acceptance-password' // disposable QA stack only
const chromePath = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const methodArg = process.argv.find(arg => arg.startsWith('--method='))
const method = methodArg ? methodArg.split('=')[1] : 'equiv'

/** Per-page layout facts; also the 12px text floor and overflow check. */
async function measure(page, label) {
  const data = await page.evaluate(() => {
    const textNodes = Array.from(
      document.querySelectorAll('p, span, td, li, button, label, dd, dt, a, h1, h2, h3'),
    ).filter(node => (node.textContent ?? '').trim().length > 0)
    const sizes = textNodes
      .map(node => parseFloat(getComputedStyle(node).fontSize))
      .filter(value => Number.isFinite(value) && value > 0)
    return {
      dpr: devicePixelRatio,
      innerWidth,
      innerHeight,
      scrollWidth: document.documentElement.scrollWidth,
      minFontPx: sizes.length ? Math.min(...sizes) : null,
    }
  })
  assert(
    data.scrollWidth <= data.innerWidth + 1,
    `${label}: content ${data.scrollWidth}px exceeds viewport ${data.innerWidth}px`,
  )
  assert(
    data.minFontPx === null || data.minFontPx >= 12,
    `${label}: text down to ${data.minFontPx}px, below the 12px floor`,
  )
  return data
}

async function signIn(page) {
  await page.goto('/login')
  await page.waitForLoadState('networkidle')
  // Wait for either the login form or a redirect away from /login, instead of
  // sampling visibility once (the guard may still be resolving).
  await page
    .waitForFunction(
      () =>
        Boolean(document.querySelector('input[name=username]')) ||
        !location.pathname.startsWith('/login'),
      undefined,
      { timeout: 20000 },
    )
    .catch(() => {})
  if (!(await page.locator('input[name=username]').isVisible().catch(() => false))) {
    return
  }
  await page.locator('input[name=username]').click()
  await page.locator('input[name=username]').type('owner')
  await page.locator('input[name=password]').click()
  await page.locator('input[name=password]').type(password)
  await page.getByRole('button', { name: /登录|正在登录/ }).click()
  // Wait for the SPA to leave /login. A 401 in a later request can legitimately
  // bounce it back, so this is checked before any measuring happens.
  await page.waitForFunction(() => !location.pathname.startsWith('/login'), undefined, {
    timeout: 20000,
  })
  await page.waitForLoadState('networkidle')
}

async function runEquivalence() {
  const browser = await chromium.launch({
    channel: 'chrome',
    headless: true,
    args: ['--window-size=1440,900'],
  })
  try {
    const context = await browser.newContext({
      baseURL,
      viewport: { width: 1440, height: 900 },
      reducedMotion: 'reduce',
    })
    const page = await context.newPage()
    const pageErrors = []
    page.on('pageerror', error => pageErrors.push(error.message))

    // Start from a fixed, known viewport so the halved/doubled comparison is
    // against the geometry this script controls.
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/login')
    await page.waitForLoadState('networkidle')
    const baseline = await page.evaluate(() => ({
      dpr: devicePixelRatio,
      innerWidth,
      innerHeight,
    }))
    console.log('BASELINE', JSON.stringify(baseline))

    // Sign in at the normal size first; then resize the same document to the
    // geometry of a 200% zoomed window. Doing it in this order keeps the login
    // flow itself out of the measurement and avoids re-authenticating.
    await signIn(page)
    const probe = await page.evaluate(async () => {
      const response = await fetch('/api/v1/auth/session', { credentials: 'same-origin' })
      const body = await response.text()
      return { status: response.status, body: body.slice(0, 160), cookie: document.cookie }
    })
    console.log('session probe:', JSON.stringify(probe), 'url:', page.url())
    assert.equal(probe.status, 200, `not signed in before measuring pages (session ${probe.status})`)

    const cdp = await context.newCDPSession(page)
    // 1440x900 CSS at DPR 1  ->  720x450 CSS at DPR 2: the CSS geometry Chrome
    // produces when page zoom is exactly 200% in the same physical window.
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: Math.round(baseline.innerWidth / 2),
      height: Math.round(baseline.innerHeight / 2),
      deviceScaleFactor: baseline.dpr * 2,
      mobile: false,
      scale: 1,
      screenWidth: baseline.innerWidth,
      screenHeight: baseline.innerHeight,
    })
    await page.waitForTimeout(300)
    const zoomed = await page.evaluate(() => ({
      dpr: devicePixelRatio,
      innerWidth,
      innerHeight,
      outerWidth,
    }))
    assert(
      Math.abs(zoomed.dpr - baseline.dpr * 2) < 0.01,
      `DPR did not double: ${baseline.dpr} -> ${zoomed.dpr}`,
    )
    assert(
      Math.abs(zoomed.innerWidth * 2 - baseline.innerWidth) <= 2,
      `CSS viewport did not halve: ${baseline.innerWidth} -> ${zoomed.innerWidth}`,
    )

    const pages = [
      ['overview', '公司概览'],
      ['organization', '组织'],
      ['models', '模型中心'],
      ['audit', '审计'],
      ['setup/company', null],
    ]
    const layouts = []
    for (const [route, heading] of pages) {
      await page.goto(`/${route}`)
      await page.waitForLoadState('networkidle')
      if (heading) {
        await page.getByRole('heading', { name: heading, exact: true }).waitFor({ timeout: 15000 })
      }
      const layout = await measure(page, `${route} @200% equivalent`)
      await page.screenshot({
        path: path.join(out, `zoom200equiv-${route.replace('/', '-')}.png`),
        fullPage: true,
      })
      layouts.push({ route, ...layout })
      console.log(`PASS ${route} @200% equivalent`, JSON.stringify(layout))
    }

    // Employee drawer at 200%: content must scroll internally, not overflow.
    await page.goto('/organization')
    await page.waitForLoadState('networkidle')
    await page.locator('.org__tree-button').first().click()
    const drawer = page.getByRole('dialog', { name: /首席执行官/ })
    await drawer.waitFor({ timeout: 15000 })
    const drawerBox = await drawer.boundingBox()
    const viewport = page.viewportSize()
    assert(drawerBox && viewport, 'drawer or viewport missing')
    assert(
      drawerBox.width <= viewport.width + 1 && drawerBox.height <= viewport.height + 1,
      `drawer ${drawerBox.width}x${drawerBox.height} exceeds viewport ${viewport.width}x${viewport.height}`,
    )
    await page.screenshot({ path: path.join(out, 'zoom200equiv-drawer.png'), fullPage: true })
    console.log('PASS drawer fits viewport @200% equivalent', JSON.stringify(drawerBox))

    assert.deepEqual(pageErrors, [], `page errors: ${pageErrors.join('; ')}`)
    const result = {
      method:
        'CDP Emulation.setDeviceMetricsOverride: CSS viewport halved + DPR doubled (equivalent layout conditions of 200% page zoom)',
      explicitly_not: 'the browser zoom feature; see zoom-200-real.json',
      browser: browser.version(),
      baseline,
      zoomed,
      layouts,
      drawer: drawerBox,
      pageErrors,
    }
    fs.writeFileSync(path.join(out, 'zoom-200-equivalent.json'), JSON.stringify(result, null, 2))
    console.log('EQUIVALENT ZOOM GATE PASSED')
  } finally {
    await browser.close()
  }
}

async function runOsInput() {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'acos-zoom200-'))
  const port = 9410
  const chrome = spawn(
    chromePath,
    [
      `--user-data-dir=${profile}`,
      `--remote-debugging-port=${port}`,
      '--window-size=1440,900',
      '--window-position=20,20',
      '--no-first-run',
      '--no-default-browser-check',
      `${baseURL}/login`,
    ],
    { stdio: 'ignore' },
  )

  let browser
  try {
    for (let attempt = 0; attempt < 80; attempt += 1) {
      try {
        if ((await fetch(`http://127.0.0.1:${port}/json/version`)).ok) break
      } catch {
        /* not up yet */
      }
      await new Promise(resolve => setTimeout(resolve, 400))
    }
    browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`)
    const context = browser.contexts()[0]
    const page = context.pages()[0] ?? (await context.newPage())
    await page.waitForLoadState('networkidle')

    // Instrument the renderer so "input arrived" is measurable, not assumed.
    await page.evaluate(() => {
      window.__receivedKeys = []
      window.addEventListener(
        'keydown',
        event => window.__receivedKeys.push(`${event.key}:${event.ctrlKey}`),
        true,
      )
    })

    const baseline = await page.evaluate(() => ({
      dpr: devicePixelRatio,
      innerWidth,
      outerWidth,
    }))
    console.log('BASELINE', JSON.stringify(baseline))

    const diagnosis = execFileSync(
      'powershell.exe',
      [
        '-NoProfile',
        '-Command',
        `
Add-Type -TypeDefinition @'
using System; using System.Text; using System.Runtime.InteropServices;
public class Z {
  [StructLayout(LayoutKind.Sequential)] public struct KEYBDINPUT { public ushort wVk; public ushort wScan; public uint dwFlags; public uint time; public IntPtr dwExtraInfo; }
  [StructLayout(LayoutKind.Explicit, Size=40)] public struct INPUT { [FieldOffset(0)] public uint type; [FieldOffset(8)] public KEYBDINPUT ki; }
  [DllImport("user32.dll")] public static extern uint SendInput(uint n, INPUT[] p, int cb);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool attach);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetActiveWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
}
'@
$target = Get-Process chrome | Where-Object { $_.MainWindowTitle -like '*AI Company OS*' } | Select-Object -First 1
if (-not $target) { Write-Output "NOTARGET"; exit }
$h = $target.MainWindowHandle
$fg = [Z]::GetForegroundWindow(); $fgPid = 0
$fgTid = [Z]::GetWindowThreadProcessId($fg, [ref]$fgPid)
$tPid = 0; $tTid = [Z]::GetWindowThreadProcessId($h, [ref]$tPid)
$myTid = [Z]::GetCurrentThreadId()
[Z]::AttachThreadInput($myTid, $fgTid, $true) | Out-Null
[Z]::AttachThreadInput($myTid, $tTid, $true) | Out-Null
[Z]::BringWindowToTop($h) | Out-Null
[Z]::SetForegroundWindow($h) | Out-Null
[Z]::SetActiveWindow($h) | Out-Null
Start-Sleep -Milliseconds 600
$sb = New-Object System.Text.StringBuilder 300
[Z]::GetWindowText($h, $sb, 300) | Out-Null
$match = ([Z]::GetForegroundWindow() -eq $h)
Write-Output ("foregroundMatch=" + $match)
Write-Output ("targetTitle=" + $sb.ToString())
for ($i = 0; $i -lt 6; $i++) {
  $arr = New-Object 'Z+INPUT[]' 4
  $arr[0].type = 1; $arr[0].ki.wVk = 0x11
  $arr[1].type = 1; $arr[1].ki.wVk = 0xBB
  $arr[2].type = 1; $arr[2].ki.wVk = 0xBB; $arr[2].ki.dwFlags = 2
  $arr[3].type = 1; $arr[3].ki.wVk = 0x11; $arr[3].ki.dwFlags = 2
  $sent = [Z]::SendInput(4, $arr, 40)
  if ($sent -ne 4) { Write-Output ("sendinputPartial=" + $sent) }
  Start-Sleep -Milliseconds 300
}
[Z]::AttachThreadInput($myTid, $fgTid, $false) | Out-Null
[Z]::AttachThreadInput($myTid, $tTid, $false) | Out-Null
Write-Output "sent=6"
`,
      ],
      { encoding: 'utf8' },
    )
    console.log(diagnosis.trim())

    await new Promise(resolve => setTimeout(resolve, 1500))
    const receivedKeys = await page.evaluate(() => window.__receivedKeys)
    const zoomed = await page.evaluate(() => ({
      dpr: devicePixelRatio,
      innerWidth,
      outerWidth,
    }))
    console.log('ZOOMED', JSON.stringify(zoomed), 'keys received:', JSON.stringify(receivedKeys))

    const zoomApplied =
      Math.abs(zoomed.dpr - baseline.dpr * 2) < 0.02 &&
      zoomed.outerWidth === baseline.outerWidth &&
      Math.abs(zoomed.innerWidth * 2 - baseline.innerWidth) <= 4

    const result = {
      method: "real Chrome window + Windows SendInput Ctrl+'=' x6 (browser page zoom)",
      foregroundAchieved: diagnosis.includes('foregroundMatch=True'),
      rendererReceivedKeyEvents: receivedKeys,
      baseline,
      zoomed,
      zoomApplied,
      conclusion: zoomApplied
        ? 'actual browser 200% zoom verified'
        : 'blocked: OS-injected input does not reach the automated Chrome window renderer on this host',
      evidenceForBlock:
        receivedKeys.length === 0
          ? 'The automated window was made foreground, yet its page recorded zero keydown events, so the browser never saw the zoom accelerator.'
          : 'Key events reached the renderer but the accelerator did not change page zoom.',
    }
    fs.writeFileSync(path.join(out, 'zoom-200-real.json'), JSON.stringify(result, null, 2))

    if (!zoomApplied) {
      console.error(
        'REAL BROWSER ZOOM NOT VERIFIED — see docs/acceptance/evidence/phase-02/zoom-200-real.json',
      )
      process.exitCode = 2
      return
    }

    await signIn(page)
    for (const [route, heading] of [
      ['overview', '公司概览'],
      ['organization', '组织'],
      ['models', '模型中心'],
      ['audit', '审计'],
    ]) {
      await page.goto(`${baseURL}/${route}`)
      await page.waitForLoadState('networkidle')
      await page.getByRole('heading', { name: heading, exact: true }).waitFor({ timeout: 15000 })
      const layout = await measure(page, `${route} @real 200% zoom`)
      await page.screenshot({ path: path.join(out, `zoom200-${route}.png`), fullPage: true })
      console.log(`PASS ${route} @real 200% zoom`, JSON.stringify(layout))
    }
    console.log('REAL ZOOM GATE PASSED')
  } finally {
    if (browser) await browser.close().catch(() => {})
    chrome.kill()
    await new Promise(resolve => setTimeout(resolve, 1200))
    fs.rmSync(profile, { recursive: true, force: true })
  }
}

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  if (method === 'os-input') {
    await runOsInput()
  } else {
    await runEquivalence()
  }
})().catch(error => {
  console.error(error)
  process.exitCode = 1
})
