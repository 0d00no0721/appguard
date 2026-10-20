/* ==========================================================================
   AppGuard · 本地进程账本（浏览器前端）
   - 零依赖、零构建：原生 JS + fetch + EventSource
   - 只通过同源 HTTP/SSE 调本地网关；不接触特权 API
   - 数据口径全部来自守护：UI 只展示，不重算判定
   ========================================================================== */
(() => {
  'use strict';

  const TOKEN_KEY = 'appguard.token.v1';
  const VERSION = 'v0.3.0';
  const POLL_MS = 10000;

  /* ------------------------------------------------------------------ *
   * 枚举中文表（与 src/appguard/models.py 保持一致）
   * ------------------------------------------------------------------ */
  const ROLE_ZH = {
    main: '主程序', launcher: '启动器', updater: '更新器', telemetry: '遥测上报',
    crash: '崩溃收集', ad: '推广/推送', tray: '托盘常驻', helper: '辅助进程',
    daemon: '后台常驻', service: '系统服务', installer: '安装/卸载',
    system: '系统进程', unknown: '未知'
  };
  const ACTIVITY_ZH = {
    foreground: '前台使用中', visible: '有可见窗口', tray: '仅托盘',
    background: '后台运行', service_session: '服务会话'
  };
  const ORIGIN_ZH = {
    user: '用户启动', child_of_user: '用户程序子进程', autostart: '开机自启',
    service: '服务拉起', child_of_service: '服务子进程', unknown: '来源未知'
  };
  const RISK_ZH = { safe: '可安全结束', caution: '需确认', protected: '受保护' };
  const KIND_ZH = { app: '应用', game: '游戏', runtime: '运行时', driver: '驱动', system: '系统', unknown: '未知' };
  const EVENT_ZH = {
    'daemon.start': '守护启动', 'daemon.stop': '守护停止',
    'daemon.pipe_ready': '管道就绪', 'daemon.pipe_error': '管道失败',
    'daemon.tcp_ready': 'TCP 就绪', 'daemon.reload': '重载配置',
    'cleanup.planned': '生成计划', 'cleanup.executed': '执行清理',
    'cleanup.start': '开始清理', 'cleanup.dry_run': '演练清理',
    'process.blocked': '结束进程', 'service.stopped': '停止服务',
    'policy.changed': '策略变更', 'policy.restored': '策略恢复',
    'kb.review': '知识库待办', 'kb.applied': '知识库入库', 'kb.reloaded': '知识库重载',
    'kb.rolled_back': '知识库回滚', 'kb.pending': '写入待办',
    'config.reloaded': '配置重载', 'collect.error': '采集异常', 'state.save_failed': '状态写入失败',
    'process.kill_failed': '结束失败', 'service.stop_failed': '停止失败',
    'kb.pending_failed': '待办写入失败', 'kb.load': '知识库加载',
    'plan.invalidated': '计划失效'
  };
  const ACTION_ZH = {
    plan: '生成计划', cleanup: '执行清理', kill: '结束进程', stop_service: '停止服务',
    review: '生成待办', apply: '入库', rollback: '回滚', start: '启动', stop: '停止',
    listen: '监听', record: '记录', restore: '恢复', export: '导出'
  };

  const TM_COLORS = {
    main: '#2e86c1', launcher: '#5bc0eb', updater: '#f5b942', telemetry: '#c9a227',
    crash: '#ff9f43', ad: '#ff6b81', tray: '#00acc1', helper: '#7e57c2',
    daemon: '#3949ab', service: '#6a5cff', installer: '#8d6e63',
    system: '#55555f', unknown: '#6b6b76'
  };
  const SW_PALETTE = ['#6a5cff', '#2e86c1', '#5bc0eb', '#8b5cf6', '#00acc1', '#7e57c2', '#3949ab', '#4dd0e1'];

  const NAV = [
    { id: 'overview', label: '总览' },
    { id: 'software', label: '软件' },
    { id: 'processes', label: '进程' },
    { id: 'cleanup', label: '清理' },
    { id: 'audit', label: '审计' },
    { id: 'settings', label: '设置' }
  ];

  /* 图标：全部用 path（便于 pathLength=100 的“描绘”动画），粗线、几何。 */
  const icon = (paths) => '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">' + paths + '</svg>';
  const ICONS = {
    overview: icon(
      '<path d="M3 3h18v18H3z" pathLength="100"/>' +
      '<path d="M3 12h18" pathLength="100"/><path d="M12 3v18" pathLength="100"/>'),
    software: icon(
      '<path d="M3 3h7v7H3z" pathLength="100"/><path d="M14 3h7v7h-7z" pathLength="100"/>' +
      '<path d="M3 14h7v7H3z" pathLength="100"/><path d="M14 14h7v7h-7z" pathLength="100"/>'),
    processes: icon(
      '<path d="M4 5h3v3H4z" pathLength="100"/><path d="M10 6.5h10" pathLength="100"/>' +
      '<path d="M4 11h3v3H4z" pathLength="100"/><path d="M10 12.5h10" pathLength="100"/>' +
      '<path d="M4 17h3v3H4z" pathLength="100"/><path d="M10 18.5h10" pathLength="100"/>'),
    cleanup: icon(
      '<path d="M5 5l14 14" pathLength="100"/><path d="M19 5L5 19" pathLength="100"/>' +
      '<path d="M3 8V3h5" pathLength="100"/><path d="M21 8V3h-5" pathLength="100"/>' +
      '<path d="M3 16v5h5" pathLength="100"/><path d="M21 16v5h-5" pathLength="100"/>'),
    audit: icon(
      '<path d="M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18z" pathLength="100"/>' +
      '<path d="M12 7v5l3.5 2" pathLength="100"/>'),
    settings: icon(
      '<path d="M3 7h18" pathLength="100"/><path d="M3 12h18" pathLength="100"/><path d="M3 17h18" pathLength="100"/>' +
      '<path d="M8 5h3v4H8z" pathLength="100"/><path d="M14 10h3v4h-3z" pathLength="100"/>' +
      '<path d="M6 15h3v4H6z" pathLength="100"/>')
  };

  const SCREENS = {
    overview: { title: '总览', kicker: 'PROCESS LEDGER / 01', load: loadOverview },
    software: { title: '软件', kicker: 'SOFTWARE LEDGER / 02', load: loadSoftware },
    processes: { title: '进程', kicker: 'CLASSIFY / 03', load: loadProcesses },
    cleanup: { title: '清理', kicker: 'SURGERY / 04', load: loadCleanup },
    audit: { title: '审计', kicker: 'AUDIT / 05', load: loadAudit },
    settings: { title: '设置', kicker: 'POLICY / 06', load: loadSettings }
  };

  const SSE_EVENTS = [
    'heartbeat', 'snapshot.updated', 'software.started', 'software.stopped',
    'process.started', 'process.exited', 'process.blocked', 'process.kill_failed',
    'cleanup.executed', 'plan.invalidated', 'policy.changed', 'notice',
    'service.stopped', 'service.stop_failed', 'kb.pending', 'kb.review',
    'kb.applied', 'kb.reloaded', 'kb.rolled_back', 'config.reloaded'
  ];

  /* ------------------------------------------------------------------ *
   * 全局状态
   * ------------------------------------------------------------------ */
  const S = {
    token: '', screen: 'overview', screenOpts: {}, ret: 0,
    status: null, connected: false, seq: 0, lastUpdate: 0,
    software: [], procs: [], procsTotal: 0,
    plan: null, planKey: '', planAt: 0,
    cleanup: { plan: null, planId: '', selected: new Set() },
    sw: { filter: 'all', q: '', sort: 'memory' },
    proc: { q: '', software: '', role: '', activity: '', origin: '', sort: 'memory', page: 1, selected: null },
    audit: { event: '', limit: 200, rows: [], detailJson: '' },
    auditForce: false,
    rules: null, kb: null, policy: null,
    modalOpen: false, confirmResolve: null, modalOnClose: null,
    toastTimer: 0, refreshTimer: 0, pollTimer: 0, tickTimer: 0,
    softTimer: 0, softSeq: 0, softLast: 0, chromeBuilt: false, bannerKey: '',
    swSig: '', procSig: '', auditTop: 0,
    eventSource: null, busy: false, errorSeen: false
  };

  /* ------------------------------------------------------------------ *
   * 小工具
   * ------------------------------------------------------------------ */
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  const ESC_MAP = {
    '&': '&' + 'amp;', '<': '&' + 'lt;', '>': '&' + 'gt;',
    '"': '&' + 'quot;', "'": '&#' + '39;'
  };

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ESC_MAP[ch]);
  }

  function setHTML(target, html) {
    const node = typeof target === 'string' ? $(target) : target;
    if (node && node.innerHTML !== html) node.innerHTML = html;
  }

  function setText(target, text) {
    const node = typeof target === 'string' ? $(target) : target;
    if (node && node.textContent !== String(text)) node.textContent = String(text);
  }

  function fmtMB(value) {
    const n = Number(value) || 0;
    if (n >= 1024) return (n / 1024).toFixed(1) + ' GB';
    if (n >= 10) return Math.round(n) + ' MB';
    return n.toFixed(1) + ' MB';
  }

  function fmtNum(value) {
    const n = Number(value) || 0;
    return n.toLocaleString('zh-CN');
  }

  function fmtClock(ts) {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return String(ts || '—');
    const p = (x) => String(x).padStart(2, '0');
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }

  function fmtDateTime(ts) {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return String(ts || '—');
    const p = (x) => String(x).padStart(2, '0');
    return p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }

  function fmtDuration(seconds) {
    const s = Math.max(0, Number(seconds) || 0);
    if (s < 60) return s + 's';
    const m = Math.floor(s / 60);
    if (m < 60) return m + 'm';
    const h = Math.floor(m / 60);
    return h + 'h ' + String(m % 60).padStart(2, '0') + 'm';
  }

  function fmtCountdown(seconds) {
    const s = Math.max(0, Number(seconds) || 0);
    return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
  }

  function pct(part, total) {
    const t = Number(total) || 0;
    if (t <= 0) return '0%';
    return Math.round((Number(part) || 0) / t * 100) + '%';
  }

  function swColor(id) {
    let h = 0;
    const text = String(id || '');
    for (let i = 0; i < text.length; i += 1) h = (h * 31 + text.charCodeAt(i)) >>> 0;
    return SW_PALETTE[h % SW_PALETTE.length];
  }

  function initials(name) {
    const text = String(name || '?').trim();
    return text ? text.slice(0, 1).toUpperCase() : '?';
  }

  function swName(id) {
    if (!id) return '—';
    const found = S.software.find((s) => s.id === id);
    return found ? found.name : id;
  }

  function planExpired(plan) {
    if (!plan || !plan.expires_at) return true;
    const at = Date.parse(plan.expires_at);
    return !at || at <= Date.now();
  }

  function isCurrent(screen, ret) {
    return S.screen === screen && S.ret === ret;
  }

  /* ------------------------------------------------------------------ *
   * API / 错误
   * ------------------------------------------------------------------ */
  class ApiError extends Error {
    constructor(code, message, detail, status) {
      super(message || code || '请求失败');
      this.name = 'ApiError';
      this.code = code || 'E_UNKNOWN';
      this.detail = detail || null;
      this.status = Number(status) || 0;
    }
  }

  async function call(method, params, opts) {
    if (!S.token) throw new ApiError('E_NO_TOKEN', '缺少访问令牌：请从 guardctl web 输出的带 #token= 地址打开', null, 401);
    const timeout = (opts && opts.timeout) || 15000;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch('/api/' + method, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ' + S.token
        },
        body: JSON.stringify(params || {}),
        signal: controller.signal,
        cache: 'no-store',
        credentials: 'same-origin'
      });
      const text = await response.text();
      let payload = null;
      try { payload = text ? JSON.parse(text) : {}; } catch (err) {
        throw new ApiError('E_BAD_JSON', '网关返回了无效 JSON（HTTP ' + response.status + '）', null, response.status);
      }
      if (!response.ok || !payload || payload.ok !== true) {
        const info = (payload && payload.error) || {};
        throw new ApiError(info.code || ('HTTP_' + response.status),
          info.message || ('请求失败：HTTP ' + response.status), info.detail || null, response.status);
      }
      return payload.result;
    } catch (err) {
      if (err && err.name === 'AbortError') {
        throw new ApiError('E_TIMEOUT', '请求超时（' + Math.round(timeout / 1000) + ' 秒）', null, 504);
      }
      if (err instanceof ApiError) throw err;
      throw new ApiError('E_NETWORK', '无法连接本地网关：' + (err && err.message ? err.message : err), null, 0);
    } finally {
      clearTimeout(timer);
    }
  }

  /* ------------------------------------------------------------------ *
   * 顶栏 / 横幅 / 状态栏
   * ------------------------------------------------------------------ */
  function ipcLabel(ipc) {
    if (!ipc) return 'IPC —';
    if (ipc.transport === 'pipe') return 'PIPE ' + (ipc.pipe || '');
    if (ipc.transport === 'tcp') return 'TCP ' + (ipc.host || '127.0.0.1') + ':' + (ipc.port || '?');
    return String(ipc.transport || 'IPC').toUpperCase();
  }

  function buildChrome() {
    const host = $('#top-right');
    if (!host) return;
    host.innerHTML =
      '<span class="badge" id="badge-conn"><i class="dot"></i><span id="badge-conn-text"></span></span>' +
      '<span class="badge" id="badge-role"><i class="dot"></i><span id="badge-role-text"></span></span>' +
      '<span class="badge" id="badge-dry"><i class="dot"></i><span id="badge-dry-text"></span></span>' +
      '<button class="btn ghost sm" data-act="refresh">刷新</button>';
    S.chromeBuilt = true;
  }

  function setBadge(id, kind, text) {
    const node = $('#' + id);
    if (!node) return;
    const cls = 'badge ' + (kind || '');
    if (node.className !== cls) node.className = cls;
    const label = $('#' + id + '-text');
    if (label && label.textContent !== text) label.textContent = text;
  }

  function updateChrome() {
    const st = S.status;
    if (!st) return;
    if (!S.chromeBuilt) buildChrome();
    const dry = !!st.dry_run;
    const online = S.connected;
    setBadge('badge-conn', online ? 'ok' : 'err', online ? '已连接' : '重连中');
    setBadge('badge-role', st.elevated ? 'accent' : 'warn', st.elevated ? '管理员' : '降级');
    setBadge('badge-dry', dry ? 'accent' : 'err', dry ? '演练' : '实盘');

    const adminBtn = '<span style="flex:1"></span>' +
      '<button class="btn xs ghost" data-act="show-admin-help">如何获得管理员</button>';
    const settingsBtn = '<span style="flex:1"></span>' +
      '<button class="btn xs ghost" data-act="nav" data-screen="settings">去设置关闭演练</button>';
    let kind = '';
    let html = '';
    if (!st.elevated) {
      kind = 'warn';
      html = '<b>只读模式</b> · 当前守护是普通用户：可以查看 / 生成计划 / 演练，' +
        '真实结束进程与停服务不可用。' + adminBtn;
    } else if (st.health === 'degraded') {
      kind = 'warn';
      html = '<b>降级运行</b> · ' + esc(st.degraded_reason || '守护报告降级') +
        (dry ? '（当前仍是演练模式）' : '');
    } else if (dry) {
      kind = 'warn';
      html = '<b>管理员已就绪 · 仍是演练模式</b> · 执行只写审计，不会真正结束进程。' +
        settingsBtn;
    } else {
      kind = 'err';
      html = '<b>实盘模式</b> · 执行清理会真正结束进程 / 停止服务';
    }
    const key = kind + '|' + html;
    if (key !== S.bannerKey) {
      if (!html) hideBanner();
      else showBanner(kind, html);
      S.bannerKey = key;
    }
    renderStatusbar();
  }

  function showBanner(kind, html) {
    const el = $('#banner');
    el.className = 'banner ' + (kind || 'info');
    el.innerHTML = html;
  }

  function hideBanner() {
    const el = $('#banner');
    if (el.className === 'banner hidden' && !el.innerHTML) return;
    el.className = 'banner hidden';
    el.innerHTML = '';
    S.bannerKey = '';
  }

  function renderStatusbar() {
    const st = S.status || {};
    const ipc = st.ipc || {};
    const left = '守护 ' + String(st.health || '—').toUpperCase() + ' · ' + ipcLabel(ipc) +
      ' · 运行 ' + fmtDuration(st.uptime_s || 0);
    const right = '事件 #' + S.seq + ' · ' + (S.lastUpdate ? fmtClock(S.lastUpdate) : '等待数据');
    $('#status-left').textContent = left;
    $('#status-right').textContent = right;
  }

  /* ------------------------------------------------------------------ *
   * 状态 / SSE / 轮询
   * ------------------------------------------------------------------ */
  async function refreshStatus() {
    const st = await call('status.get', {}, { timeout: 8000 });
    S.status = st;
    S.connected = true;
    S.lastUpdate = Date.now();
    if (typeof st.event_seq === 'number') S.seq = Math.max(S.seq, st.event_seq);
    updateChrome();
    return st;
  }

  function connectEvents() {
    if (!S.token) return;
    if (S.eventSource) {
      try { S.eventSource.close(); } catch (err) { /* ignore */ }
      S.eventSource = null;
    }
    const url = '/api/events?since_seq=' + encodeURIComponent(String(S.seq)) +
      '&token=' + encodeURIComponent(S.token);
    let stream;
    try { stream = new EventSource(url); } catch (err) {
      startPolling();
      return;
    }
    S.eventSource = stream;
    stream.onopen = () => {
      S.connected = true;
      updateChrome();
      stopPolling();
    };
    stream.onerror = () => {
      S.connected = false;
      updateChrome();
      startPolling();
    };
    SSE_EVENTS.forEach((name) => {
      stream.addEventListener(name, (ev) => {
        let envelope = null;
        try { envelope = JSON.parse(ev.data); } catch (err) { return; }
        handleEvent(name, envelope);
      });
    });
    stream.onmessage = (ev) => {
      let envelope = null;
      try { envelope = JSON.parse(ev.data); } catch (err) { return; }
      handleEvent('message', envelope);
    };
  }

  function handleEvent(name, envelope) {
    const data = (envelope && envelope.data) || {};
    if (envelope && typeof envelope.seq === 'number') S.seq = Math.max(S.seq, envelope.seq);
    S.lastUpdate = Date.now();
    if (name === 'heartbeat') {
      if (S.status) {
        if (data.health) S.status.health = data.health;
        if (data.counts) S.status.counts = data.counts;
      }
      updateChrome();
      return;
    }
    if (name === 'snapshot.updated') {
      if (S.status && data.counts) S.status.counts = data.counts;
      updateChrome();
      scheduleSoft(12000);
    } else if (name === 'process.started' || name === 'process.exited') {
      scheduleSoft(8000);
    } else if (name === 'software.started' || name === 'software.stopped') {
      scheduleSoft(8000);
    } else if (name === 'cleanup.executed') {
      S.plan = null; S.planKey = ''; S.cleanup.plan = null;
      toast('清理已执行：' + (data.killed || 0) + ' 项成功，' + (data.failed || 0) + ' 项失败', 'ok');
      scheduleRefresh(['cleanup', 'overview', 'audit'], 450);
    } else if (name === 'policy.changed') {
      S.policy = null;
      updateChrome();
      scheduleRefresh(['settings'], 350);
    } else if (name === 'notice') {
      toast(data.message || '守护通知', data.level === 'error' ? 'err' : (data.level === 'warn' ? 'warn' : 'info'));
    } else if (name === 'service.stopped') {
      toast('服务已停止：' + (data.name || '（未知）'), 'ok');
      scheduleSoft(900);
    } else if (name === 'service.stop_failed' || name === 'process.kill_failed') {
      toast('操作失败：' + (data.name || '（未知）'), 'err');
      scheduleSoft(900);
    } else if (name === 'process.blocked') {
      scheduleSoft(1200);
    } else if (name.indexOf('kb.') === 0) {
      if (name === 'kb.review') toast('知识库待办包已生成', 'ok');
      scheduleRefresh(['settings'], 800);
    } else if (name === 'config.reloaded') {
      scheduleRefresh(['settings'], 500);
    }
  }

  function scheduleRefresh(screens, delay) {
    if (S.modalOpen || document.hidden || screens.indexOf(S.screen) < 0) return;
    clearTimeout(S.refreshTimer);
    S.refreshTimer = setTimeout(() => {
      if (!document.hidden) refreshCurrent();
    }, delay || 1200);
  }

  const SOFT_SCREENS = ['overview', 'software', 'processes', 'audit'];

  function softEligible() {
    if (!S.token || S.modalOpen || document.hidden) return false;
    const active = document.activeElement;
    if (active && ['INPUT', 'TEXTAREA'].indexOf(active.tagName) >= 0) return false;
    return !!$('#screen .screen-inner');
  }

  function scheduleSoft(delay) {
    if (S.softTimer || !softEligible()) return;
    S.softTimer = setTimeout(() => {
      S.softTimer = 0;
      softRefresh(false);
    }, delay || 12000);
  }

  async function softRefresh(force) {
    if (!softEligible()) return;
    if (!force && Date.now() - S.softLast < 4000) return;
    S.softLast = Date.now();
    const screen = S.screen;
    const seq = ++S.softSeq;
    const host = $('#screen');
    const top = host ? host.scrollTop : 0;
    try {
      if (screen === 'overview') await softOverview(seq);
      else if (screen === 'software') await softSoftware(seq);
      else if (screen === 'processes') await softProcesses(seq);
      else if (screen === 'audit') await softAudit(seq);
    } catch (err) {
      if (seq === S.softSeq) console.debug('soft refresh failed', err);
    } finally {
      if (S.screen === screen && host) host.scrollTop = top;
    }
  }

  function softCurrent(screen, seq) {
    return S.screen === screen && S.softSeq === seq;
  }

  function startPolling() {
    if (S.pollTimer) return;
    S.pollTimer = setInterval(async () => {
      if (document.hidden) return;
      try {
        await refreshStatus();
        refreshCurrent();
      } catch (err) { /* 保持重连状态 */ }
    }, POLL_MS);
  }

  function stopPolling() {
    if (S.pollTimer) {
      clearInterval(S.pollTimer);
      S.pollTimer = 0;
    }
  }

  /* ------------------------------------------------------------------ *
   * 路由
   * ------------------------------------------------------------------ */
  function buildNav() {
    $('#nav').innerHTML = NAV.map((item) =>
      '<button class="nav-item" data-act="nav" data-screen="' + item.id + '" title="' + esc(item.label) + '">' +
        '<span class="ico">' + ICONS[item.id] + '</span>' +
        '<span class="nav-label">' + esc(item.label) + '</span>' +
      '</button>').join('');
  }

  function go(screen, opts) {
    const meta = SCREENS[screen] || SCREENS.overview;
    const key = SCREENS[screen] ? screen : 'overview';
    S.screen = key;
    S.screenOpts = opts || {};
    S.ret += 1;
    const ret = S.ret;
    closeModal(true);
    $$('.nav-item').forEach((node) => {
      if (node.dataset.screen === key) node.setAttribute('aria-current', 'page');
      else node.removeAttribute('aria-current');
    });
    $('.titles .kicker').textContent = meta.kicker;
    $('#page-title').textContent = meta.title;
    document.title = 'AppGuard · ' + meta.title;
    const host = $('#screen');
    host.scrollTop = 0;
    host.innerHTML = '<div class="screen-inner"><div class="loading"><span class="spinner"></span>正在读取守护…</div></div>';
    meta.load(ret, S.screenOpts).catch((err) => screenError(err, key, ret));
  }

  function refreshCurrent() {
    if (!S.token || S.modalOpen) return;
    if (SOFT_SCREENS.indexOf(S.screen) >= 0) {
      softRefresh(true);
      return;
    }
    const meta = SCREENS[S.screen];
    if (!meta) return;
    const active = document.activeElement;
    if (active && ['INPUT', 'TEXTAREA'].indexOf(active.tagName) >= 0) {
      /* 用户正在输入时不要整屏重绘，避免丢焦点/光标。 */
      return;
    }
    S.ret += 1;
    const ret = S.ret;
    meta.load(ret, S.screenOpts).catch((err) => {
      if (!isCurrent(S.screen, ret)) return;
      if (err && err.status === 401) { renderTokenInvalid(err); return; }
      toast('刷新失败：' + (err.message || err), 'err');
    });
  }

  function screenError(err, screen, ret) {
    if (!isCurrent(screen, ret)) return;
    if (err && err.status === 401) { renderTokenInvalid(err); return; }
    if (!S.connected || (err && (err.code === 'E_IPC' || err.code === 'E_NETWORK' || err.status === 503))) {
      renderOffline(err);
      return;
    }
    $('#screen').innerHTML =
      '<div class="screen-inner"><div class="card empty-card">' +
        '<div class="empty-glyph">!</div><h2>读取失败</h2>' +
        '<p>' + esc(err && err.message ? err.message : err) + '</p>' +
        '<button class="btn primary" data-act="retry">重试</button>' +
      '</div></div>';
  }

  function renderOffline(err) {
    S.connected = false;
    updateChrome();
    const message = err && err.message ? err.message : '守护不可达';
    $('#screen').innerHTML =
      '<div class="screen-inner"><div class="card empty-card">' +
        '<div class="empty-glyph">×</div><h2>守护未连接</h2>' +
        '<p>' + esc(message) + '</p>' +
        '<p class="muted">请先在项目目录启动守护：</p>' +
        '<pre>python tools\\guardctl.py serve --transport auto</pre>' +
        '<p class="muted">再另开一个终端启动桌面网关：</p>' +
        '<pre>python tools\\guardctl.py web --port 0</pre>' +
        '<button class="btn primary" data-act="retry">重新连接</button>' +
      '</div></div>';
  }

  function renderTokenMissing() {
    $('#screen').innerHTML =
      '<div class="screen-inner"><div class="card empty-card">' +
        '<div class="empty-glyph">#</div><h2>缺少访问令牌</h2>' +
        '<p>这个页面只接受带令牌的本地地址。令牌在启动网关时打印，也会写进端点文件。</p>' +
        '<pre>python tools\\guardctl.py web --port 0</pre>' +
        '<p class="muted">打开输出里的「带令牌 URL」（<code>#token=…</code>）。令牌只放在 URL fragment 里，不会发给服务器。</p>' +
        '<button class="btn primary" data-act="reload-page">重新加载</button>' +
      '</div></div>';
  }

  function renderTokenInvalid(err) {
    $('#screen').innerHTML =
      '<div class="screen-inner"><div class="card empty-card">' +
        '<div class="empty-glyph">!</div><h2>访问令牌无效或已过期</h2>' +
        '<p>' + esc(err && err.message ? err.message : '网关拒绝了本次请求') + '</p>' +
        '<p class="muted">网关每次启动都会换一个随机令牌；请用最新的带 <code>#token=</code> 地址重新打开。</p>' +
        '<button class="btn primary" data-act="reload-page">重新加载</button>' +
      '</div></div>';
  }

  function renderRetry() {
    refreshStatus().then(() => {
      connectEvents();
      go(S.screen, S.screenOpts);
    }).catch((err) => {
      if (err && err.status === 401) renderTokenInvalid(err);
      else renderOffline(err);
    });
  }

  /* ------------------------------------------------------------------ *
   * 通用组件
   * ------------------------------------------------------------------ */
  function card(kicker, title, body, actions, extraClass) {
    return '<section class="card ' + (extraClass || '') + '">' +
      '<header class="card-h"><span class="card-k">' + esc(kicker) + '</span>' +
      '<h2>' + esc(title) + '</h2>' +
      '<div class="card-actions">' + (actions || '') + '</div></header>' +
      '<div class="card-b">' + body + '</div>' +
    '</section>';
  }

  function statTile(label, value, sub, color, id) {
    const bars = [7, 12, 9, 15, 11, 18, 8, 13, 10, 16]
      .map((h) => '<i style="height:' + h + 'px"></i>').join('');
    return '<article class="stat"' + (id ? ' id="' + id + '"' : '') + '>' +
      '<div class="stat-lbl"><span class="stat-mark" style="background:' + color + '"></span>' + esc(label) + '</div>' +
      '<div class="stat-val">' + esc(value) + '</div>' +
      '<div class="stat-sub">' + esc(sub) + '</div>' +
      '<div class="stat-bars">' + bars + '</div>' +
    '</article>';
  }

  function donut(segments, opts) {
    const options = opts || {};
    const size = options.size || 168;
    const thickness = options.thickness || 20;
    const total = segments.reduce((acc, seg) => acc + Math.max(0, Number(seg.value) || 0), 0) || 1;
    const radius = (size - thickness) / 2;
    const center = size / 2;
    const circumference = 2 * Math.PI * radius;
    let offset = 0;
    const arcs = segments.map((seg) => {
      const len = Math.max(0, Number(seg.value) || 0) / total * circumference;
      const node = '<circle cx="' + center + '" cy="' + center + '" r="' + radius +
        '" fill="none" stroke="' + seg.color + '" stroke-width="' + thickness +
        '" stroke-dasharray="' + len + ' ' + (circumference - len) +
        '" stroke-dashoffset="' + (-offset) +
        '" transform="rotate(-90 ' + center + ' ' + center + ')"></circle>';
      offset += len;
      return node;
    }).join('');
    const value = options.centerValue != null ? options.centerValue : total;
    return '<div class="donut-wrap">' +
      '<svg width="' + size + '" height="' + size + '" viewBox="0 0 ' + size + ' ' + size + '">' + arcs + '</svg>' +
      '<div class="donut-center"><b>' + esc(value) + '</b><span>' + esc(options.centerLabel || '') + '</span></div>' +
    '</div>';
  }

  function legend(segments, total) {
    return '<div class="legend">' + segments.map((seg) =>
      '<div class="legend-row"><i style="background:' + seg.color + '"></i>' +
        '<span>' + esc(seg.label) + '<span class="pct">' + pct(seg.value, total) + '</span></span>' +
        '<b>' + esc(seg.value) + '</b></div>').join('') + '</div>';
  }

  function barList(rows) {
    if (!rows.length) return '<div class="tbl-empty">没有数据</div>';
    return '<div class="bar-list">' + rows.map((row) =>
      '<div class="bar-row"' + (row.act ? ' data-act="' + row.act + '" data-id="' + esc(row.id) + '"' : '') + '>' +
        '<span class="bar-name" title="' + esc(row.name) + '">' + esc(row.name) + '</span>' +
        '<div class="bar-track"><i style="width:' + Math.max(1, Math.min(100, row.pct)) + '%;background:' + (row.color || 'linear-gradient(90deg,var(--accent-2),var(--sky))') + '"></i></div>' +
        '<span class="bar-val">' + esc(row.value) + '</span>' +
      '</div>').join('') + '</div>';
  }

  function tag(text, kind) {
    return '<span class="tag ' + (kind || '') + '">' + esc(text) + '</span>';
  }

  function emptyBlock(text) {
    return '<div class="tbl-empty">' + esc(text) + '</div>';
  }

  /* ------------------------------------------------------------------ *
   * 总览
   * ------------------------------------------------------------------ */
  async function loadOverview(ret) {
    const st = await call('status.get');
    if (!isCurrent('overview', ret)) return;
    S.status = st;
    S.connected = true;
    S.lastUpdate = Date.now();
    if (typeof st.event_seq === 'number') S.seq = Math.max(S.seq, st.event_seq);
    const res = await call('software.list', { limit: 0 });
    if (!isCurrent('overview', ret)) return;
    S.software = res.items || [];
    let plan = null;
    try {
      plan = await ensurePlan('all', { scope: 'all' });
    } catch (err) {
      plan = null;
    }
    if (!isCurrent('overview', ret)) return;
    renderOverview(st, S.software, plan);
    updateChrome();
  }

  function overviewParts(st, software, plan) {
    const counts = st.counts || {};
    const totalSw = software.length || counts.software || 0;
    const inUse = software.filter((s) => s.in_use);
    const privateSw = software.filter((s) => (s.private_background || 0) > 0);
    const privateActive = privateSw.filter((s) => !s.in_use);
    const other = Math.max(0, totalSw - inUse.length - privateActive.length);
    const summary = (plan && plan.summary) || {};
    const safeMem = summary.default_memory_mb || 0;
    const safeCount = summary.default_checked || 0;
    const protectedCount = summary.protected || 0;
    const memTop = software.slice().sort((a, b) => (b.memory_mb || 0) - (a.memory_mb || 0)).slice(0, 8);
    const maxMem = Math.max(1, memTop.length ? (memTop[0].memory_mb || 0) : 1);
    const expired = !!(plan && planExpired(plan));
    const segments = [
      { label: '在用', value: inUse.length, color: 'var(--sky)' },
      { label: '私自后台', value: privateActive.length, color: 'var(--accent)' },
      { label: '其他', value: other, color: '#55555f' }
    ];
    const donutHtml =
      '<div class="donut-flex">' +
        donut(segments, { centerValue: totalSw, centerLabel: '软件' }) +
        legend(segments, totalSw) +
      '</div>';
    const memHtml = barList(memTop.map((s) => ({
      name: s.name,
      value: fmtMB(s.memory_mb),
      pct: Math.round((s.memory_mb || 0) / maxMem * 100),
      act: 'sw-open',
      id: s.id,
      color: 'linear-gradient(90deg,var(--accent),var(--sky))'
    }))) +
      '<div class="card-note" style="margin-top:12px">条长 = 工作集内存，点击进入软件详情。</div>';
    const privateHtml = privateSw.length
      ? '<div class="tiles">' + privateSw.slice(0, 8).map((s) =>
          '<button class="mini-tile" data-act="clean-software" data-id="' + esc(s.id) + '" data-name="' + esc(s.name) + '">' +
            '<span class="mini-ic" style="background:' + swColor(s.id) + '">' + esc(initials(s.name)) + '</span>' +
            '<span class="mini-main"><b>' + esc(s.name) + '</b><small>' + (s.private_background || 0) + ' 个后台组件</small></span>' +
            '<span class="mini-arrow">→</span>' +
          '</button>').join('') + '</div>'
      : emptyBlock('没有发现私自后台组件');
    const inUseHtml = inUse.length
      ? '<div class="use-list">' + inUse.slice(0, 8).map((s) =>
          '<button class="use-row" data-act="sw-open" data-id="' + esc(s.id) + '">' +
            '<i class="dot sky"></i>' +
            '<span class="use-name">' + esc(s.name) + '</span>' +
            '<span class="use-reason" title="' + esc(s.in_use_reason) + '">' + esc(s.in_use_reason || '在用') + '</span>' +
          '</button>').join('') + '</div>'
      : emptyBlock('没有识别到正在使用的软件');
    return {
      heroNum: counts.private || 0,
      statUse: statTile('在用软件', fmtNum(counts.in_use != null ? counts.in_use : inUse.length),
        '共 ' + fmtNum(totalSw) + ' 个软件 · ' + fmtNum(counts.processes || 0) + ' 个进程',
        'var(--sky)', 'ov-stat-use'),
      statPrivate: statTile('私自进程', fmtNum(counts.private || 0),
        privateSw.length + ' 个软件带后台组件', 'var(--accent)', 'ov-stat-private'),
      statMem: statTile('默认可释放', fmtMB(safeMem),
        expired ? '计划已过期 · 进入清理重新生成' : '默认勾选 ' + safeCount + ' 项',
        'var(--green)', 'ov-stat-mem'),
      statProtected: statTile('受保护', fmtNum(protectedCount), '微软 / 系统 / 白名单',
        'var(--amber)', 'ov-stat-protected'),
      donut: donutHtml,
      mem: memHtml,
      private: privateHtml,
      inuse: inUseHtml
    };
  }

  function renderOverview(st, software, plan) {
    const p = overviewParts(st, software, plan);
    $('#screen').innerHTML =
      '<div class="screen-inner enter">' +
        '<section class="hero">' +
          '<div class="hero-copy">' +
            '<div class="kicker">APPGUARD / PRIVATE LEDGER</div>' +
            '<h2>没在用，<em>就别在跑。</em></h2>' +
            '<p>按软件分组看清每一个进程；只有你勾选，工具才动手。</p>' +
          '</div>' +
          '<div class="hero-num"><b id="ov-hero-num">' + esc(p.heroNum) + '</b><span>私自进程</span></div>' +
          '<div class="hero-act">' +
            '<button class="btn primary" data-act="goto-cleanup">生成清理计划</button>' +
            '<button class="btn ghost" data-act="goto-processes">查看进程分类</button>' +
          '</div>' +
        '</section>' +
        '<div class="stats">' + p.statUse + p.statPrivate + p.statMem + p.statProtected + '</div>' +
        '<div class="grid g2">' +
          card('01 / COMPOSITION', '软件构成', '<div id="ov-donut">' + p.donut + '</div>') +
          card('02 / MEMORY', '内存 TOP', '<div id="ov-mem">' + p.mem + '</div>') +
        '</div>' +
        '<div class="grid g2">' +
          card('03 / PRIVATE', '私自组件', '<div id="ov-private">' + p.private + '</div>',
            '<button class="btn sm ghost" data-act="nav" data-screen="cleanup">全部清理</button>') +
          card('04 / IN USE', '正在使用的软件', '<div id="ov-inuse">' + p.inuse + '</div>',
            '<button class="btn sm ghost" data-act="nav" data-screen="software">软件清单</button>') +
        '</div>' +
      '</div>';
  }

  async function softOverview(seq) {
    const st = await call('status.get');
    if (!softCurrent('overview', seq)) return;
    S.status = st;
    S.connected = true;
    S.lastUpdate = Date.now();
    if (typeof st.event_seq === 'number') S.seq = Math.max(S.seq, st.event_seq);
    const res = await call('software.list', { limit: 0 });
    if (!softCurrent('overview', seq)) return;
    S.software = res.items || [];
    updateChrome();
    const plan = (S.plan && S.planKey === 'all') ? S.plan : null;
    const p = overviewParts(st, S.software, plan);
    setText('#ov-hero-num', p.heroNum);
    setHTML('#ov-stat-use', p.statUse);
    setHTML('#ov-stat-private', p.statPrivate);
    setHTML('#ov-stat-mem', p.statMem);
    setHTML('#ov-stat-protected', p.statProtected);
    setHTML('#ov-donut', p.donut);
    setHTML('#ov-mem', p.mem);
    setHTML('#ov-private', p.private);
    setHTML('#ov-inuse', p.inuse);
  }

  /* ------------------------------------------------------------------ *
   * 软件
   * ------------------------------------------------------------------ */
  async function loadSoftware(ret) {
    const res = await call('software.list', { limit: 0 });
    if (!isCurrent('software', ret)) return;
    S.software = res.items || [];
    renderSoftwareScreen();
  }

  function softwareFiltered() {
    let list = S.software.slice();
    const filter = S.sw.filter;
    if (filter === 'in_use') list = list.filter((s) => s.in_use);
    else if (filter === 'private') list = list.filter((s) => (s.private_background || 0) > 0);
    else if (filter === 'system') list = list.filter((s) => s.kind === 'system');
    if (S.sw.q) {
      const q = S.sw.q.toLowerCase();
      list = list.filter((s) => [s.name, s.publisher, s.version, s.id]
        .some((v) => String(v || '').toLowerCase().indexOf(q) >= 0));
    }
    const sort = S.sw.sort;
    list.sort((a, b) => {
      if (sort === 'name') return String(a.name || '').localeCompare(String(b.name || ''), 'zh');
      if (sort === 'private') return (b.private_background || 0) - (a.private_background || 0);
      return (b.memory_mb || 0) - (a.memory_mb || 0);
    });
    return list;
  }

  function softwareCounts() {
    return {
      all: S.software.length,
      in_use: S.software.filter((s) => s.in_use).length,
      private: S.software.filter((s) => (s.private_background || 0) > 0).length,
      system: S.software.filter((s) => s.kind === 'system').length
    };
  }

  function renderSoftwareScreen() {
    const counts = softwareCounts();
    const chips = [
      ['all', '全部', counts.all],
      ['in_use', '在用', counts.in_use],
      ['private', '私自', counts.private],
      ['system', '系统', counts.system]
    ].map((item) =>
      '<button class="chip" data-act="sw-filter" data-filter="' + item[0] + '">' +
        esc(item[1]) + ' <span class="n">' + item[2] + '</span></button>').join('');
    $('#screen').innerHTML =
      '<div class="screen-inner enter">' +
        '<div class="toolbar"><div class="chips">' + chips + '</div>' +
          '<div class="search"><input class="input" id="sw-q" placeholder="搜索软件 / 发布者 / ID" value="' + esc(S.sw.q) + '"></div>' +
          '<select class="select" id="sw-sort">' +
            '<option value="memory"' + (S.sw.sort === 'memory' ? ' selected' : '') + '>按内存</option>' +
            '<option value="private"' + (S.sw.sort === 'private' ? ' selected' : '') + '>按私自组件</option>' +
            '<option value="name"' + (S.sw.sort === 'name' ? ' selected' : '') + '>按名称</option>' +
          '</select>' +
          '<span class="count" id="sw-count"></span>' +
        '</div>' +
        '<div class="sw-grid" id="sw-grid"></div>' +
      '</div>';
    updateSoftwareGrid();
  }

  function updateSoftwareChips() {
    const counts = softwareCounts();
    const values = { all: counts.all, in_use: counts.in_use, private: counts.private, system: counts.system };
    $$('.chip[data-filter]').forEach((node) => {
      node.classList.toggle('active', node.dataset.filter === S.sw.filter);
      const n = node.querySelector('.n');
      if (n) setText(n, values[node.dataset.filter] != null ? values[node.dataset.filter] : 0);
    });
  }

  function updateSoftwareGrid() {
    const list = softwareFiltered();
    const host = $('#sw-grid');
    if (!host) return;
    const count = $('#sw-count');
    if (count) count.textContent = list.length + ' / ' + S.software.length + ' 个软件';
    updateSoftwareChips();
    if (!list.length) {
      setHTML(host, '<div class="sw-empty">没有匹配的软件。换个筛选条件试试。</div>');
      return;
    }
    host.innerHTML = list.map((s) => swCard(s)).join('');
  }

  function swCardClass(s) {
    return 'sw-card ' + (s.in_use ? 'is-use' : '') + ' ' + ((s.private_background || 0) ? 'is-private' : '');
  }

  function swCardContent(s) {
    const isUse = !!s.in_use;
    const privateCount = s.private_background || 0;
    const privatePct = Math.min(100, Math.round(privateCount / Math.max(1, (s.processes || []).length) * 100));
    return '<span class="sw-top"><span class="dot ' + (isUse ? 'sky' : 'muted') + '"></span>' +
        '<h3>' + esc(s.name) + '</h3>' + tag(KIND_ZH[s.kind] || s.kind || '未知', 'muted') + '</span>' +
      '<span class="sw-sub">' + esc([s.publisher, s.version].filter(Boolean).join(' · ') || s.id) + '</span>' +
      '<span class="sw-meter"><i style="width:' + privatePct + '%"></i></span>' +
      '<span class="sw-stats">' +
        '<span><b>' + (s.processes || []).length + '</b> 进程</span>' +
        '<span><b>' + privateCount + '</b> 私自</span>' +
        '<span><b>' + (s.services || []).length + '</b> 服务</span>' +
        '<span><b>' + esc(fmtMB(s.memory_mb)) + '</b></span>' +
      '</span>' +
      '<span class="sw-use ' + (isUse ? 'on' : '') + '">' + (isUse ? '在用 · ' + esc(s.in_use_reason || '') : '未在用') + '</span>';
  }

  function swCard(s) {
    return '<button class="' + swCardClass(s) + '" data-act="sw-open" data-id="' + esc(s.id) + '">' +
      swCardContent(s) +
    '</button>';
  }

  function patchSoftwareGrid() {
    const host = $('#sw-grid');
    if (!host) return;
    const list = softwareFiltered();
    const count = $('#sw-count');
    if (count) count.textContent = list.length + ' / ' + S.software.length + ' 个软件';
    updateSoftwareChips();
    if (!list.length) {
      setHTML(host, '<div class="sw-empty">没有匹配的软件。换个筛选条件试试。</div>');
      return;
    }
    const empty = host.querySelector('.sw-empty');
    if (empty) host.innerHTML = '';
    const nodes = new Map();
    Array.from(host.children).forEach((node) => {
      if (node.dataset && node.dataset.id) nodes.set(node.dataset.id, node);
    });
    const wanted = new Set(list.map((s) => s.id));
    nodes.forEach((node, id) => {
      if (!wanted.has(id)) {
        node.remove();
        nodes.delete(id);
      }
    });
    list.forEach((s) => {
      let node = nodes.get(s.id);
      if (!node) {
        node = document.createElement('button');
        node.dataset.act = 'sw-open';
        node.dataset.id = s.id;
        nodes.set(s.id, node);
      }
      const cls = swCardClass(s);
      if (node.className !== cls) node.className = cls;
      const content = swCardContent(s);
      if (node.innerHTML !== content) node.innerHTML = content;
    });
    const desired = list.map((s) => nodes.get(s.id));
    const current = Array.from(host.children);
    const same = desired.length === current.length && desired.every((node, i) => node === current[i]);
    if (!same) desired.forEach((node) => host.appendChild(node));
  }

  async function softSoftware(seq) {
    const res = await call('software.list', { limit: 0 });
    if (!softCurrent('software', seq)) return;
    S.software = res.items || [];
    patchSoftwareGrid();
  }

  async function openSoftware(id) {
    openModal('软件详情', '<div class="loading"><span class="spinner"></span>正在读取软件…</div>', { wide: true });
    try {
      const sw = await call('software.get', { id: id });
      if (!S.modalOpen) return;
      openModal('软件 · ' + (sw.name || id), softwareModalBody(sw), { wide: true });
    } catch (err) {
      if (!S.modalOpen) return;
      openModal('软件详情', '<p>' + esc(err.message || err) + '</p>', { wide: true });
    }
  }

  function softwareModalBody(sw) {
    const procs = sw.processes || [];
    const services = sw.services || [];
    const autostart = sw.autostart || [];
    const actions =
      '<div class="modal-actions">' +
        ((sw.private_background || 0) > 0
          ? '<button class="btn primary" data-act="clean-software" data-id="' + esc(sw.id) + '" data-name="' + esc(sw.name) + '">清理该软件的私自进程（' + sw.private_background + '）</button>'
          : '') +
        '<button class="btn" data-act="protect-software" data-id="' + esc(sw.id) + '">加入保护名单</button>' +
        '<button class="btn ghost" data-act="modal-close">关闭</button>' +
      '</div>';
    const procRows = procs.length ? procs.map((p) => {
      const c = p.classification || {};
      return '<tr>' +
        '<td class="pid">' + p.pid + '</td>' +
        '<td class="name">' + esc(p.name) + '</td>' +
        '<td>' + esc(ROLE_ZH[c.role] || c.role || '—') + '</td>' +
        '<td>' + esc(ACTIVITY_ZH[c.activity] || c.activity || '—') + '</td>' +
        '<td>' + esc(ORIGIN_ZH[c.origin] || c.origin || '—') + '</td>' +
        '<td>' + (c.private ? tag('私自', 'accent') : (c.microsoft ? tag('微软', 'muted') : '—')) + '</td>' +
        '<td class="num">' + esc(fmtMB(p.memory_mb)) + '</td>' +
      '</tr>';
    }).join('') : '<tr><td colspan="7" class="tbl-empty">没有进程</td></tr>';
    const serviceRows = services.length ? services.map((sv) =>
      '<tr><td class="name">' + esc(sv.name) + '</td><td>' + esc(sv.display_name || '') + '</td>' +
        '<td>' + esc(sv.state || '') + '</td><td>' + esc(sv.start_type || '') + '</td>' +
        '<td>' + esc(sv.account || '') + '</td></tr>').join('')
      : '<tr><td colspan="5" class="tbl-empty">没有关联服务</td></tr>';
    const autoRows = autostart.length ? autostart.map((a) =>
      '<tr><td class="name">' + esc(a.name) + '</td><td>' + esc(a.kind || '') + '</td>' +
        '<td class="wrap">' + esc(a.command || '') + '</td>' +
        '<td>' + (a.enabled ? tag('启用', 'green') : tag('停用', 'muted')) + '</td></tr>').join('')
      : '<tr><td colspan="4" class="tbl-empty">没有自启项</td></tr>';

    return '<div class="sw-hero">' +
        '<span class="mini-ic" style="background:' + swColor(sw.id) + '">' + esc(initials(sw.name)) + '</span>' +
        '<div><h3>' + esc(sw.name) + '</h3><div class="sw-sub">' + esc([sw.publisher, sw.version].filter(Boolean).join(' · ') || sw.id) + '</div></div>' +
        '<div class="modal-actions" style="margin-left:auto">' +
          (sw.in_use ? tag('在用', 'sky') : tag('未在用', 'muted')) +
          ((sw.private_background || 0) > 0 ? tag('私自 ' + sw.private_background, 'accent') : '') +
        '</div>' +
      '</div>' +
      '<dl class="modal-kv">' +
        '<dt>软件 ID</dt><dd class="mono">' + esc(sw.id) + '</dd>' +
        '<dt>类型</dt><dd>' + esc(KIND_ZH[sw.kind] || sw.kind || '未知') + '</dd>' +
        '<dt>安装目录</dt><dd class="mono">' + esc(sw.install_dir || '—') + '</dd>' +
        '<dt>在用依据</dt><dd>' + esc(sw.in_use_reason || '—') + '</dd>' +
        '<dt>内存 / 进程</dt><dd>' + esc(fmtMB(sw.memory_mb)) + ' / ' + procs.length + ' 个</dd>' +
        '<dt>保护状态</dt><dd>' + (sw.in_use ? '在用软件的后台组件默认不勾选' : '未在用，后台组件可默认勾选') + '</dd>' +
      '</dl>' +
      actions +
      '<div class="modal-section"><h3>进程（' + procs.length + '）</h3><div class="table-wrap"><table class="tbl">' +
        '<thead><tr><th>PID</th><th>进程</th><th>角色</th><th>活跃</th><th>来源</th><th>标记</th><th class="num">内存</th></tr></thead>' +
        '<tbody>' + procRows + '</tbody></table></div></div>' +
      '<div class="modal-section"><h3>服务（' + services.length + '）</h3><div class="table-wrap"><table class="tbl">' +
        '<thead><tr><th>服务</th><th>显示名</th><th>状态</th><th>启动类型</th><th>账户</th></tr></thead>' +
        '<tbody>' + serviceRows + '</tbody></table></div></div>' +
      '<div class="modal-section"><h3>自启项（' + autostart.length + '）</h3><div class="table-wrap"><table class="tbl">' +
        '<thead><tr><th>名称</th><th>类型</th><th>命令</th><th>状态</th></tr></thead>' +
        '<tbody>' + autoRows + '</tbody></table></div></div>';
  }

  /* ------------------------------------------------------------------ *
   * 进程
   * ------------------------------------------------------------------ */
  async function ensureSoftware() {
    if (S.software && S.software.length) return S.software;
    const res = await call('software.list', { limit: 0 });
    S.software = res.items || [];
    return S.software;
  }

  async function loadProcesses(ret) {
    const [procs, software] = await Promise.all([
      call('process.list', { limit: 0, sort: 'memory' }),
      ensureSoftware()
    ]);
    if (!isCurrent('processes', ret)) return;
    S.procs = procs.items || [];
    S.procsTotal = procs.total || S.procs.length;
    S.software = software || S.software;
    renderProcessScreen();
  }

  function optionList(entries, selected) {
    return entries.map((entry) =>
      '<option value="' + esc(entry[0]) + '"' + (entry[0] === selected ? ' selected' : '') + '>' +
      esc(entry[1]) + '</option>').join('');
  }

  function renderProcessScreen() {
    const swEntries = [['', '全部软件']].concat(
      S.software.slice().sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'zh'))
        .map((s) => [s.id, s.name || s.id]));
    const roleEntries = [['', '全部角色']].concat(Object.keys(ROLE_ZH).map((k) => [k, ROLE_ZH[k]]));
    const actEntries = [['', '全部活跃度']].concat(Object.keys(ACTIVITY_ZH).map((k) => [k, ACTIVITY_ZH[k]]));
    const originEntries = [['', '全部来源']].concat(Object.keys(ORIGIN_ZH).map((k) => [k, ORIGIN_ZH[k]]));
    const roleLegend = ['main', 'service', 'daemon', 'helper', 'updater', 'ad', 'system']
      .map((r) => '<span><i style="background:' + TM_COLORS[r] + '"></i>' + esc(ROLE_ZH[r]) + '</span>').join('');

    $('#screen').innerHTML =
      '<div class="screen-inner enter">' +
        '<div class="toolbar">' +
          '<div class="search"><input class="input" id="proc-q" placeholder="搜索进程名 / PID / 路径" value="' + esc(S.proc.q) + '"></div>' +
          '<select class="select" id="proc-software">' + optionList(swEntries, S.proc.software) + '</select>' +
          '<select class="select" id="proc-role">' + optionList(roleEntries, S.proc.role) + '</select>' +
          '<select class="select" id="proc-activity">' + optionList(actEntries, S.proc.activity) + '</select>' +
          '<select class="select" id="proc-origin">' + optionList(originEntries, S.proc.origin) + '</select>' +
          '<select class="select" id="proc-sort">' + optionList([
            ['memory', '按内存'], ['cpu', '按 CPU'], ['pid', '按 PID'], ['name', '按名称']
          ], S.proc.sort) + '</select>' +
          '<span class="spacer"></span>' +
          '<span class="count" id="proc-count"></span>' +
        '</div>' +
        '<section class="card">' +
          '<header class="card-h"><span class="card-k">01 / MEMORY MAP</span><h2>内存地图</h2>' +
            '<div class="card-actions"><span class="card-note">面积 = 工作集内存 · 颜色 = 角色</span></div></header>' +
          '<div class="card-b"><div id="tm-host"></div><div class="tm-legend">' + roleLegend + '</div></div>' +
        '</section>' +
        '<div class="proc-layout">' +
          '<section class="card">' +
            '<header class="card-h"><span class="card-k">02 / PROCESSES</span><h2>进程分类</h2>' +
              '<div class="card-actions"><span class="card-note">点一行看解释</span></div></header>' +
            '<div class="card-b flush"><div class="table-wrap"><table class="tbl">' +
              '<thead><tr><th>PID</th><th>进程</th><th>角色</th><th>活跃</th><th>来源</th><th>标记</th><th class="num">内存</th><th>归属</th><th>风险</th></tr></thead>' +
              '<tbody id="proc-body"></tbody></table></div>' +
              '<div class="pager" id="proc-pager"></div></div>' +
          '</section>' +
          '<aside class="card proc-side">' +
            '<header class="card-h"><span class="card-k">03 / EXPLAIN</span><h2>进程解释</h2></header>' +
            '<div class="card-b" id="proc-side"></div>' +
          '</aside>' +
        '</div>' +
      '</div>';
    updateProcessView();
  }

  function filteredProcs() {
    let list = S.procs.slice();
    const f = S.proc;
    if (f.q) {
      const q = f.q.toLowerCase();
      list = list.filter((p) => String(p.name || '').toLowerCase().indexOf(q) >= 0 ||
        String(p.exe || '').toLowerCase().indexOf(q) >= 0 ||
        String(p.pid).indexOf(q) >= 0);
    }
    if (f.software) list = list.filter((p) => (p.classification || {}).software_id === f.software);
    if (f.role) list = list.filter((p) => (p.classification || {}).role === f.role);
    if (f.activity) list = list.filter((p) => (p.classification || {}).activity === f.activity);
    if (f.origin) list = list.filter((p) => (p.classification || {}).origin === f.origin);
    const sort = f.sort;
    list.sort((a, b) => {
      if (sort === 'pid') return a.pid - b.pid;
      if (sort === 'name') return String(a.name || '').localeCompare(String(b.name || ''), 'zh');
      if (sort === 'cpu') return (b.cpu_seconds || 0) - (a.cpu_seconds || 0);
      return (b.memory_mb || 0) - (a.memory_mb || 0);
    });
    return list;
  }

  function updateProcessView() {
    const list = filteredProcs();
    if (S.proc.selected != null && !list.some((p) => p.pid === S.proc.selected)) S.proc.selected = null;
    const count = $('#proc-count');
    if (count) count.textContent = list.length + ' / ' + S.procs.length + ' 个进程';
    const tm = $('#tm-host');
    if (tm) tm.innerHTML = treemapHtml(list);

    const pageSize = 60;
    const pages = Math.max(1, Math.ceil(list.length / pageSize));
    S.proc.page = Math.min(Math.max(1, S.proc.page), pages);
    const start = (S.proc.page - 1) * pageSize;
    const slice = list.slice(start, start + pageSize);
    const body = $('#proc-body');
    if (body) {
      body.innerHTML = slice.length
        ? slice.map((p) => processRow(p)).join('')
        : '<tr><td colspan="9" class="tbl-empty">没有匹配的进程</td></tr>';
    }
    const pager = $('#proc-pager');
    if (pager) {
      pager.innerHTML =
        '<span>第 ' + S.proc.page + ' / ' + pages + ' 页 · ' + (list.length ? (start + 1) + '–' + (start + slice.length) : '0') + ' / ' + list.length + '</span>' +
        '<div class="pager-btns">' +
          '<button class="btn xs ghost" data-act="proc-page" data-page="' + (S.proc.page - 1) + '"' + (S.proc.page <= 1 ? ' disabled' : '') + '>上一页</button>' +
          '<button class="btn xs ghost" data-act="proc-page" data-page="' + (S.proc.page + 1) + '"' + (S.proc.page >= pages ? ' disabled' : '') + '>下一页</button>' +
        '</div>';
    }
    const side = $('#proc-side');
    if (side) {
      const selected = list.find((p) => p.pid === S.proc.selected);
      side.innerHTML = selected ? processDetail(selected)
        : '<div class="ex-placeholder">点左侧任意一行进程，查看它的归属、职能、来源与中文解释。</div>';
    }
  }

  function patchProcessView() {
    const list = filteredProcs();
    if (S.proc.selected != null && !list.some((p) => p.pid === S.proc.selected)) S.proc.selected = null;
    setText('#proc-count', list.length + ' / ' + S.procs.length + ' 个进程');
    const tm = $('#tm-host');
    if (tm) {
      const sig = list.slice(0, 60).map((p) => p.pid + ':' + Math.round(p.memory_mb || 0)).join(',');
      if (sig !== S.procSig) {
        S.procSig = sig;
        tm.innerHTML = treemapHtml(list);
      }
    }
    const pageSize = 60;
    const pages = Math.max(1, Math.ceil(list.length / pageSize));
    S.proc.page = Math.min(Math.max(1, S.proc.page), pages);
    const start = (S.proc.page - 1) * pageSize;
    const slice = list.slice(start, start + pageSize);
    const body = $('#proc-body');
    if (body) {
      const rows = Array.from(body.children);
      const samePids = slice.length > 0 && rows.length === slice.length &&
        slice.every((p, i) => Number(rows[i].dataset.pid) === p.pid);
      if (samePids) {
        slice.forEach((p, i) => {
          const row = rows[i];
          const cls = S.proc.selected === p.pid ? 'is-selected' : '';
          if (row.className !== cls) row.className = cls;
          const cells = processCells(p);
          if (row.innerHTML !== cells) row.innerHTML = cells;
        });
      } else {
        const html = slice.length
          ? slice.map((p) => processRow(p)).join('')
          : '<tr><td colspan="9" class="tbl-empty">没有匹配的进程</td></tr>';
        if (body.innerHTML !== html) body.innerHTML = html;
      }
    }
    const pager = $('#proc-pager');
    if (pager) {
      const html =
        '<span>第 ' + S.proc.page + ' / ' + pages + ' 页 · ' +
          (list.length ? (start + 1) + '–' + (start + slice.length) : '0') + ' / ' + list.length + '</span>' +
        '<div class="pager-btns">' +
          '<button class="btn xs ghost" data-act="proc-page" data-page="' + (S.proc.page - 1) + '"' + (S.proc.page <= 1 ? ' disabled' : '') + '>上一页</button>' +
          '<button class="btn xs ghost" data-act="proc-page" data-page="' + (S.proc.page + 1) + '"' + (S.proc.page >= pages ? ' disabled' : '') + '>下一页</button>' +
        '</div>';
      if (pager.innerHTML !== html) pager.innerHTML = html;
    }
    const side = $('#proc-side');
    if (side) {
      const selected = list.find((p) => p.pid === S.proc.selected);
      const html = selected ? processDetail(selected)
        : '<div class="ex-placeholder">点左侧任意一行进程，查看它的归属、职能、来源与中文解释。</div>';
      if (side.innerHTML !== html) {
        const top = side.scrollTop;
        side.innerHTML = html;
        side.scrollTop = top;
      }
    }
  }

  async function softProcesses(seq) {
    const procs = await call('process.list', { limit: 0, sort: 'memory' });
    if (!softCurrent('processes', seq)) return;
    S.procs = procs.items || [];
    S.procsTotal = procs.total || S.procs.length;
    patchProcessView();
  }
  function roleTag(role) {
    const kind = ['ad', 'updater', 'telemetry', 'crash'].indexOf(role) >= 0 ? 'amber'
      : role === 'service' ? 'accent'
      : role === 'system' ? 'muted'
      : role === 'main' ? 'sky' : '';
    return tag(ROLE_ZH[role] || role || '未知', kind);
  }

  function activityTag(activity) {
    const kind = activity === 'foreground' ? 'green'
      : activity === 'visible' ? 'sky'
      : activity === 'tray' ? 'amber'
      : activity === 'service_session' ? 'accent' : 'muted';
    return '<span class="tag ' + kind + '"><i class="dot"></i>' + esc(ACTIVITY_ZH[activity] || activity || '—') + '</span>';
  }

  function riskTag(risk) {
    const kind = risk === 'safe' ? 'green' : risk === 'caution' ? 'amber' : risk === 'protected' ? 'red' : 'muted';
    return tag(RISK_ZH[risk] || risk || '—', kind);
  }

  function processCells(p) {
    const c = p.classification || {};
    const marks = [];
    if (c.microsoft) marks.push(tag('微软', 'muted'));
    else if (c.private) marks.push(tag('私自', 'accent'));
    if (c.role === 'service') marks.push(tag('服务', 'sky'));
    if (!marks.length) marks.push('<span class="dim">—</span>');
    return '<td class="pid">' + p.pid + '</td>' +
      '<td class="name">' + esc(p.name) + (p.windows && p.windows.length ? ' ' + tag('窗口', 'muted') : '') + '</td>' +
      '<td>' + roleTag(c.role) + '</td>' +
      '<td>' + activityTag(c.activity) + '</td>' +
      '<td>' + esc(ORIGIN_ZH[c.origin] || c.origin || '—') + '</td>' +
      '<td>' + marks.join(' ') + '</td>' +
      '<td class="num">' + esc(fmtMB(p.memory_mb)) + '</td>' +
      '<td class="ellipsis" title="' + esc(swName(c.software_id)) + '">' + esc(swName(c.software_id)) + '</td>' +
      '<td>' + riskTag(c.risk) + '</td>';
  }

  function processRow(p) {
    const selected = S.proc.selected === p.pid ? ' is-selected' : '';
    return '<tr class="' + selected.trim() + '" data-act="proc-pick" data-pid="' + p.pid + '">' +
      processCells(p) +
    '</tr>';
  }

  function processDetail(p) {
    const c = p.classification || {};
    const reasons = (c.reasons || []).map((r) => '<code>' + esc(r) + '</code>').join('');
    const windows = (p.windows || []).length
      ? '<div class="ci-extra">' + p.windows.slice(0, 4).map((w) => '<code>' + esc(w) + '</code>').join('') + '</div>'
      : '';
    return '<div class="explain">' +
      '<h3>' + esc(p.name) + ' <span class="pid">#' + p.pid + '</span></h3>' +
      '<div class="ex-meta">' + roleTag(c.role) + activityTag(c.activity) + riskTag(c.risk) +
        (c.microsoft ? tag('微软', 'muted') : '') + (c.private ? tag('私自', 'accent') : '') + '</div>' +
      '<div class="ex-text">' + esc(c.explanation || '暂无解释') + '</div>' +
      '<dl class="ex-kv">' +
        '<dt>归属软件</dt><dd>' + esc(swName(c.software_id)) + '</dd>' +
        '<dt>来源</dt><dd>' + esc(ORIGIN_ZH[c.origin] || c.origin || '—') + '</dd>' +
        '<dt>内存</dt><dd>' + esc(fmtMB(p.memory_mb)) + '</dd>' +
        '<dt>父进程</dt><dd>' + esc(p.parent_name || (p.ppid != null ? '#' + p.ppid : '—')) + '</dd>' +
        '<dt>会话 / 用户</dt><dd>' + esc((p.session_id != null ? p.session_id : '—') + ' / ' + (p.user || '—')) + '</dd>' +
        '<dt>置信度</dt><dd>' + (c.confidence != null ? Math.round(c.confidence * 100) + '%' : '—') + '</dd>' +
        '<dt>启动时间</dt><dd>' + esc(p.started_at ? fmtDateTime(p.started_at) : '—') + '</dd>' +
        '<dt>路径</dt><dd class="mono">' + esc(p.exe || '—') + '</dd>' +
      '</dl>' +
      (reasons ? '<div><div class="card-note">判定依据</div><div class="reason-list">' + reasons + '</div></div>' : '') +
      windows +
      '<div class="modal-actions">' +
        '<button class="btn sm" data-act="plan-pid" data-pid="' + p.pid + '">生成该进程的清理计划</button>' +
      '</div>' +
    '</div>';
  }

  function isDark(hex) {
    const text = String(hex || '').replace('#', '');
    if (text.length < 6) return true;
    const r = parseInt(text.slice(0, 2), 16);
    const g = parseInt(text.slice(2, 4), 16);
    const b = parseInt(text.slice(4, 6), 16);
    return (0.299 * r + 0.587 * g + 0.114 * b) < 135;
  }

  function treemapHtml(list) {
    const top = list.slice().sort((a, b) => (b.memory_mb || 0) - (a.memory_mb || 0)).slice(0, 60);
    const items = top.map((p) => ({ value: Math.max(0.01, p.memory_mb || 0), p: p }));
    const total = items.reduce((acc, item) => acc + item.value, 0);
    if (!items.length || total <= 0) return emptyBlock('没有可显示的进程内存数据');
    const W = 1000;
    const H = 300;
    const cells = treemapLayout(items, 0, 0, W, H);
    const html = cells.map((cell) => {
      const p = cell.p;
      const c = p.classification || {};
      const role = c.role || 'unknown';
      const color = TM_COLORS[role] || TM_COLORS.unknown;
      const wpct = Math.max(0, Math.min(100, cell.w / W * 100));
      const hpct = Math.max(0, Math.min(100, cell.h / H * 100));
      const label = wpct > 6 && hpct > 9
        ? esc(String(p.name || '').slice(0, Math.max(3, Math.floor(cell.w / 7))))
        : '';
      const text = p.name + ' · ' + fmtMB(p.memory_mb) + ' · ' + (ROLE_ZH[role] || role);
      return '<button class="tm-cell" style="left:' + cell.x / W * 100 + '%;top:' + cell.y / H * 100 +
        '%;width:' + wpct + '%;height:' + hpct + '%;background:' + color +
        ';color:' + (isDark(color) ? '#fff' : '#101013') + '" data-act="proc-pick" data-pid="' + p.pid +
        '" title="' + esc(text) + '">' + label + '</button>';
    }).join('');
    return '<div class="tm-box">' + html + '</div>';
  }

  function treemapLayout(items, x, y, w, h) {
    const total = items.reduce((acc, item) => acc + Math.max(0, item.value), 0);
    if (total <= 0 || w <= 0 || h <= 0) return [];
    const areas = items.map((item) => Math.max(0, item.value) / total * w * h);
    const out = [];
    let rect = { x: x, y: y, w: w, h: h };
    let index = 0;
    while (index < areas.length) {
      const short = Math.min(rect.w, rect.h);
      let end = index + 1;
      let sum = areas[index];
      let row = [areas[index]];
      while (end < areas.length) {
        const next = row.concat([areas[end]]);
        const nextSum = sum + areas[end];
        if (worst(next, short, nextSum) <= worst(row, short, sum)) {
          row = next;
          sum = nextSum;
          end += 1;
        } else break;
      }
      if (rect.w >= rect.h) {
        const cw = sum / rect.h;
        let oy = rect.y;
        for (let k = 0; k < row.length; k += 1) {
          const ch = row[k] / cw;
          out.push(Object.assign({}, items[index + k], { x: rect.x, y: oy, w: cw, h: ch }));
          oy += ch;
        }
        rect = { x: rect.x + cw, y: rect.y, w: rect.w - cw, h: rect.h };
      } else {
        const ch = sum / rect.w;
        let ox = rect.x;
        for (let k = 0; k < row.length; k += 1) {
          const cw = row[k] / ch;
          out.push(Object.assign({}, items[index + k], { x: ox, y: rect.y, w: cw, h: ch }));
          ox += cw;
        }
        rect = { x: rect.x, y: rect.y + ch, w: rect.w, h: rect.h - ch };
      }
      index = end;
    }
    return out;
  }

  function worst(row, length, sum) {
    if (!row.length || sum <= 0 || length <= 0) return Infinity;
    const max = Math.max.apply(null, row);
    const min = Math.min.apply(null, row);
    return Math.max((length * length * max) / (sum * sum), (sum * sum) / (length * length * min));
  }

  function selectProcess(pid) {
    S.proc.selected = pid;
    $$('#proc-body tr').forEach((row) => {
      row.classList.toggle('is-selected', Number(row.dataset.pid) === pid);
    });
    const found = S.procs.find((p) => p.pid === pid);
    const side = $('#proc-side');
    if (side) side.innerHTML = found ? processDetail(found) : '<div class="ex-placeholder">没有这个进程。</div>';
  }

  function planForPid(pid) {
    go('cleanup', { scope: 'pids', pids: [pid], name: '进程 #' + pid });
  }

  /* ------------------------------------------------------------------ *
   * 清理
   * ------------------------------------------------------------------ */
  async function ensurePlan(key, params) {
    if (S.plan && S.planKey === key && !planExpired(S.plan)) return S.plan;
    const plan = await call('cleanup.plan', params, { timeout: 30000 });
    S.plan = plan;
    S.planKey = key;
    S.planAt = Date.now();
    if (S.cleanup.planId !== plan.plan_id) {
      S.cleanup.planId = plan.plan_id;
      S.cleanup.selected = new Set((plan.items || []).filter((item) => item.default_checked).map((item) => item.pid));
    }
    return plan;
  }

  async function loadCleanup(ret, opts) {
    const o = Object.assign({ scope: 'all' }, opts || {});
    const scope = ['all', 'software', 'pids'].indexOf(o.scope) >= 0 ? o.scope : 'all';
    const key = scope === 'software' ? 'software:' + o.id
      : scope === 'pids' ? 'pids:' + (o.pids || []).join(',')
      : 'all';
    const params = scope === 'software' ? { scope: 'software', software_ids: [o.id] }
      : scope === 'pids' ? { scope: 'pids', pids: o.pids }
      : { scope: 'all' };
    const plan = await ensurePlan(key, params);
    if (!isCurrent('cleanup', ret)) return;
    S.cleanup.plan = plan;
    S.screenOpts = o;
    if (scope === 'pids' && !(plan.items || []).length) {
      toast('该进程不在清理候选池（可能受保护、已退出，或不属于任何软件）', 'warn');
    }
    renderCleanup(plan, o);
    loadCleanupHistory(ret);
  }

  function scopeLabel(o) {
    if (o && o.scope === 'software') return o.name || swName(o.id);
    if (o && o.scope === 'pids') return o.name || ('指定进程 × ' + (o.pids || []).length);
    return '全部软件';
  }

  function kpi(label, value, kind) {
    return '<div class="kpi"><div class="kpi-l">' + esc(label) + '</div>' +
      '<div class="kpi-v ' + (kind || '') + '">' + esc(value) + '</div></div>';
  }

  function renderCleanup(plan, opts) {
    const items = plan.items || [];
    const summary = plan.summary || {};
    const expired = planExpired(plan);
    S.cleanup.selected = new Set([...S.cleanup.selected].filter((pid) =>
      items.some((item) => item.pid === pid)));
    const safeItems = items.filter((item) => item.risk === 'safe');
    const cautionItems = items.filter((item) => item.risk === 'caution');
    const otherItems = items.filter((item) => item.risk !== 'safe' && item.risk !== 'caution');
    const segments = [
      { label: '可安全结束', value: safeItems.length, color: 'var(--sky)' },
      { label: '需确认', value: cautionItems.length, color: 'var(--amber)' },
      { label: '受保护', value: (plan.protected_skipped || []).length, color: '#55555f' }
    ];
    const dry = !!(S.status && S.status.dry_run);
    const elevated = !!(S.status && S.status.elevated);
    const warning = expired
      ? '<div class="plan-warn err"><b>计划已过期</b><span>通知已失效；重新生成后才能执行。</span>' +
        '<span class="spacer"></span><button class="btn sm danger" data-act="plan-fresh">重新生成</button></div>'
      : !elevated
        ? '<div class="plan-warn err"><b>只读模式</b>' +
          '<span>当前守护是普通用户：可以查看和演练，真实结束进程 / 停服务不可用。</span>' +
          '<span class="spacer"></span><button class="btn sm danger" data-act="show-admin-help">如何获得管理员</button></div>'
        : dry
          ? '<div class="plan-warn"><b>管理员已就绪 · 演练模式</b>' +
            '<span>执行时只写审计：不会发关闭消息、不会结束进程、不会停止服务。</span>' +
            '<span class="spacer"></span><button class="btn sm" data-act="nav" data-screen="settings">去设置关闭演练</button></div>'
          : '<div class="plan-warn err"><b>实盘模式</b><span>执行会真正结束勾选的进程 / 停止服务。请确认计划内容。</span></div>';

    const protectedList = plan.protected_skipped || [];
    const protectedTags = protectedList.slice(0, 14).map((item) =>
      tag(item.name + ' #' + item.pid + ' · ' + (item.why || '受保护'), 'muted')).join('');
    const protectedMore = protectedList.length > 14 ? tag('+' + (protectedList.length - 14) + ' 项', 'muted') : '';

    $('#screen').innerHTML =
      '<div class="screen-inner enter">' +
        '<div class="plan-head">' +
          '<div class="plan-title"><div class="kicker">SURGERY / 04</div><h2>清理计划</h2>' +
            '<div class="plan-id">' + esc(plan.plan_id) + ' · ' + esc(fmtDateTime(plan.ts)) + '</div></div>' +
          '<div class="plan-meta">' +
            '<div class="pm"><span>范围</span><b>' + esc(scopeLabel(opts)) + '</b></div>' +
            '<div class="pm"><span>剩余</span><b id="plan-countdown" class="' + (expired ? 'danger' : '') + '">--:--</b></div>' +
            '<div class="pm"><span>候选</span><b>' + items.length + '</b></div>' +
          '</div>' +
          '<div class="plan-actions"><button class="btn ghost" data-act="plan-fresh">重新生成</button></div>' +
        '</div>' +
        warning +
        '<div class="grid g2">' +
          card('PLAN / SUMMARY', '计划概览',
            '<div class="plan-summary">' +
              donut(segments, { centerValue: items.length, centerLabel: '候选进程' }) +
              '<div class="plan-kpis">' +
                kpi('可安全结束', summary.safe || 0, 'sky') +
                kpi('需确认', summary.caution || 0, 'amber') +
                kpi('默认勾选', summary.default_checked || 0, 'green') +
                kpi('停止服务', summary.stop_services || 0, '') +
                kpi('默认释放', fmtMB(summary.default_memory_mb || 0), 'green') +
                kpi('受保护', summary.protected || 0, 'red') +
              '</div>' +
            '</div>') +
          card('SELECTION', '执行选择',
            '<div class="exec-bar">' +
              '<div class="exec-info">已选 <b id="sel-count">0</b> 项 · 预计释放 <b id="sel-mem">0 MB</b> · <span id="sel-services">0</span> 个服务</div>' +
              '<button class="btn sm" data-act="sel-default">全选默认</button>' +
              '<button class="btn sm ghost" data-act="sel-clear">清空</button>' +
              '<button class="btn ' + (dry ? 'primary' : 'danger') + '" id="exec-count" data-act="execute">执行选中</button>' +
            '</div>' +
            '<div class="note" style="margin-top:10px">执行只处理你勾选的 PID；默认不连带子进程。' +
              '服务行是「停止服务」，只改运行状态、不改启动类型。</div>') +
        '</div>' +
        '<div class="plan-groups">' +
          cleanupGroup('默认勾选 · 可安全结束', safeItems, 'sky') +
          cleanupGroup('需确认 · 默认不勾', cautionItems, 'amber') +
          cleanupGroup('其他候选', otherItems, 'muted') +
        '</div>' +
        '<div class="grid g2">' +
          card('GUARD', '受保护（不可执行）',
            protectedList.length
              ? '<div class="protected-strip">' + protectedTags + protectedMore + '</div>'
              : '<div class="card-note">没有受保护对象。</div>') +
          card('HISTORY', '最近执行',
            '<div id="cleanup-history"><div class="tbl-empty">读取中…</div></div>') +
        '</div>' +
      '</div>';
    updateSelectionUI();
    tick();
  }

  function cleanupGroup(title, items, dotKind) {
    if (!items.length) return '';
    const note = dotKind === 'amber' ? '默认不勾选，逐项确认' : (dotKind === 'sky' ? '逐项可改选' : '不在默认范围');
    return '<section class="group">' +
      '<header class="group-h"><i class="dot ' + dotKind + '"></i><h3>' + esc(title) + '</h3>' +
        '<span class="card-note">' + note + '</span>' +
        '<span class="n">' + items.length + ' 项</span></header>' +
      items.map((item) => cleanupItemRow(item)).join('') +
    '</section>';
  }

  function cleanupItemRow(item) {
    const checked = S.cleanup.selected.has(item.pid);
    const actionTag = item.action === 'stop_service'
      ? tag('停止服务', 'amber')
      : tag('结束进程', 'accent');
    const extras = [];
    (item.services || []).forEach((name) => extras.push('<code>' + esc(name) + '</code>'));
    if (item.windows && item.windows.length) extras.push('<code>窗口：' + esc(item.windows[0]) + '</code>');
    return '<div class="ci-row ' + (checked ? 'is-checked' : '') + '" data-act="ci-toggle" data-pid="' + item.pid + '">' +
      '<span class="check" role="checkbox" aria-checked="' + (checked ? 'true' : 'false') + '" aria-label="选择 ' + esc(item.name) + '"></span>' +
      '<div class="ci-main">' +
        '<div class="ci-name"><b>' + esc(item.name) + '</b><span class="pid">#' + item.pid + '</span>' +
          actionTag + (item.private ? tag('私自', 'accent') : '') + (item.microsoft ? tag('微软', 'muted') : '') + '</div>' +
        '<div class="ci-meta">' + esc(item.software || '未归属') + ' · ' +
          esc(ROLE_ZH[item.role] || item.role || '未知') + ' · ' +
          esc(ORIGIN_ZH[item.origin] || item.origin || '未知') + ' · ' + esc(fmtMB(item.memory_mb)) + '</div>' +
        '<div class="ci-reason">' + esc(item.reason || '') + '</div>' +
        (extras.length ? '<div class="ci-extra">' + extras.join('') + '</div>' : '') +
      '</div>' +
    '</div>';
  }

  function updateSelectionUI() {
    const plan = S.cleanup.plan;
    if (!plan) return;
    $$('.ci-row').forEach((row) => {
      const pid = Number(row.dataset.pid);
      const on = S.cleanup.selected.has(pid);
      row.classList.toggle('is-checked', on);
      const box = row.querySelector('.check');
      if (box) box.setAttribute('aria-checked', on ? 'true' : 'false');
    });
    updateExecBar();
  }

  function updateExecBar() {
    const plan = S.cleanup.plan;
    if (!plan) return;
    const items = plan.items || [];
    const selected = items.filter((item) => S.cleanup.selected.has(item.pid));
    const memory = selected.reduce((acc, item) => acc + (item.memory_mb || 0), 0);
    const services = selected.filter((item) => item.action === 'stop_service').length;
    const countNode = $('#sel-count');
    const memNode = $('#sel-mem');
    const servicesNode = $('#sel-services');
    if (countNode) countNode.textContent = String(selected.length);
    if (memNode) memNode.textContent = fmtMB(memory);
    if (servicesNode) servicesNode.textContent = String(services);
    const button = $('#exec-count');
    if (button) {
      const dry = !!(S.status && S.status.dry_run);
      button.textContent = (dry ? '演练执行' : '执行选中') + ' (' + selected.length + ')';
      button.disabled = selected.length === 0 || planExpired(plan);
    }
  }

  function toggleItem(pid) {
    if (S.cleanup.selected.has(pid)) S.cleanup.selected.delete(pid);
    else S.cleanup.selected.add(pid);
    updateSelectionUI();
  }

  function selectDefault() {
    const plan = S.cleanup.plan;
    if (!plan) return;
    S.cleanup.selected = new Set((plan.items || []).filter((item) => item.default_checked).map((item) => item.pid));
    updateSelectionUI();
  }

  function freshPlan() {
    S.plan = null;
    S.planKey = '';
    S.cleanup.planId = '';
    const o = S.screenOpts || { scope: 'all' };
    go('cleanup', o.scope ? o : { scope: 'all' });
  }

  async function executeCleanup() {
    const plan = S.cleanup.plan;
    if (!plan) return;
    if (planExpired(plan)) {
      toast('计划已过期，正在重新生成…', 'warn');
      freshPlan();
      return;
    }
    const selected = (plan.items || []).filter((item) => S.cleanup.selected.has(item.pid));
    if (!selected.length) {
      toast('先勾选要处理的进程', 'warn');
      return;
    }
    const pids = selected.map((item) => item.pid).sort((a, b) => a - b);
    const services = selected.filter((item) => item.action === 'stop_service').length;
    const real = !!(S.status && S.status.dry_run === false);
    if (real && !(S.status && S.status.elevated)) {
      toast('当前守护是普通用户，不能实盘执行；请先获得管理员权限。', 'warn');
      showAdminHelp();
      return;
    }
    const ok = await confirmDialog({
      title: real ? '实盘执行清理' : '演练执行清理',
      body: '<p>计划 <code>' + esc(plan.plan_id) + '</code> 中将处理：</p>' +
        '<p><b>' + selected.length + '</b> 个进程 / <b>' + services + '</b> 个服务，' +
        '预计释放 <b>' + esc(fmtMB(selected.reduce((acc, item) => acc + (item.memory_mb || 0), 0))) + '</b>。</p>' +
        (real ? '<p class="warn">实盘模式：会真正结束进程 / 停止服务，不可撤销。</p>'
              : '<p class="muted">演练模式：只写审计，不会真正动作。</p>'),
      confirmText: real ? '确认执行' : '演练执行',
      danger: real
    });
    if (!ok) return;
    const button = $('#exec-count');
    if (button) {
      button.disabled = true;
      button.textContent = '执行中…';
    }
    try {
      const result = await call('cleanup.execute', {
        plan_id: plan.plan_id,
        pids: pids,
        timeout_ms: 600000
      }, { timeout: 620000 });
      S.plan = null;
      S.planKey = '';
      S.cleanup.plan = null;
      S.cleanup.selected = new Set();
      showResult(result);
      refreshStatus().catch(() => {});
    } catch (err) {
      if (err.code === 'E_PLAN_EXPIRED' || err.code === 'E_PLAN_STALE') {
        toast('计划已失效，正在重新生成…', 'warn');
        S.plan = null;
        S.planKey = '';
        refreshCurrent();
      } else {
        handleError(err);
        if (button) {
          button.disabled = false;
          updateExecBar();
        }
      }
    }
  }

  function showResult(result) {
    const killed = result.killed || [];
    const failed = result.failed || [];
    const all = killed.concat(failed);
    const rows = all.length ? all.map((item) =>
      '<tr><td class="name">' + esc(item.name) + '</td><td class="pid">' + item.pid + '</td>' +
        '<td>' + (item.action === 'stop_service' ? '停止服务' : '结束进程') + '</td>' +
        '<td>' + (item.ok ? tag('成功', 'green') : tag('失败', 'red')) + '</td>' +
        '<td>' + esc(item.mode || '—') + '</td>' +
        '<td class="wrap">' + esc(item.error || '') + '</td></tr>').join('')
      : '<tr><td colspan="6" class="tbl-empty">没有实际动作</td></tr>';
    const body = '<div class="plan-warn ' + (result.dry_run ? '' : 'err') + '">' +
        '<b>' + (result.dry_run ? '演练完成' : '执行完成') + '</b>' +
        '<span>成功 ' + killed.length + ' · 失败 ' + failed.length + ' · 释放 ' + esc(fmtMB(result.freed_mb || 0)) + '</span></div>' +
      '<div class="modal-section"><h3>逐项结果</h3><div class="table-wrap"><table class="tbl">' +
        '<thead><tr><th>进程</th><th>PID</th><th>动作</th><th>结果</th><th>方式</th><th>错误</th></tr></thead>' +
        '<tbody>' + rows + '</tbody></table></div></div>' +
      (result.audit_seq_from != null
        ? '<div class="card-note">审计序号：' + result.audit_seq_from + ' – ' + (result.audit_seq_to != null ? result.audit_seq_to : '') + '</div>'
        : '');
    openModal('执行结果', body, {
      wide: true,
      footer: '<button class="btn ghost" data-act="result-audit">查看审计</button>' +
        '<button class="btn primary" data-act="result-done">完成</button>',
      onClose: () => { if (S.screen === 'cleanup') freshPlan(); }
    });
  }

  async function loadCleanupHistory(ret) {
    try {
      const res = await call('cleanup.history', { limit: 8 });
      if (!isCurrent('cleanup', ret)) return;
      const host = $('#cleanup-history');
      if (!host) return;
      const rows = res.items || [];
      if (!rows.length) {
        host.innerHTML = '<div class="tbl-empty">还没有执行记录</div>';
        return;
      }
      host.innerHTML = '<div class="table-wrap"><table class="tbl">' +
        '<thead><tr><th>时间</th><th>计划</th><th class="num">成功</th><th class="num">释放</th><th>模式</th></tr></thead><tbody>' +
        rows.map((row) => '<tr><td>' + esc(fmtDateTime(row.ts)) + '</td>' +
          '<td class="mono ellipsis">' + esc(row.plan_id || '—') + '</td>' +
          '<td class="num">' + (row.killed || 0) + '</td>' +
          '<td class="num">' + esc(fmtMB(row.freed_mb || 0)) + '</td>' +
          '<td>' + (row.dry_run ? tag('演练', 'accent') : tag('实盘', 'red')) + '</td></tr>').join('') +
        '</tbody></table></div>';
    } catch (err) {
      /* 历史读取失败不影响主流程 */
    }
  }

  /* ------------------------------------------------------------------ *
   * 审计
   * ------------------------------------------------------------------ */
  async function loadAudit(ret) {
    const res = await call('audit.tail', { n: S.audit.limit });
    if (!isCurrent('audit', ret)) return;
    /* 守护的 tail 按时间正序返回最后 N 条；UI 要“最新在上”。 */
    S.audit.rows = (res.rows || []).slice().reverse();
    renderAuditScreen();
  }

  function eventTag(event) {
    const kind = event.indexOf('cleanup.') === 0 ? 'accent'
      : event.indexOf('daemon.') === 0 ? 'sky'
      : event.indexOf('kb.') === 0 ? 'green'
      : event.indexOf('policy.') === 0 ? 'amber'
      : event.indexOf('process.') === 0 || event.indexOf('service.') === 0 ? 'red' : 'muted';
    return tag(EVENT_ZH[event] || event, kind);
  }

  function resultTag(value) {
    const text = String(value == null ? '' : value).trim();
    const low = text.toLowerCase();
    if (low === 'ok' || low === 'success' || low === '成功') return tag('成功', 'green');
    if (low === 'failed' || low === 'error' || low === '失败') return tag('失败', 'red');
    if (low === '部分' || low === 'partial') return tag('部分', 'amber');
    if (low === 'skipped' || low === 'skip' || low === '跳过') return tag('跳过', 'muted');
    if (low === 'begin' || low === 'started' || low === '开始') return tag('开始', 'accent');
    return text ? tag(text, 'muted') : '';
  }

  function renderAuditScreen() {
    const events = Array.from(new Set(S.audit.rows.map((row) => row.event))).sort();
    if (S.audit.event && events.indexOf(S.audit.event) < 0) S.audit.event = '';
    const eventOptions = [['', '全部事件']].concat(events.map((event) => [event, EVENT_ZH[event] || event]));
    $('#screen').innerHTML =
      '<div class="screen-inner enter">' +
        '<div class="toolbar">' +
          '<select class="select" id="audit-event">' + optionList(eventOptions, S.audit.event) + '</select>' +
          '<select class="select" id="audit-limit">' + optionList([
            ['100', '最近 100 条'], ['200', '最近 200 条'], ['500', '最近 500 条'], ['1000', '最近 1000 条']
          ], String(S.audit.limit)) + '</select>' +
          '<button class="btn sm ghost" data-act="refresh">刷新</button>' +
          '<span class="spacer"></span>' +
          '<span class="count" id="audit-count"></span>' +
        '</div>' +
        card('AUDIT / 05', '审计日志',
          '<div class="table-wrap"><table class="tbl">' +
            '<thead><tr><th>序号</th><th>时间</th><th>事件</th><th>目标</th><th>软件</th><th>动作 / 结果</th><th>模式</th></tr></thead>' +
            '<tbody id="audit-body"></tbody></table></div>') +
      '</div>';
    applyAuditFilter();
  }

  function auditRowHtml(row) {
    return '<tr data-act="audit-detail" data-seq="' + row.seq + '">' +
      '<td class="pid">' + row.seq + '</td>' +
      '<td>' + esc(fmtDateTime(row.ts)) + '</td>' +
      '<td>' + eventTag(row.event) + '</td>' +
      '<td class="name">' + (row.pid != null ? '#' + row.pid + ' ' : '') + esc(row.name || '—') + '</td>' +
      '<td class="ellipsis">' + esc(row.software_id ? swName(row.software_id) : '—') + '</td>' +
      '<td>' + esc(ACTION_ZH[row.action] || row.action || '') + ' ' + resultTag(row.result) + '</td>' +
      '<td>' + (row.dry_run ? tag('演练', 'accent') : tag('实盘', 'muted')) + '</td>' +
    '</tr>';
  }

  function auditCount(rows) {
    const visible = S.audit.event ? rows.filter((row) => row.event === S.audit.event) : rows;
    setText('#audit-count', visible.length + ' / ' + rows.length + ' 条');
  }

  function applyAuditFilter() {
    const rows = S.audit.event
      ? S.audit.rows.filter((row) => row.event === S.audit.event)
      : S.audit.rows;
    auditCount(S.audit.rows);
    const body = $('#audit-body');
    if (!body) return;
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="7" class="tbl-empty">没有匹配的审计记录</td></tr>';
      return;
    }
    body.innerHTML = rows.map((row) => auditRowHtml(row)).join('');
  }

  async function softAudit(seq) {
    const res = await call('audit.tail', { n: S.audit.limit });
    if (!softCurrent('audit', seq)) return;
    const rows = (res.rows || []).slice().reverse();
    const oldTop = S.audit.rows.length ? S.audit.rows[0].seq : 0;
    S.audit.rows = rows;
    if (S.auditForce) {
      S.auditForce = false;
      applyAuditFilter();
      return;
    }
    auditCount(rows);
    if (S.audit.event || !oldTop || !$('#audit-body')) return;
    const fresh = rows.filter((row) => row.seq > oldTop);
    if (!fresh.length) return;
    const host = $('#screen');
    const body = $('#audit-body');
    /* 用户滚下去了就不要把内容往上顶；只在顶部时插入新行。 */
    if (!host || host.scrollTop > 40 || !body) return;
    const empty = body.querySelector('.tbl-empty');
    if (empty) empty.remove();
    body.insertAdjacentHTML('afterbegin', fresh.map((row) => auditRowHtml(row)).join(''));
    while (body.children.length > S.audit.limit) body.lastElementChild.remove();
  }

  function openAuditDetail(seq) {
    const row = S.audit.rows.find((item) => item.seq === seq);
    if (!row) return;
    S.audit.detailJson = JSON.stringify(row, null, 2);
    openModal('审计 #' + seq, '<pre>' + esc(S.audit.detailJson) + '</pre>', {
      wide: true,
      footer: '<button class="btn ghost" data-act="copy-json">复制 JSON</button>' +
        '<button class="btn primary" data-act="modal-close">关闭</button>'
    });
  }

  /* ------------------------------------------------------------------ *
   * 设置
   * ------------------------------------------------------------------ */
  async function loadSettings(ret) {
    const [policy, rules, kb, st] = await Promise.all([
      call('policy.get'),
      call('classify.rules'),
      call('kb.stats'),
      call('status.get')
    ]);
    if (!isCurrent('settings', ret)) return;
    S.policy = policy;
    S.rules = rules;
    S.kb = kb;
    S.status = st;
    S.lastUpdate = Date.now();
    if (typeof st.event_seq === 'number') S.seq = Math.max(S.seq, st.event_seq);
    renderSettingsScreen(policy, rules, kb, st);
    updateChrome();
  }

  function setRow(title, desc, control) {
    return '<div class="set-row"><div class="set-main"><b>' + esc(title) + '</b><p>' + esc(desc) + '</p></div>' +
      '<div class="set-ctl">' + control + '</div></div>';
  }

  function policyCard(policy) {
    const roles = (policy.never_touch_roles || []).map((role) => tag(ROLE_ZH[role] || role, 'muted')).join('');
    return card('01 / POLICY', '策略',
      setRow('演练模式（dry_run）',
        '开启时只写审计：不发 WM_CLOSE、不结束进程、不停服务。关闭前会二次确认。',
        '<button class="switch ' + (policy.dry_run ? 'on' : '') + '" data-act="toggle-dryrun" role="switch" aria-checked="' + (policy.dry_run ? 'true' : 'false') + '" aria-label="演练模式"><i></i></button>') +
      setRow('离开时也允许清理',
        '默认否：用户离开（idle）时不自动清理。本工具始终需要用户点选。',
        '<button class="switch ' + (policy.allow_cleanup_while_idle ? 'on' : '') + '" data-act="toggle-idle" role="switch" aria-checked="' + (policy.allow_cleanup_while_idle ? 'true' : 'false') + '" aria-label="离开时允许清理"><i></i></button>') +
      setRow('“刚启动算在用”窗口',
        '进程启动后的多少秒内视为“用户刚打开”，用于“在用”判定。单位：秒。',
        '<input class="input set-input" id="min-idle" type="number" min="0" max="86400" step="10" value="' + esc(policy.min_idle_s) + '">' +
        '<button class="btn sm" data-act="save-min-idle">保存</button>') +
      setRow('永不触碰的角色',
        '这些角色的进程不会进入任何清理计划。配置在 config.toml 的 [policy]。',
        '<div class="chips">' + (roles || tag('（空）', 'muted')) + '</div>') +
      setRow('重载配置',
        '重新读取 config.toml 与知识库（不重启守护）。',
        '<button class="btn sm" data-act="reload-config">重载</button>'),
      '<span class="card-note">策略保存即刻生效</span>');
  }

  function protectCard(policy) {
    const patterns = policy.protect || [];
    const list = patterns.length
      ? patterns.map((pattern) =>
          '<div class="protect-item"><span class="mono" title="' + esc(pattern) + '">' + esc(pattern) + '</span>' +
            '<button class="btn xs ghost" data-act="protect-remove" data-pattern="' + esc(pattern) + '">移除</button></div>').join('')
      : '<div class="card-note">保护名单为空。</div>';
    return card('02 / PROTECTION', '保护名单',
      '<div class="protect-list">' + list + '</div>' +
      '<div class="protect-add">' +
        '<select class="select" id="protect-kind">' +
          '<option value="sw">软件 ID</option><option value="pid">PID</option><option value="exe">exe 路径</option>' +
        '</select>' +
        '<input class="input" id="protect-value" placeholder="例如 androiddevice / 1234 / E:\\app\\x.exe">' +
        '<button class="btn sm primary" data-act="protect-add">加入</button>' +
      '</div>' +
      '<div class="note" style="margin-top:10px">保护名单命中的对象<b>永远不出现在任何计划里</b>；写入 state.json，立即生效。</div>');
  }

  function kbCard(rules, kb) {
    const top = (kb.top || []).slice(0, 6);
    const topHtml = top.length
      ? top.map((row) => '<div class="rule-row"><code>' + esc(row.name || '—') + '</code>' +
          '<span>' + esc(ROLE_ZH[row.role] || row.role || '') + ' × ' + (row.hit_count || 1) + '</span></div>').join('')
      : '<div class="card-note">没有待办条目。</div>';
    const rulesPreview = (rules.rules || []).slice(0, 6).map((rule) =>
      '<div class="rule-row"><code>' + esc(rule.match || rule.id) + '</code>' +
        '<span>' + esc(ROLE_ZH[rule.role] || rule.role || '') + '</span></div>').join('');
    return card('03 / KNOWLEDGE', '知识库',
      '<dl class="kb-facts">' +
        '<dt>版本</dt><dd class="mono">' + esc(kb.kb_version || rules.kb_version || '—') + '</dd>' +
        '<dt>规则数</dt><dd>' + (kb.kb_size != null ? kb.kb_size : rules.kb_size || 0) + ' 条（内置 ' + (rules.builtin_size || 0) + '）</dd>' +
        '<dt>知识库路径</dt><dd class="mono">' + esc(kb.kb_path || rules.kb_path || '—') + '</dd>' +
        '<dt>待办条目</dt><dd>' + (kb.count || 0) + ' 条 · ' + (kb.pending_enabled ? '已启用采集' : '已关闭采集') + '</dd>' +
        '<dt>待办文件</dt><dd class="mono">' + esc(kb.path || '—') + '</dd>' +
      '</dl>' +
      '<div class="card-note" style="margin-bottom:6px">待办 TOP</div>' +
      '<div class="rule-list">' + topHtml + '</div>' +
      '<div class="card-note" style="margin-bottom:6px">规则预览</div>' +
      '<div class="rule-list">' + (rulesPreview || '<div class="card-note">没有外部规则。</div>') + '</div>' +
      '<div class="modal-actions">' +
        '<button class="btn sm" data-act="kb-reload">重载知识库</button>' +
        '<button class="btn sm ghost" data-act="kb-review">生成待办包</button>' +
      '</div>' +
      '<div class="note" style="margin-top:10px">补丁<b>不会自动应用</b>：需要在 CLI 里 <code>kb apply</code> 显式确认（默认 dry-run、写前备份）。</div>');
  }

  function daemonCard(st) {
    const warnings = st.warnings || {};
    const items = [];
    (warnings.kb || []).forEach((text) => items.push(text));
    if (warnings.state) items.push(warnings.state);
    if (warnings.collect) items.push(warnings.collect);
    const warningHtml = items.length
      ? '<div class="warn-list">' + items.map((text) => '<div class="warn-item">' + esc(text) + '</div>').join('') + '</div>'
      : '';
    return card('04 / DAEMON', '守护信息',
      '<dl class="kb-facts">' +
        '<dt>健康</dt><dd>' + (st.health === 'ok' ? tag('OK', 'green') : tag(String(st.health || 'unknown').toUpperCase(), 'amber')) + '</dd>' +
        '<dt>权限</dt><dd>' + (st.elevated ? tag('管理员', 'accent') : tag('普通用户 / 降级', 'amber')) + '</dd>' +
        '<dt>实盘可用</dt><dd>' + (st.elevated && st.dry_run === false
          ? tag('是', 'green') : tag('否（需管理员 + 关闭演练）', 'amber')) + '</dd>' +
        '<dt>运行时长</dt><dd>' + esc(fmtDuration(st.uptime_s || 0)) + '</dd>' +
        '<dt>IPC</dt><dd class="mono">' + esc(ipcLabel(st.ipc)) + '</dd>' +
        '<dt>事件序号</dt><dd class="mono">#' + S.seq + '</dd>' +
        '<dt>软件 / 进程</dt><dd>' + (st.counts ? (st.counts.software || 0) + ' / ' + (st.counts.processes || 0) : '—') + '</dd>' +
      '</dl>' +
      (st.degraded_reason ? '<div class="warn-list"><div class="warn-item">' + esc(st.degraded_reason) + '</div></div>' : '') +
      warningHtml +
      '<div class="modal-actions" style="margin-top:10px">' +
        '<button class="btn sm" data-act="test-conn">测试连接</button>' +
        (st.elevated ? '' : '<button class="btn sm danger" data-act="show-admin-help">如何获得管理员</button>') +
        '<button class="btn sm ghost" data-act="refresh">刷新</button>' +
      '</div>' +
      '<div class="note" style="margin-top:10px">浏览器前端以<b>普通用户</b>通过本地网关访问守护；真实清理权限仍由守护判定。</div>');
  }

  function renderSettingsScreen(policy, rules, kb, st) {
    $('#screen').innerHTML =
      '<div class="screen-inner enter"><div class="settings-grid">' +
        policyCard(policy) + protectCard(policy) + kbCard(rules, kb) + daemonCard(st) +
      '</div></div>';
  }

  async function savePolicy(patch) {
    try {
      setBusy(true);
      const res = await call('policy.set', patch);
      S.policy = res;
      toast('策略已更新：' + ((res.changed || []).join(', ') || '无变化'), 'ok');
      await refreshStatus().catch(() => {});
      refreshCurrent();
    } catch (err) {
      handleError(err);
    } finally {
      setBusy(false);
    }
  }

  async function toggleDryRun() {
    const current = !!(S.policy && S.policy.dry_run);
    if (current && !(S.status && S.status.elevated)) {
      toast('当前是普通用户守护，关闭演练也无法真正清理；先获得管理员权限。', 'warn');
      showAdminHelp();
      return;
    }
    if (current) {
      const ok = await confirmDialog({
        title: '关闭演练模式',
        body: '<p>关闭后，<b>执行清理会真正结束进程 / 停止服务</b>。</p>' +
          '<p class="muted">建议先用演练模式核对计划内容。</p>',
        confirmText: '关闭演练',
        danger: true
      });
      if (!ok) return;
    }
    await savePolicy({ dry_run: !current });
  }

  async function saveMinIdle() {
    const node = $('#min-idle');
    if (!node) return;
    const value = Number(node.value);
    if (!Number.isInteger(value) || value < 0) {
      toast('请输入非负整数（秒）', 'warn');
      return;
    }
    await savePolicy({ min_idle_s: value });
  }

  async function protectAdd() {
    const kindNode = $('#protect-kind');
    const valueNode = $('#protect-value');
    if (!kindNode || !valueNode) return;
    const kind = kindNode.value;
    const value = String(valueNode.value || '').trim();
    if (!value) {
      toast('先填一个保护对象', 'warn');
      return;
    }
    let params = null;
    if (kind === 'pid') {
      const pid = Number(value);
      if (!Number.isInteger(pid) || pid <= 0) {
        toast('PID 必须是正整数', 'warn');
        return;
      }
      params = { pid: pid };
    } else if (kind === 'sw') {
      params = { software_id: value };
    } else {
      params = { exe: value };
    }
    try {
      const res = await call('protect.add', params);
      toast('已加入保护名单（当前 ' + (res.protect || []).length + ' 项）', 'ok');
      refreshCurrent();
    } catch (err) {
      handleError(err);
    }
  }

  async function protectRemove(pattern) {
    if (!pattern) return;
    try {
      await call('protect.remove', { pattern: pattern });
      toast('已移出保护名单', 'ok');
      refreshCurrent();
    } catch (err) {
      handleError(err);
    }
  }

  async function protectSoftware(id) {
    if (!id) return;
    try {
      await call('protect.add', { software_id: id });
      toast('已加入保护名单', 'ok');
      closeModal(true);
      if (S.screen === 'software') updateSoftwareGrid();
      if (S.screen === 'settings') refreshCurrent();
    } catch (err) {
      handleError(err);
    }
  }

  async function reloadConfig() {
    try {
      const res = await call('daemon.reload');
      toast('配置已重载（KB ' + (res.kb_version || '?') + (res.warnings && res.warnings.length ? '，' + res.warnings.length + ' 条告警' : '') + '）', 'ok');
      refreshStatus().catch(() => {});
      refreshCurrent();
    } catch (err) {
      handleError(err);
    }
  }

  async function kbReload() {
    try {
      const res = await call('classify.reload');
      toast('知识库已重载：' + (res.kb_version || '?') + '（' + (res.kb_size || 0) + ' 条）', 'ok');
      refreshCurrent();
    } catch (err) {
      handleError(err);
    }
  }

  async function kbReview() {
    try {
      const res = await call('kb.review');
      toast('待办包已生成' + (res.dir ? '：' + res.dir : ''), 'ok');
      refreshCurrent();
    } catch (err) {
      handleError(err);
    }
  }

  async function testConn() {
    try {
      await call('ping');
      toast('网关与守护连接正常', 'ok');
    } catch (err) {
      handleError(err);
    }
  }

  /* ------------------------------------------------------------------ *
   * 弹窗 / toast / busy
   * ------------------------------------------------------------------ */
  function openModal(title, body, opts) {
    const options = opts || {};
    S.modalOpen = true;
    S.modalOnClose = typeof options.onClose === 'function' ? options.onClose : null;
    $('#modal-root').innerHTML =
      '<div class="modal-mask" data-act="modal-mask">' +
        '<div class="modal ' + (options.wide ? 'wide' : '') + '" role="dialog" aria-modal="true" aria-label="' + esc(title) + '">' +
          '<header class="modal-h"><h2 id="modal-title">' + esc(title) + '</h2>' +
            '<button class="btn ghost sm" data-act="modal-close" aria-label="关闭">✕</button></header>' +
          '<div class="modal-b" id="modal-body">' + body + '</div>' +
          (options.footer ? '<footer class="modal-f">' + options.footer + '</footer>' : '') +
        '</div>' +
      '</div>';
  }

  function closeModal(silent) {
    if (!S.modalOpen) return;
    if (!silent && S.confirmResolve) {
      const resolve = S.confirmResolve;
      S.confirmResolve = null;
      resolve(false);
    }
    const onClose = S.modalOnClose;
    S.modalOnClose = null;
    S.modalOpen = false;
    $('#modal-root').innerHTML = '';
    if (onClose) {
      try { onClose(); } catch (err) { /* 关闭回调不允许影响界面 */ }
    }
  }

  function showAdminHelp() {
    const st = S.status || {};
    const current = st.elevated ? '管理员' : '普通用户';
    const mode = st.dry_run ? '演练模式' : '实盘模式';
    const body =
      '<p>当前守护：<b>' + esc(current) + '</b> · <b>' + esc(mode) + '</b>。</p>' +
      '<p>“真实结束进程 / 停服务”需要<b>管理员守护</b>；浏览器和网关始终以普通用户运行，' +
        '不会自己提权。</p>' +
      '<div class="modal-section"><h3>方式一：一键（推荐）</h3>' +
        '<p>双击项目目录里的 <code>AppGuard-admin.bat</code>，在 UAC 弹窗选“是”。' +
        '它会停掉普通守护、注册登录任务，并立刻启动管理员守护。</p>' +
        '<pre>' + esc('E:\\小工具\\app_guard\\AppGuard-admin.bat') + '</pre>' +
      '</div>' +
      '<div class="modal-section"><h3>方式二：手动</h3>' +
        '<p>以管理员身份打开 PowerShell，然后执行：</p>' +
        '<pre>' + esc('cd /d E:\\小工具\\app_guard\npython tools\\guardctl.py stop\npython tools\\guardctl.py serve --transport auto') + '</pre>' +
        '<p>保持该窗口运行，再双击 <code>AppGuard.bat</code> 打开界面。</p>' +
      '</div>' +
      '<div class="modal-section"><h3>方式三：安装登录任务</h3>' +
        '<p>右键 <code>install.bat</code> → “以管理员身份运行”。新版本会立即启动管理员守护，' +
        '并注册登录任务。</p>' +
      '</div>' +
      '<div class="note">关闭“演练模式”后才会真正结束进程/停服务；' +
        '如果守护仍是普通用户，执行会被守护以 <code>E_PERMISSION</code> 拒绝。</div>';
    openModal('获得管理员权限', body, {
      wide: true,
      footer: '<button class="btn primary" data-act="modal-close">知道了</button>'
    });
  }

  function confirmDialog(options) {
    return new Promise((resolve) => {
      S.confirmResolve = resolve;
      openModal(options.title || '确认', options.body || '', {
        footer: '<button class="btn ghost" data-act="modal-cancel">取消</button>' +
          '<button class="btn ' + (options.danger ? 'danger' : 'primary') + '" data-act="modal-confirm">' +
          esc(options.confirmText || '确认') + '</button>'
      });
    });
  }

  function toast(message, kind) {
    const node = $('#toast');
    if (!node) return;
    node.textContent = String(message || '');
    node.className = 'toast show ' + (kind || '');
    clearTimeout(S.toastTimer);
    S.toastTimer = setTimeout(() => {
      node.className = 'toast ' + (kind || '');
    }, 4600);
  }

  function setBusy(value) {
    S.busy = !!value;
    document.body.classList.toggle('is-busy', S.busy);
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      toast('已复制到剪贴板', 'ok');
    } catch (err) {
      toast('复制失败：浏览器拒绝了剪贴板访问', 'err');
    }
  }

  /* ------------------------------------------------------------------ *
   * 错误映射
   * ------------------------------------------------------------------ */
  function handleError(err) {
    if (!err) return;
    if (err.status === 401) {
      renderTokenInvalid(err);
      return;
    }
    const code = err.code || '';
    if (code === 'E_PERMISSION') {
      toast(err.message || '权限不足：非管理员只能演练', 'err');
    } else if (code === 'E_DEGRADED') {
      toast(err.message || '守护处于降级模式', 'warn');
    } else if (code === 'E_PLAN_EXPIRED' || code === 'E_PLAN_STALE') {
      toast(err.message || '计划已失效，请重新生成', 'warn');
    } else {
      toast(err.message || String(err), 'err');
    }
    if (code === 'E_IPC' || code === 'E_NETWORK') {
      S.connected = false;
      updateChrome();
    }
  }

  /* ------------------------------------------------------------------ *
   * 定时器：计划倒计时
   * ------------------------------------------------------------------ */
  function tick() {
    const plan = S.cleanup.plan;
    const node = $('#plan-countdown');
    if (!node || !plan || !plan.expires_at) return;
    const left = Math.round((Date.parse(plan.expires_at) - Date.now()) / 1000);
    if (left <= 0) {
      node.textContent = '已过期';
      node.className = 'danger';
      const button = $('#exec-count');
      if (button) button.disabled = true;
      return;
    }
    node.textContent = fmtCountdown(left);
    node.className = left <= 30 ? 'warn' : '';
  }

  /* ------------------------------------------------------------------ *
   * Token / 交互绑定 / 启动
   * ------------------------------------------------------------------ */
  function readToken() {
    const hash = location.hash || '';
    const match = /(?:^#|&)token=([^&]+)/.exec(hash);
    if (match) {
      const token = decodeURIComponent(match[1]);
      try { sessionStorage.setItem(TOKEN_KEY, token); } catch (err) { /* ignore */ }
      try { history.replaceState(null, '', location.pathname + location.search); } catch (err) { /* ignore */ }
      return token;
    }
    try { return sessionStorage.getItem(TOKEN_KEY) || ''; } catch (err) { return ''; }
  }

  const ACTIONS = {
    'nav': (el) => go(el.dataset.screen),
    'refresh': () => refreshCurrent(),
    'retry': () => renderRetry(),
    'reload-page': () => location.reload(),
    'goto-cleanup': () => go('cleanup', { scope: 'all' }),
    'goto-processes': () => go('processes'),
    'sw-filter': (el) => { S.sw.filter = el.dataset.filter; updateSoftwareGrid(); },
    'sw-open': (el) => openSoftware(el.dataset.id),
    'clean-software': (el) => go('cleanup', {
      scope: 'software',
      id: el.dataset.id,
      name: el.dataset.name || swName(el.dataset.id)
    }),
    'protect-software': (el) => protectSoftware(el.dataset.id),
    'proc-page': (el) => { S.proc.page = Number(el.dataset.page) || 1; updateProcessView(); },
    'proc-pick': (el) => selectProcess(Number(el.dataset.pid)),
    'plan-pid': (el) => planForPid(Number(el.dataset.pid)),
    'ci-toggle': (el) => toggleItem(Number(el.dataset.pid)),
    'sel-default': () => selectDefault(),
    'sel-clear': () => { S.cleanup.selected.clear(); updateSelectionUI(); },
    'execute': () => executeCleanup(),
    'plan-fresh': () => freshPlan(),
    'result-done': () => closeModal(true),
    'result-audit': () => { S.modalOnClose = null; closeModal(true); go('audit'); },
    'audit-detail': (el) => openAuditDetail(Number(el.dataset.seq)),
    'copy-json': () => copyText(S.audit.detailJson),
    'toggle-dryrun': () => toggleDryRun(),
    'toggle-idle': () => savePolicy({
      allow_cleanup_while_idle: !(S.policy && S.policy.allow_cleanup_while_idle)
    }),
    'save-min-idle': () => saveMinIdle(),
    'protect-add': () => protectAdd(),
    'protect-remove': (el) => protectRemove(el.dataset.pattern),
    'reload-config': () => reloadConfig(),
    'kb-reload': () => kbReload(),
    'kb-review': () => kbReview(),
    'test-conn': () => testConn(),
    'show-admin-help': () => showAdminHelp(),
    'modal-close': () => closeModal(),
    'modal-cancel': () => {
      if (S.confirmResolve) {
        const resolve = S.confirmResolve;
        S.confirmResolve = null;
        resolve(false);
      }
      closeModal(true);
    },
    'modal-confirm': () => {
      if (S.confirmResolve) {
        const resolve = S.confirmResolve;
        S.confirmResolve = null;
        resolve(true);
      }
      closeModal(true);
    }
  };

  function bindGlobal() {
    document.addEventListener('click', (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) return;
      const el = target.closest('[data-act]');
      if (!el) return;
      const act = el.dataset.act;
      if (act === 'modal-mask') {
        if (event.target === el) closeModal();
        return;
      }
      const handler = ACTIONS[act];
      if (!handler) return;
      event.preventDefault();
      try {
        const result = handler(el, event);
        if (result && typeof result.catch === 'function') result.catch(handleError);
      } catch (err) {
        handleError(err);
      }
    });

    document.addEventListener('input', (event) => {
      const target = event.target;
      if (target.id === 'sw-q') {
        S.sw.q = target.value;
        updateSoftwareGrid();
      } else if (target.id === 'proc-q') {
        S.proc.q = target.value;
        S.proc.page = 1;
        updateProcessView();
      }
    });

    document.addEventListener('change', (event) => {
      const target = event.target;
      if (target.id === 'sw-sort') {
        S.sw.sort = target.value;
        updateSoftwareGrid();
      } else if (target.id === 'proc-software') {
        S.proc.software = target.value; S.proc.page = 1; updateProcessView();
      } else if (target.id === 'proc-role') {
        S.proc.role = target.value; S.proc.page = 1; updateProcessView();
      } else if (target.id === 'proc-activity') {
        S.proc.activity = target.value; S.proc.page = 1; updateProcessView();
      } else if (target.id === 'proc-origin') {
        S.proc.origin = target.value; S.proc.page = 1; updateProcessView();
      } else if (target.id === 'proc-sort') {
        S.proc.sort = target.value; S.proc.page = 1; updateProcessView();
      } else if (target.id === 'audit-event') {
        S.audit.event = target.value;
        applyAuditFilter();
      } else if (target.id === 'audit-limit') {
        S.audit.limit = Number(target.value) || 200;
        S.auditForce = true;
        refreshCurrent();
      }
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && S.modalOpen) {
        if (S.confirmResolve) {
          const resolve = S.confirmResolve;
          S.confirmResolve = null;
          resolve(false);
        }
        closeModal(true);
      }
    });

    document.addEventListener('visibilitychange', () => {
      if (document.hidden) return;
      refreshStatus().then(() => refreshCurrent()).catch(() => {});
    });

    window.addEventListener('beforeunload', () => {
      if (S.eventSource) {
        try { S.eventSource.close(); } catch (err) { /* ignore */ }
      }
    });
  }

  function init() {
    S.token = readToken();
    buildNav();
    bindGlobal();
    const versionNode = $('#app-version');
    if (versionNode) versionNode.textContent = VERSION;
    if (!S.token) {
      renderTokenMissing();
      return;
    }
    refreshStatus().then(() => {
      connectEvents();
      go('overview');
    }).catch((err) => {
      if (err && err.status === 401) renderTokenInvalid(err);
      else renderOffline(err);
    });
    S.tickTimer = setInterval(tick, 1000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
