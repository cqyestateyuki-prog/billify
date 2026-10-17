// Walk the PRISM demo story and save screens: v1 fails → alert → root cause → replay v2 → pass.
//   node "design/billify-dark/capture.js"
const path = require('path');
const { chromium } = require(path.join(process.env.HOME, 'Desktop/tools/ui-diff/node_modules/playwright'));
const URL = 'file://' + path.join(__dirname, 'billify.html');
const out = (n) => path.join(__dirname, 'screens', n + '.png');
(async () => {
  const browser = await chromium.launch({ channel: 'chrome' });
  const errors = [];
  const run = async (w, h, tag) => {
    const p = await browser.newPage({ viewport: { width: w, height: h } });
    p.on('pageerror', (e) => errors.push(tag + ': ' + e.message));
    await p.goto(URL); await p.waitForTimeout(1200);
    const shot = async (n, full) => { await p.screenshot({ path: out(tag + n), fullPage: !!full }); console.log('saved', tag + n); };
    if (tag === '') await shot('01-home');
    await p.click('#h-set'); await p.check('#s-raw');
    if (tag === '') await shot('02-demo-settings-v1');
    await p.click('#h-set');
    await p.click('#b-try'); await p.waitForTimeout(300);
    if (tag === '') await shot('03-staged-7');
    await p.click('#b-read'); await p.waitForSelector('#v-review:not([hidden])', { timeout: 15000 }); await p.waitForTimeout(300);
    await shot('04-review-v1');
    await p.click('[data-steps]:nth-of-type(1) >> nth=1'); await p.waitForTimeout(300);
    await shot('05-steps-before-send-v1');
    await p.keyboard.press('Escape');
    await p.click('#b-transfer'); await p.waitForSelector('#tr-actions:not([hidden])', { timeout: 90000 }); await p.waitForTimeout(400);
    await shot('06-transfer-v1-alert-root-cause', true);
    await p.click('#tr-list [data-steps] >> nth=1'); await p.waitForTimeout(300);
    await shot('07-steps-failed-run-v1');
    await p.keyboard.press('Escape');
    await p.click('#b-replay'); await p.waitForTimeout(500);
    await p.waitForSelector('#tr-actions:not([hidden])', { timeout: 90000 }); await p.waitForTimeout(400);
    await shot('08-replay-v2-all-created', true);
    await p.click('#tr-list [data-steps] >> nth=1'); await p.waitForTimeout(300);
    await shot('09-steps-same-invoice-v2');
    const overflow = await p.evaluate(() => document.documentElement.scrollWidth - innerWidth);
    console.log(tag || 'desktop', 'horizontal overflow:', overflow);
    await p.close();
  };
  await run(1440, 900, '');
  await run(390, 844, 'phone-');
  await browser.close();
  console.log(errors.length ? 'JS ERRORS\n' + errors.join('\n') : 'no JS errors');
  process.exit(errors.length ? 1 : 0);
})();
