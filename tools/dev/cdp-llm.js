'use strict';
/* 新 LLM 设置的 UI 冒烟：设置卡 + 进程 AI 确认 + mock 结果。 */
const { spawn } = require('child_process');
const fs = require('fs');
const http = require('http');

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const URL = process.env.AG_URL;
const PORT = Number(process.env.AG_PORT || 9351);
const PROFILE = 'E:\\小工具\\app_guard\\.llm-dev\\chrome2';
const OUT = 'E:\\小工具\\app_guard\\.llm-dev';
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
  if (!URL) throw new Error('AG_URL required');
  fs.mkdirSync(PROFILE, { recursive: true });
  const child = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=' + PORT, '--user-data-dir=' + PROFILE,
    '--window-size=1600,1000', '--hide-scrollbars', 'about:blank'
  ], { stdio: 'ignore' });
  const errors = [];
  const results = [];
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
        errors.push((d.exception && d.exception.description) || d.text || 'exception');
      }
      if (msg.method === 'Log.entryAdded' && msg.params.entry.level === 'error') {
        errors.push(msg.params.entry.text);
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
    const shot = async (name) => {
      const r = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
      fs.writeFileSync(OUT + '\\' + name, Buffer.from(r.data, 'base64'));
    };
    const check = (name, ok, detail) => results.push({ name, ok: !!ok, detail: detail || '' });

    await send('Page.enable');
    await send('Runtime.enable');
    await send('Log.enable');
    await send('Page.navigate', { url: URL });
    await sleep(7000);

    await ev("document.querySelector('[data-screen=\"settings\"]').click()");
    await sleep(3000);
    check('设置页有 AI 卡片', (await ev("document.body.innerText")).includes('AI 进程解释'), '');
    check('AI Base URL 回显', (await ev("(document.querySelector('#llm-base')||{}).value || ''")).includes('127.0.0.1'), await ev("(document.querySelector('#llm-base')||{}).value || ''"));
    check('AI 模型回显', (await ev("(document.querySelector('#llm-model')||{}).value || ''")) === 'mock-llm', await ev("(document.querySelector('#llm-model')||{}).value || ''"));
    check('有测试连接按钮', await ev("!!document.querySelector('[data-act=\"llm-test\"]')"), '');
    await shot('llm-settings.png');

    await ev("document.querySelector('[data-screen=\"processes\"]').click()");
    await sleep(3000);
    const picked = await ev(`(function(){
      var el = document.querySelector('[data-act="proc-pick"]');
      if (!el) return false;
      el.click();
      return true;
    })()`);
    check('进程页能选中一行', picked, '');
    await sleep(1200);
    check('进程详情有 AI 解释按钮', await ev("!!document.querySelector('[data-act=\"ai-process\"]')"), '');
    await ev("document.querySelector('[data-act=\"ai-process\"]').click()");
    await sleep(2500);
    const consentText = await ev("(document.querySelector('.modal')||{}).innerText || ''");
    check('AI 确认弹窗出现', consentText.includes('允许并解释'), consentText.slice(0, 180));
    check('确认里有文件选择器', await ev("!!document.querySelector('#ai-file-input')"), '');
    check('确认里有模型名', consentText.includes('mock-llm'), '');
    await send('DOM.enable');
    const doc = await send('DOM.getDocument', { depth: -1 });
    const inputNode = await send('DOM.querySelector', {
      nodeId: doc.root.nodeId, selector: '#ai-file-input'
    });
    await send('DOM.setFileInputFiles', {
      nodeId: inputNode.nodeId,
      files: ['E:\\小工具\\app_guard\\.llm-dev\\sample.ini']
    });
    await sleep(1200);
    const fileText = await ev("(document.querySelector('#ai-file-list')||{}).innerText || ''");
    check('文件选择器读到 sample.ini', fileText.includes('sample.ini'), fileText);
    await shot('llm-consent.png');

    await ev("document.querySelector('[data-act=\"ai-run\"]').click()");
    await sleep(4000);
    const resultText = await ev("(document.querySelector('.modal')||{}).innerText || ''");
    check('AI 结果弹窗出现', resultText.includes('mock 解释'), resultText.slice(0, 180));
    check('AI 结果带免责声明', resultText.includes('不会自动执行'), '');
    try {
      const audit = fs.readFileSync('E:\\小工具\\app_guard\\.llm-dev\\local\\llm-audit.jsonl', 'utf8');
      check('审计记录了文件名', audit.includes('sample.ini'), '');
      check('审计不含文件内容', !audit.includes('SUPERSECRET'), '');
    } catch (err) {
      check('审计文件可读', false, String(err));
    }
    await shot('llm-result.png');

    console.log('CHECKS=' + JSON.stringify(results));
    console.log('ERRORS=' + JSON.stringify(errors.slice(-20)));
    ws.close();
    if (results.some((r) => !r.ok)) process.exitCode = 1;
  } catch (err) {
    console.error('LLM_UI_FAIL ' + (err && err.stack ? err.stack : err));
    process.exitCode = 1;
  } finally {
    try { child.kill(); } catch (e) {}
    await sleep(300);
  }
})();
