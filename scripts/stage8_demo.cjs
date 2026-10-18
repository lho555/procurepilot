// Read-only browser acceptance. Requires an existing Playwright installation and Edge.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const reportPath = process.argv[2];
if (!reportPath) throw new Error('Usage: node scripts/stage8_demo.cjs <Stage7 report.json>');
const reportBytes = fs.readFileSync(reportPath);
const report = JSON.parse(reportBytes.toString('utf8').replace(/^\uFEFF/, ''));
if (!report.passed || report.task_count !== 17) throw new Error('Use a passing 17-task report from scripts/run_evals.py');
const output = path.join(root, '.local', 'stage8');
fs.mkdirSync(output, { recursive: true });
(async () => {
  const context = await chromium.launchPersistentContext(path.join(output, 'browser'), { channel: 'msedge', headless: true });
  const checks = [], errors = [], writes = [];
  try {
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/**', async route => {
      const request = route.request();
      if (request.method() !== 'GET' && !request.url().endsWith('/agent/analyze')) {
        writes.push(request.url()); await route.abort();
      } else await route.continue();
    });
    await page.goto('http://127.0.0.1:5173');
    await page.getByText('后端运行正常', { exact: true }).waitFor();
    const analysisResponse = page.waitForResponse(response => response.url().endsWith('/agent/analyze'));
    await page.getByRole('button', { name: '运行只读分析', exact: true }).click();
    const analysis = await (await analysisResponse).json();
    if (analysis.status !== 'COMPLETED' || !analysis.read_only || !analysis.tool_calls.some(call => call.transport === 'mcp:stdio')) throw new Error('Missing real MCP analysis');
    await page.getByText('Final Result · 分析结论', { exact: true }).waitFor();
    await page.getByText('Agent Timeline · 执行记录与工具参数', { exact: true }).waitFor();
    checks.push('real_analysis_timeline_tools_evidence_result');
    for (const tab of ['持久工作流', '人工审批', '业务查询', '制度证据']) {
      await page.getByRole('tab', { name: tab, exact: true }).click();
      const selected = page.getByRole('tab', { name: tab, exact: true });
      if (await selected.getAttribute('aria-selected') !== 'true') throw new Error('Navigation did not select ' + tab);
      if (tab === '持久工作流') {
        await page.getByLabel('工作流幂等键', { exact: true }).fill('stage8-cancel-only');
        await page.getByRole('button', { name: '创建并提交审批', exact: true }).click();
        await page.getByRole('dialog').getByRole('button', { name: /取\s*消/ }).click();
        await page.getByRole('dialog').waitFor({ state: 'hidden' });
      }
    }
    await page.getByRole('tab', { name: '任务与分析', exact: true }).click();
    await page.getByText('Final Result · 分析结论', { exact: true }).waitFor();
    await page.getByRole('tab', { name: '持久工作流', exact: true }).click();
    if (await page.getByLabel('工作流幂等键', { exact: true }).inputValue() !== 'stage8-cancel-only') throw new Error('Tab switch lost form state');
    checks.push('all_workbench_sections_navigate');
    await page.getByRole('tab', { name: 'Evaluation', exact: true }).click();
    await page.getByText('尚未导入报告。不会显示预设成绩。', { exact: true }).waitFor();
    const input = page.getByLabel('选择评测报告', { exact: true });
    const upload = data => input.setInputFiles({ name: 'report.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(data)) });
    await upload(report);
    await page.getByText('报告结果：全部通过', { exact: true }).waitFor();
    await page.getByRole('cell', { name: '17 / 17', exact: true }).waitFor();
    await page.getByRole('cell', { name: '0 / 17', exact: true }).waitFor();
    await page.locator('.ant-pagination-item-2').click();
    await page.getByRole('cell', { name: 'restart_resume', exact: true }).waitFor();
    checks.push('passing_report_denominators_and_task_pagination');
    await page.screenshot({ path: path.join(output, 'evaluation.png'), fullPage: true });
    const failed = structuredClone(report);
    failed.passed = false; failed.results[0].passed = false; failed.results[0].checks.execution = false;
    failed.results[0].error_type = 'SyntheticFailure'; failed.metrics.task_success.numerator--;
    await upload(failed);
    await page.getByText('报告结果：未通过', { exact: true }).waitFor();
    await page.getByRole('cell', { name: 'execution、SyntheticFailure', exact: true }).waitFor();
    checks.push('failed_task_visible');
    const partial = structuredClone(report);
    partial.passed = false; partial.results = []; partial.evaluated_count = 0;
    for (const metric of Object.values(partial.metrics)) { metric.numerator = 0; metric.denominator = 0; }
    await upload(partial);
    await page.getByText('报告结果：未通过', { exact: true }).waitFor();
    if (await page.getByRole('cell', { name: '不适用', exact: true }).count() !== 7) throw new Error('Missing N/A');
    checks.push('partial_report_failed_and_na');
    await input.setInputFiles({ name: 'broken.json', mimeType: 'application/json', buffer: Buffer.from('{') });
    await page.getByText('无法读取评测报告', { exact: true }).waitFor();
    if (await page.getByText('报告结果：全部通过', { exact: true }).count()) throw new Error('Stale report shown');
    checks.push('malformed_report_clears_stale_result');
    const invalid = structuredClone(report); invalid.metrics.task_success.numerator = 18;
    await upload(invalid);
    await page.getByText('指标分子 / 分母不正确', { exact: true }).waitFor();
    checks.push('invalid_counts_rejected');
    const inconsistent = structuredClone(report); inconsistent.results[0].passed = false;
    await upload(inconsistent);
    await page.getByText('任务结果格式或检查状态不一致', { exact: true }).waitFor();
    checks.push('inconsistent_summary_rejected');
    await input.setInputFiles({ name: 'large.json', mimeType: 'application/json', buffer: Buffer.alloc(2 * 1024 * 1024 + 1) });
    await page.getByText('报告不能超过 2 MB', { exact: true }).waitFor();
    checks.push('oversized_report_rejected');
    await upload(report);
    await page.getByText('报告结果：全部通过', { exact: true }).waitFor();
    await page.getByRole('button', { name: '清除报告', exact: true }).click();
    await page.getByText('尚未导入报告。不会显示预设成绩。', { exact: true }).waitFor();
    await upload(report);
    await page.getByText('报告结果：全部通过', { exact: true }).waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)) throw new Error('Mobile page overflow');
    await page.screenshot({ path: path.join(output, 'mobile.png'), fullPage: true });
    checks.push('clear_report_and_mobile_render');
    if (errors.length || writes.length) throw new Error(JSON.stringify({ errors, writes }));
    checks.push('no_page_errors_or_business_writes');
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ passed: true, checks, finished_at: new Date().toISOString(), environment: { node: process.version, browser: context.browser()?.version() }, report: path.resolve(reportPath), errors, writes }, null, 2));
    console.log(JSON.stringify({ passed: true, checks }));
  } catch (error) {
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ passed: false, checks, error: error.message, errors, writes }, null, 2));
    throw error;
  } finally { await context.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
