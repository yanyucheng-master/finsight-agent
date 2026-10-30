// 端到端冒烟测试：node frontend/e2e/smoke.cjs（需先启动 finsight serve；截图输出到 e2e/out）
const { chromium } = require('playwright');
const OUT = process.env.E2E_OUT || __dirname + '/out';
const BASE = process.env.BASE_URL || 'http://127.0.0.1:8000';
require('fs').mkdirSync(OUT, { recursive: true });
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1360, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  await page.goto(BASE + '/#/');
  await page.waitForSelector('text=样本公司');
  await page.waitForSelector('text=低风险', { timeout: 30000 });
  await page.screenshot({ path: OUT + '/1_home.png', fullPage: true });
  await page.goto(BASE + '/#/c/000002');
  await page.waitForSelector('text=综合风险评级', { timeout: 30000 });
  await page.screenshot({ path: OUT + '/2_risk.png', fullPage: false });
  // 点击信号中的“源”引用 → 证据抽屉
  await page.locator('button:has-text("源")').first().click();
  await page.waitForSelector('text=证据链');
  await page.waitForTimeout(500);
  await page.screenshot({ path: OUT + '/3_drawer.png' });
  await page.keyboard.press('Escape');
  await page.click('text=研究简报');
  await page.click('text=模板简报（无需大模型）');
  await page.waitForSelector('text=抽取到的数字均已按引用核验', { timeout: 30000 });
  await page.screenshot({ path: OUT + '/4_brief.png', fullPage: true });
  // 口径提示必须让核验条显示为“未通过”（用 2026-10-29 真实 Kimi 简报重新核验后的结果作为接口响应）
  const fixture = require('fs').readFileSync(__dirname + '/fixture_report_consistency.json', 'utf-8');
  const doneJob = { id: 'fixture', kind: 'report', code: '000002', live: false, status: 'done', stage: 'done', detail: '',
    events: [], elapsed_s: 0, stages: ['evidence', 'drafting', 'verifying', 'revising', 'done'], error: null,
    result: JSON.parse(fixture) };
  await page.route('**/api/companies/*/report/jobs*', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(doneJob) }));
  await page.click('text=模板简报（无需大模型）');
  await page.waitForSelector('text=数字一致，但存在口径或合规提示', { timeout: 30000 });
  await page.waitForSelector('text=口径提示');
  await page.screenshot({ path: OUT + '/4b_brief_consistency.png', fullPage: true });
  await page.unroute('**/api/companies/*/report/jobs*');
  await page.click('nav >> text=指标趋势');
  await page.waitForTimeout(800);
  await page.screenshot({ path: OUT + '/5_metrics.png' });
  await page.click('nav >> text=财务报表');
  await page.waitForTimeout(300);
  await page.locator('td.font-mono').first().click();
  await page.waitForSelector('text=SHA-256');
  await page.screenshot({ path: OUT + '/6_statement_drawer.png' });
  await page.keyboard.press('Escape');
  await page.click('nav >> text=智能问答');
  // 没有配置 Key 时，提问按钮不可用，并明确说明原因（而不是点了才报错）
  await page.waitForSelector('text=服务端未配置大模型 Key');
  if (!(await page.locator('button:has-text("提问")').isDisabled())) throw new Error('无 Key 时提问按钮应禁用');
  await page.screenshot({ path: OUT + '/7_ask_nollm.png' });
  console.log('errors:', JSON.stringify(errors));
  await browser.close();
})().catch(e => { console.error('E2E FAILED', e); process.exit(1); });
