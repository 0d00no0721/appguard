'use strict';
/* 权限引导与按钮文案冒烟。 */
const { spawn } = require('child_process');
const fs = require('fs');
const http = require('http');

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const URL = process.env.AG_URL;
const PORT = Number(process.env.AG_PORT || 9345);
const PROFILE = 'E:\\小工具\\app_guard\\.appguard-dev\\chrome-help';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function getJson(path) {
  return new Promise((resolve, reject) => {
    const req = http.get({ host: '127.0.0.1', port: PORT, path }, (res) => {
      let data = '';
      res.on('data', (c) => { data += c; });
      res.on('end', () => { try { resolve(JSON.parse(data)); } catch (e) { reject(e); } });
    });
    req.on('error', reject);
  });
}
async function waitChrome() {
  const start = Date.now();
  while (Date.now() - start < 20000) {
    try { const list = await getJson('/json/list'); if (list.length) return list; } catch (e) {}
    await sleep(250);
  }
  throw new Error('chrome not ready');
}

(async () => {
  fs.mkdirSync(PROFILE, { recursive: true });
  const child = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=' + PORT, '--user-data-dir=' + PROFILE,
    '--window-size=1600,1000', '--hide-scrollbars', 'about:blank'
  ], { stdio: 'ignore' });
  const checks = [];
  const errors = [];
  try {
    const list = await waitChrome();
    const page = list.find((x) => x.type === 'page') || list[0];
    const ws = new WebSocket(page.webSocketDebuggerUrl);
    const pending = new Map();
    let seq = 0;
    await new Promise((resolve, reject) => {
      ws.addEventListener('open', resolve);
      ws.addEventListener('error', reject);
    });
    ws.addEventListener('message', (e) => {
      let msg; try { msg = JSON.parse(e.data); } catch (err) { return; }
      if (msg.id && pending.has(msg.id)) {
        const p = pending.get(msg.id); pending.delete(msg.id);
        if (msg.error) p.reject(new Error(msg.error.message)); else p.resolve(msg.result);
        return;
      }
      if (msg.method === 'Runtime.exceptionThrown') {
        const d = msg.params.exceptionDetails || {};
        errors.push((d.exception && d.exception.description) || d.text);
      }
    });
    const send = (method, params) => new Promise((resolve, reject) => {
      const id = ++seq; pending.set(id, { resolve, reject });
      ws.send(JSON.stringify({ id, method, params: params || {} }));
    });
    const ev = async (expr) => {
      const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
      if (r && r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
      return r && r.result ? r.result.value : null;
    };
    const check = (name, ok, detail) => checks.push({ name, ok: !!ok, detail: detail || '' });

    await send('Page.enable');
    await send('Runtime.enable');
    await send('Page.navigate', { url: URL });
    await sleep(6000);

    check('顶部显示只读模式', (await ev("document.querySelector('#banner').innerText")).indexOf('只读模式') >= 0,
      await ev("document.querySelector('#banner').innerText"));
    check('管理员帮助按钮存在', await ev("!!document.querySelector('[data-act=\"show-admin-help\"]')"), '');
    await ev("document.querySelector('[data-act=\"show-admin-help\"]').click()");
    await sleep(500);
    check('帮助弹窗打开', await ev("!!document.querySelector('.modal')"), '');
    const modal = await ev("(document.querySelector('.modal')||{}).innerText||''");
    check('帮助弹窗含一键脚本', modal.indexOf('AppGuard-admin.bat') >= 0, modal.slice(0, 120));
    await ev("document.querySelector('[data-act=\"modal-close\"]').click()");

    await ev("document.querySelector('[data-screen=\"settings\"]').click()");
    await sleep(2500);
    check('设置页有管理员帮助按钮', await ev("!!document.querySelector('[data-act=\"show-admin-help\"]')"), '');
    check('设置页显示实盘不可用', (await ev("document.querySelector('#screen').innerText")).indexOf('实盘可用') >= 0, '');
    await ev("document.querySelector('[data-act=\"toggle-dryrun\"]').click()");
    await sleep(600);
    const help = await ev("!!document.querySelector('.modal') && (document.querySelector('.modal').innerText||'').indexOf('获得管理员权限')>=0");
    check('非管理员切换演练会弹帮助而不是直接关闭', help, '');
    await ev("document.querySelector('[data-act=\"modal-close\"]').click()");

    await ev("document.querySelector('[data-screen=\"cleanup\"]').click()");
    await sleep(2200);
    const cleanup = await ev("document.querySelector('#screen').innerText");
    check('清理页显示只读模式', cleanup.indexOf('只读模式') >= 0, cleanup.slice(0, 100));
    check('执行按钮显示演练执行', cleanup.indexOf('演练执行') >= 0, cleanup.slice(-160));

    console.log('CHECKS=' + JSON.stringify(checks));
    console.log('ERRORS=' + JSON.stringify(errors));
    ws.close();
    if (checks.some((c) => !c.ok)) process.exitCode = 1;
  } catch (err) {
    console.error('HELP_FAIL ' + (err && err.stack ? err.stack : err));
    process.exitCode = 1;
  } finally {
    try { child.kill(); } catch (e) {}
    await sleep(300);
  }
})();
