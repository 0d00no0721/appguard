'use strict';
/* 开发期冒烟：用 Chrome DevTools Protocol 打开 AppGuard 页面，抓控制台错误 + 截图。
   不依赖 --dump-dom / --screenshot（SSE 长连接会拖住 load 判定）。 */
const { spawn } = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');

const CHROME = process.env.AG_CHROME || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const TARGET_URL = process.env.AG_URL || 'http://127.0.0.1:8799/#token=devtoken';
const OUT = process.env.AG_OUT || 'E:\\小工具\\app_guard\\.appguard-dev\\shot-overview.png';
const PROFILE = process.env.AG_PROFILE || 'E:\\小工具\\app_guard\\.appguard-dev\\chrome-cdp';
const PORT = Number(process.env.AG_PORT || 9333);
const WAIT = Number(process.env.AG_WAIT || 6000);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function httpJson(pathname) {
  return new Promise((resolve, reject) => {
    const req = http.get({ host: '127.0.0.1', port: PORT, path: pathname, timeout: 2000 }, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        try { resolve(JSON.parse(data)); } catch (err) { reject(err); }
      });
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
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.mkdirSync(PROFILE, { recursive: true });
  const child = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=' + PORT, '--user-data-dir=' + PROFILE,
    '--window-size=1600,1000', '--hide-scrollbars', '--force-device-scale-factor=1',
    'about:blank'
  ], { stdio: 'ignore' });

  const errors = [];
  const consoleLogs = [];
  try {
    const list = await waitForEndpoint();
    const page = list.find((item) => item.type === 'page') || list[0];
    if (!page || !page.webSocketDebuggerUrl) throw new Error('no debuggable page');
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

    await send('Page.enable');
    await send('Runtime.enable');
    await send('Log.enable');
    await send('Page.addScriptToEvaluateOnNewDocument', {
      source: "window.__agErrors=[];window.addEventListener('error',function(e){window.__agErrors.push(String(e.message)+' @'+e.filename+':'+e.lineno);});window.addEventListener('unhandledrejection',function(e){window.__agErrors.push('rejection: '+String((e.reason&&e.reason.message)||e.reason));});"
    });
    await send('Page.navigate', { url: TARGET_URL });
    await sleep(WAIT);

    const evaluate = async (expression) => {
      const res = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
      return res && res.result ? res.result.value : null;
    };
    const info = await evaluate("JSON.stringify({href:location.href,title:document.title,nav:document.querySelectorAll('.nav-item').length,screen:(document.querySelector('#screen')||{}).innerText,errors:window.__agErrors||[],status:(document.querySelector('#status-left')||{}).innerText,badges:(document.querySelector('#top-right')||{}).innerText})");
    const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    fs.writeFileSync(OUT, Buffer.from(shot.data, 'base64'));
    console.log('PAGE_INFO=' + info);
    console.log('CONSOLE=' + JSON.stringify(consoleLogs.slice(-30)));
    console.log('ERRORS=' + JSON.stringify(errors.slice(-20)));
    console.log('SHOT=' + OUT);
    ws.close();
  } catch (err) {
    console.error('CDP_FAIL ' + (err && err.stack ? err.stack : err));
    process.exitCode = 1;
  } finally {
    try { child.kill(); } catch (err) { /* ignore */ }
    await sleep(400);
  }
})();
