'use strict';
/* 无感刷新验证：空闲/表格/搜索焦点/审计下的 DOM 变更与动画计数。 */
const { spawn } = require('child_process');
const fs = require('fs');
const http = require('http');

const CHROME = process.env.AG_CHROME || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const TARGET_URL = process.env.AG_URL;
const PROFILE = process.env.AG_PROFILE || 'E:\\小工具\\app_guard\\.appguard-dev\\chrome-noflicker';
const PORT = Number(process.env.AG_PORT || 9342);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function getJson(pathname) {
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

async function waitForChrome() {
  const started = Date.now();
  while (Date.now() - started < 20000) {
    try {
      const list = await getJson('/json/list');
      if (Array.isArray(list) && list.length) return list;
    } catch (err) { /* not ready */ }
    await sleep(250);
  }
  throw new Error('Chrome remote debugging not ready');
}

(async () => {
  if (!TARGET_URL) throw new Error('AG_URL is required');
  fs.mkdirSync(PROFILE, { recursive: true });
  const child = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=' + PORT, '--user-data-dir=' + PROFILE,
    '--window-size=1600,1000', '--hide-scrollbars', '--force-device-scale-factor=1',
    'about:blank'
  ], { stdio: 'ignore' });

  const results = [];
  const errors = [];
  try {
    const list = await waitForChrome();
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
      if (msg.method === 'Log.entryAdded' && msg.params.entry.level === 'error') {
        errors.push(msg.params.entry.text);
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
      if (res && res.exceptionDetails) throw new Error(JSON.stringify(res.exceptionDetails));
      return res && res.result ? res.result.value : null;
    };

    await send('Page.enable');
    await send('Runtime.enable');
    await send('Log.enable');
    await send('Page.navigate', { url: TARGET_URL });
    await sleep(6500);

    const installObservers = async () => {
      await evaluate(`(function(){
        window.__obs = { screenInner: 0, screenNodes: 0, badge: 0, anim: 0 };
        try { window.__screenObs && window.__screenObs.disconnect(); } catch (e) {}
        try { window.__badgeObs && window.__badgeObs.disconnect(); } catch (e) {}
        const screen = document.querySelector('#screen');
        const top = document.querySelector('#top-right');
        window.__screenObs = new MutationObserver(function(records){
          records.forEach(function(r){
            window.__obs.screenNodes += r.addedNodes.length + r.removedNodes.length;
            Array.prototype.slice.call(r.addedNodes).concat(Array.prototype.slice.call(r.removedNodes)).forEach(function(n){
              if (n && n.nodeType === 1 && (n.classList.contains('screen-inner') || (n.querySelector && n.querySelector('.screen-inner')))) {
                window.__obs.screenInner += 1;
              }
            });
          });
        });
        window.__screenObs.observe(screen, { childList: true, subtree: true });
        window.__badgeObs = new MutationObserver(function(records){ window.__obs.badge += records.length; });
        window.__badgeObs.observe(top, { childList: true, subtree: true });
        document.addEventListener('animationstart', function(e){
          if (e.animationName === 'rise') window.__obs.anim += 1;
        }, true);
      })()`);
    };
    const report = async (label, extra) => {
      const raw = await evaluate("JSON.stringify(Object.assign({obs:window.__obs||null,scroll:(document.querySelector('#screen')||{}).scrollTop||0,active:(document.activeElement&&document.activeElement.id)||'',activeTag:(document.activeElement&&document.activeElement.tagName)||'',q:(document.querySelector('#proc-q')||{}).value||'',screenInner:!!document.querySelector('#screen .screen-inner'),badges:(document.querySelector('#top-right')||{}).childElementCount||0}, " + JSON.stringify(extra || {}) + "))");
      results.push({ label: label, data: JSON.parse(raw) });
    };
    const nav = async (screen) => {
      await evaluate("document.querySelector('[data-screen=\"" + screen + "\"]').click()");
      await sleep(3500);
    };

    await installObservers();
    await sleep(18000);
    await report('overview-idle');

    await nav('processes');
    await installObservers();
    await evaluate("document.querySelector('#screen').scrollTop = 420");
    await sleep(17000);
    await report('processes-idle');

    await evaluate("document.querySelector('#proc-q').focus(); var q=document.querySelector('#proc-q'); q.value='QQ'; q.dispatchEvent(new Event('input',{bubbles:true}))");
    await sleep(14000);
    await report('processes-input-focused');

    await evaluate("document.querySelector('#proc-q').value=''; document.querySelector('#proc-q').dispatchEvent(new Event('input',{bubbles:true})); document.querySelector('#proc-q').blur()");
    await nav('software');
    await installObservers();
    await sleep(17000);
    await report('software-idle');

    await nav('audit');
    await installObservers();
    await sleep(14000);
    await report('audit-idle');

    await nav('cleanup');
    await installObservers();
    await sleep(14000);
    await report('cleanup-idle');

    await nav('settings');
    await installObservers();
    await sleep(14000);
    await report('settings-idle');

    console.log('NOFLICKER=' + JSON.stringify(results));
    console.log('ERRORS=' + JSON.stringify(errors.slice(-20)));
    ws.close();
  } catch (err) {
    console.error('NOFLICKER_FAIL ' + (err && err.stack ? err.stack : err));
    process.exitCode = 1;
  } finally {
    try { child.kill(); } catch (err) { /* ignore */ }
    await sleep(400);
  }
})();
