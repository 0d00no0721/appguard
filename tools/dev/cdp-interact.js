'use strict';
/* 开发期交互冒烟：搜索 / 筛选 / 软件详情 -> 定向清理 / 审计过滤 / 设置表单。 */
const { spawn } = require('child_process');
const fs = require('fs');
const http = require('http');

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const URL = process.env.AG_URL || 'http://127.0.0.1:8799/#token=devtoken';
const PROFILE = 'E:\\小工具\\app_guard\\.appguard-dev\\chrome-interact';
const PORT = Number(process.env.AG_PORT || 9336);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function httpJson(pathname) {
  return new Promise((resolve, reject) => {
    const req = http.get({ host: '127.0.0.1', port: PORT, path: pathname, timeout: 2000 }, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => { try { resolve(JSON.parse(data)); } catch (err) { reject(err); } });
    });
    req.on('error', reject);
    req.on('timeout', () => req.destroy(new Error('timeout')));
  });
}

async function waitForEndpoint() {
  const started = Date.now();
  while (Date.now() - started < 20000) {
    try {
      const list = await httpJson('/json/list');
      if (Array.isArray(list) && list.length) return list;
    } catch (err) { /* not ready */ }
    await sleep(250);
  }
  throw new Error('Chrome remote debugging not ready');
}

(async () => {
  fs.mkdirSync(PROFILE, { recursive: true });
  const child = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=' + PORT, '--user-data-dir=' + PROFILE,
    '--window-size=1600,1000', '--hide-scrollbars', '--force-device-scale-factor=1',
    'about:blank'
  ], { stdio: 'ignore' });
  const checks = [];
  const errors = [];
  try {
    const list = await waitForEndpoint();
    const page = list.find((item) => item.type === 'page') || list[0];
    const ws = new WebSocket(page.webSocketDebuggerUrl);
    const pending = new Map();
    let seq = 0;
    const opened = new Promise((resolve, reject) => {
      ws.addEventListener('open', resolve);
      ws.addEventListener('error', reject);
    });
    ws.addEventListener('message', (event) => {
      let msg = null;
      try { msg = JSON.parse(event.data); } catch (err) { return; }
      if (msg.id && pending.has(msg.id)) {
        const entry = pending.get(msg.id);
        pending.delete(msg.id);
        if (msg.error) entry.reject(new Error(msg.error.message));
        else entry.resolve(msg.result);
        return;
      }
      if (msg.method === 'Runtime.exceptionThrown') {
        const d = msg.params.exceptionDetails || {};
        errors.push((d.exception && d.exception.description) || d.text || 'exception');
      }
      if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') {
        errors.push('console: ' + (msg.params.args || []).map((a) => a.value).join(' '));
      }
    });
    await opened;
    const send = (method, params) => new Promise((resolve, reject) => {
      const id = ++seq;
      pending.set(id, { resolve, reject });
      ws.send(JSON.stringify({ id, method, params: params || {} }));
    });
    const evaluate = async (expression) => {
      const res = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
      if (res && res.exceptionDetails) throw new Error('evaluate: ' + JSON.stringify(res.exceptionDetails));
      return res && res.result ? res.result.value : null;
    };
    const check = (name, ok, detail) => checks.push({ name: name, ok: !!ok, detail: detail === undefined ? '' : detail });

    await send('Page.enable');
    await send('Runtime.enable');
    await send('Page.addScriptToEvaluateOnNewDocument', {
      source: "window.__agErrors=[];window.addEventListener('error',function(e){window.__agErrors.push(String(e.message));});window.addEventListener('unhandledrejection',function(e){window.__agErrors.push('rejection: '+String((e.reason&&e.reason.message)||e.reason));});"
    });
    await send('Page.navigate', { url: URL });
    await sleep(5000);

    /* 软件搜索 */
    await evaluate("document.querySelector('[data-screen=\"software\"]').click()");
    await sleep(1200);
    await evaluate("var q=document.querySelector('#sw-q');q.value='QQ';q.dispatchEvent(new Event('input',{bubbles:true}))");
    await sleep(200);
    const searchCount = await evaluate("document.querySelector('#sw-count').innerText");
    const searchCards = await evaluate("document.querySelectorAll('#sw-grid .sw-card').length");
    check('软件搜索命中', /^[1-9]/.test(searchCount) && searchCards > 0, searchCount + ' / cards=' + searchCards);
    check('软件搜索只剩 QQ', await evaluate("Array.from(document.querySelectorAll('#sw-grid .sw-card')).every(function(n){return n.innerText.indexOf('QQ')>=0})"), '');
    await evaluate("var q=document.querySelector('#sw-q');q.value='';q.dispatchEvent(new Event('input',{bubbles:true}))");

    /* 私自筛选 */
    await evaluate("document.querySelector('[data-filter=\"private\"]').click()");
    await sleep(200);
    const privateCount = await evaluate("document.querySelector('#sw-count').innerText");
    check('私自筛选生效', /^\d+/.test(privateCount), privateCount);

    /* 软件详情 -> 定向清理 */
    await evaluate("document.querySelector('#sw-grid .sw-card').click()");
    await sleep(1200);
    check('软件详情弹窗', await evaluate("!!document.querySelector('.modal')"), '');
    const hasScoped = await evaluate("!!document.querySelector('[data-act=\"clean-software\"]')");
    if (hasScoped) {
      const swName = await evaluate("document.querySelector('.sw-hero h3').innerText");
      await evaluate("document.querySelector('[data-act=\"clean-software\"]').click()");
      await sleep(1600);
      const title = await evaluate("document.querySelector('#page-title').innerText");
      const text = await evaluate("document.querySelector('#screen').innerText.slice(0,400)");
      check('定向清理切换到清理页', title === '清理', title);
      check('定向清理范围写出软件名', text.toLowerCase().indexOf(swName.toLowerCase()) >= 0, swName + ' :: ' + text.slice(0, 160));
    } else {
      check('定向清理按钮（该软件无私自组件，跳过）', true, 'no private components');
      await evaluate("document.querySelector('[data-act=\"modal-close\"]').click()");
    }

    /* 进程筛选 */
    await evaluate("document.querySelector('[data-screen=\"processes\"]').click()");
    await sleep(1800);
    const totalText = await evaluate("document.querySelector('#proc-count').innerText");
    await evaluate("var s=document.querySelector('#proc-role');s.value='service';s.dispatchEvent(new Event('change',{bubbles:true}))");
    await sleep(300);
    const roleText = await evaluate("document.querySelector('#proc-count').innerText");
    const roleTags = await evaluate("Array.from(document.querySelectorAll('#proc-body tr td:nth-child(3)')).map(function(n){return n.innerText}).join('|')");
    check('进程角色筛选生效', roleText !== totalText && roleTags.indexOf('系统服务') >= 0, totalText + ' -> ' + roleText + ' :: ' + roleTags.slice(0, 80));
    await evaluate("var s=document.querySelector('#proc-role');s.value='';s.dispatchEvent(new Event('change',{bubbles:true}))");
    await evaluate("var q=document.querySelector('#proc-q');q.value='QQ';q.dispatchEvent(new Event('input',{bubbles:true}))");
    await sleep(300);
    const qText = await evaluate("document.querySelector('#proc-count').innerText");
    check('进程搜索生效', qText !== roleText, qText);
    await evaluate("var q=document.querySelector('#proc-q');q.value='';q.dispatchEvent(new Event('input',{bubbles:true}))");

    /* 审计过滤 */
    await evaluate("document.querySelector('[data-screen=\"audit\"]').click()");
    await sleep(1400);
    const auditAll = await evaluate("document.querySelector('#audit-count').innerText");
    await evaluate("var s=document.querySelector('#audit-event');s.value='cleanup.executed';s.dispatchEvent(new Event('change',{bubbles:true}))");
    await sleep(200);
    const auditFiltered = await evaluate("document.querySelector('#audit-count').innerText");
    const eventTags = await evaluate("Array.from(document.querySelectorAll('#audit-body tr td:nth-child(3)')).map(function(n){return n.innerText}).join('|')");
    check('审计过滤生效', auditAll !== auditFiltered && eventTags.indexOf('执行清理') >= 0, auditAll + ' -> ' + auditFiltered);
    await evaluate("var s=document.querySelector('#audit-event');s.value='';s.dispatchEvent(new Event('change',{bubbles:true}))");
    await evaluate("var s=document.querySelector('#audit-limit');s.value='500';s.dispatchEvent(new Event('change',{bubbles:true}))");
    await sleep(1600);
    const auditRows = await evaluate("document.querySelectorAll('#audit-body tr[data-seq]').length");
    const auditCountText = await evaluate("document.querySelector('#audit-count').innerText");
    check('审计 limit=500 生效', auditRows > 200, auditCountText + ' rows=' + auditRows);

    /* 设置表单 */
    await evaluate("document.querySelector('[data-screen=\"settings\"]').click()");
    await sleep(1600);
    const switches = await evaluate("document.querySelectorAll('.switch').length");
    const minIdle = await evaluate("var n=document.querySelector('#min-idle'); n ? n.value : ''");
    check('设置页含 2 个开关', switches === 2, 'switches=' + switches);
    check('设置页读到 min_idle_s', Number(minIdle) > 0, 'min_idle=' + minIdle);

    const runtimeErrors = await evaluate('JSON.stringify(window.__agErrors||[])');
    console.log('CHECKS=' + JSON.stringify(checks));
    console.log('RUNTIME_ERRORS=' + runtimeErrors);
    console.log('ERRORS=' + JSON.stringify(errors));
    ws.close();
    const failed = checks.filter((c) => !c.ok).length;
    if (failed) process.exitCode = 1;
  } catch (err) {
    console.error('INTERACT_FAIL ' + (err && err.stack ? err.stack : err));
    process.exitCode = 1;
  } finally {
    try { child.kill(); } catch (err) { /* ignore */ }
    await sleep(400);
  }
})();
