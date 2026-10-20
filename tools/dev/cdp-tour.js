'use strict';
/* 开发期冒烟：六屏 + 软件详情 + 清理演练 + 审计详情，逐屏截图与错误收集。 */
const { spawn } = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');

const CHROME = process.env.AG_CHROME || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const TARGET_URL = process.env.AG_URL || 'http://127.0.0.1:8799/#token=devtoken';
const OUTDIR = process.env.AG_OUTDIR || 'E:\\小工具\\app_guard\\.appguard-dev\\shots';
const PROFILE = process.env.AG_PROFILE || 'E:\\小工具\\app_guard\\.appguard-dev\\chrome-tour';
const PORT = Number(process.env.AG_PORT || 9334);

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
  fs.mkdirSync(OUTDIR, { recursive: true });
  fs.mkdirSync(PROFILE, { recursive: true });
  const child = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=' + PORT, '--user-data-dir=' + PROFILE,
    '--window-size=1600,1000', '--hide-scrollbars', '--force-device-scale-factor=1',
    'about:blank'
  ], { stdio: 'ignore' });

  const errors = [];
  const consoleLogs = [];
  const results = [];
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
        const detail = msg.params.exceptionDetails || {};
        errors.push((detail.exception && detail.exception.description) || detail.text || 'exception');
      } else if (msg.method === 'Runtime.consoleAPICalled') {
        const args = (msg.params.args || []).map((arg) => arg.value !== undefined ? arg.value : arg.description).join(' ');
        if (msg.params.type === 'error') errors.push('console: ' + args);
        consoleLogs.push(msg.params.type + ': ' + args);
      } else if (msg.method === 'Log.entryAdded') {
        const entry = msg.params.entry || {};
        if (entry.level === 'error') errors.push(entry.text);
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
      if (res && res.exceptionDetails) throw new Error('evaluate failed: ' + JSON.stringify(res.exceptionDetails));
      return res && res.result ? res.result.value : null;
    };
    const shot = async (name) => {
      const data = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
      const file = path.join(OUTDIR, name + '.png');
      fs.writeFileSync(file, Buffer.from(data.data, 'base64'));
      const info = await evaluate("JSON.stringify({title:document.title,screen:(document.querySelector('#screen')||{}).innerText.slice(0,220),modal:!!document.querySelector('.modal'),waves:window.__agErrors||[]})");
      results.push({ name: name, file: file, info: info });
    };

    await send('Page.enable');
    await send('Runtime.enable');
    await send('Log.enable');
    await send('Page.addScriptToEvaluateOnNewDocument', {
      source: "window.__agErrors=[];window.addEventListener('error',function(e){window.__agErrors.push(String(e.message)+' @'+e.filename+':'+e.lineno);});window.addEventListener('unhandledrejection',function(e){window.__agErrors.push('rejection: '+String((e.reason&&e.reason.message)||e.reason));});"
    });
    await send('Page.navigate', { url: TARGET_URL });
    await sleep(7000);
    await shot('1-overview');

    await evaluate("document.querySelector('[data-screen=\"software\"]').click()");
    await sleep(1400);
    await shot('2-software');
    await evaluate("document.querySelector('.sw-card') && document.querySelector('.sw-card').click()");
    await sleep(1200);
    await shot('2b-software-modal');
    await evaluate("document.querySelector('[data-act=\"modal-close\"]') && document.querySelector('[data-act=\"modal-close\"]').click()");

    await evaluate("document.querySelector('[data-screen=\"processes\"]').click()");
    await sleep(2200);
    await shot('3-processes');
    await evaluate("document.querySelector('#proc-body tr[data-pid]') && document.querySelector('#proc-body tr[data-pid]').click()");
    await sleep(500);
    await shot('3b-process-explain');

    await evaluate("document.querySelector('[data-screen=\"cleanup\"]').click()");
    await sleep(2400);
    await shot('4-cleanup');
    await evaluate("document.querySelector('[data-act=\"sel-clear\"]') && document.querySelector('[data-act=\"sel-clear\"]').click()");
    await evaluate("document.querySelector('[data-act=\"sel-default\"]') && document.querySelector('[data-act=\"sel-default\"]').click()");
    await evaluate("document.querySelector('[data-act=\"execute\"]') && document.querySelector('[data-act=\"execute\"]').click()");
    await sleep(700);
    await shot('4b-cleanup-confirm');
    const confirm = await evaluate("!!document.querySelector('[data-act=\"modal-confirm\"]')");
    if (confirm) {
      await evaluate("document.querySelector('[data-act=\"modal-confirm\"]').click()");
      await sleep(3000);
      await shot('4c-cleanup-result');
      await evaluate("document.querySelector('[data-act=\"result-done\"]') && document.querySelector('[data-act=\"result-done\"]').click()");
      await sleep(1600);
      await shot('4d-cleanup-fresh');
    }

    await evaluate("document.querySelector('[data-screen=\"audit\"]').click()");
    await sleep(1600);
    await shot('5-audit');
    await evaluate("document.querySelector('#audit-body tr[data-seq]') && document.querySelector('#audit-body tr[data-seq]').click()");
    await sleep(600);
    await shot('5b-audit-detail');
    await evaluate("document.querySelector('[data-act=\"modal-close\"]') && document.querySelector('[data-act=\"modal-close\"]').click()");

    await evaluate("document.querySelector('[data-screen=\"settings\"]').click()");
    await sleep(2200);
    await shot('6-settings');

    const runtimeErrors = await evaluate('JSON.stringify(window.__agErrors||[])');
    console.log('RESULTS=' + JSON.stringify(results));
    console.log('RUNTIME_ERRORS=' + runtimeErrors);
    console.log('ERRORS=' + JSON.stringify(errors.slice(-30)));
    ws.close();
  } catch (err) {
    console.error('TOUR_FAIL ' + (err && err.stack ? err.stack : err));
    process.exitCode = 1;
  } finally {
    try { child.kill(); } catch (err) { /* ignore */ }
    await sleep(400);
  }
})();
