// 异步生成 / 阶段进度 / 缓存样例 / 额度的端到端测试。需先运行：python frontend/e2e/serve_mock.py 8765
// 用法：BASE_URL=http://127.0.0.1:8765 node frontend/e2e/smoke_async.cjs
const { chromium } = require('playwright');
const OUT = process.env.E2E_OUT || __dirname + '/out';
const BASE = process.env.BASE_URL || 'http://127.0.0.1:8765';
require('fs').mkdirSync(OUT, { recursive: true });
const must = (cond, msg) => { if (!cond) throw new Error('断言失败：' + msg); };
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1360, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  await page.goto(BASE + '/#/c/600519');
  await page.waitForSelector('text=综合风险评级', { timeout: 30000 });
  await page.click('nav >> text=研究简报');

  // 1) 有缓存样例时直接秒开，并如实说明这是重新核验过的缓存
  await page.waitForSelector('text=缓存样例', { timeout: 5000 });
  await page.waitForSelector('text=用当前版本核验器重新核验', { timeout: 5000 });
  await page.waitForSelector('text=抽取到的数字均已按引用核验');
  await page.screenshot({ path: OUT + '/a1_cached_report.png', fullPage: true });

  // 2) 先做一次实时问答：看到“工具取证”阶段，完成后出现带核验条的回答
  await page.click('nav >> text=智能问答');
  await page.waitForSelector('text=实时生成今日剩余');
  await page.fill('input[placeholder^="向研究智能体提问"]', '茅台毛利率是多少？');
  await page.click('button:has-text("提问")');
  await page.waitForSelector('text=工具取证', { timeout: 10000 });
  await page.screenshot({ path: OUT + '/a2_ask_progress.png' });
  await page.waitForSelector('text=问：茅台毛利率是多少？', { timeout: 30000 });
  await page.waitForSelector('text=抽取到的数字均已按引用核验');

  // 3) 缓存的问答样例可以秒开
  const sampleChips = page.locator('button.rounded-full.border-amber-300');
  if (await sampleChips.count()) {
    await sampleChips.first().click();
    await page.waitForSelector('text=缓存样例', { timeout: 5000 });
  }
  await page.screenshot({ path: OUT + '/a3_ask_done.png', fullPage: true });

  // 4) 实时生成简报：阶段条依次出现
  await page.click('nav >> text=研究简报');
  await page.click('button:has-text("AI 实时生成简报")');
  await page.waitForSelector('text=整理证据', { timeout: 10000 });
  await page.waitForSelector('text=逐个核验数字', { timeout: 15000 });
  await page.screenshot({ path: OUT + '/a4_report_progress.png' });
  await page.waitForSelector('text=大模型（mock-llm）', { timeout: 30000 });

  // 5) 额度用完（每位访客 2 次）：按钮禁用，并显示 0
  await page.waitForFunction(() => document.body.innerText.includes('实时生成今日剩余 0'), null, { timeout: 10000 });
  must(await page.locator('button:has-text("AI 实时生成简报")').isDisabled(), '额度用完后实时生成按钮应禁用');
  await page.screenshot({ path: OUT + '/a5_quota_exhausted.png' });

  // 6) 模板简报不受额度限制
  await page.click('button:has-text("模板简报")');
  await page.waitForSelector('text=确定性模板', { timeout: 15000 });

  console.log('errors:', JSON.stringify(errors));
  await browser.close();
  if (errors.length) process.exit(2);
})().catch(e => { console.error('E2E FAILED', e); process.exit(1); });
