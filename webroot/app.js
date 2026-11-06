'use strict';

const WEBUI_SH = '/data/adb/modules/tailscale_direct/webui.sh';
const TIMEOUTS = {
  status: 20000,
  login: 75000,
  netcheck: 40000,
  checkUpdate: 95000,
  ping: 12000,
  control: 90000,
  update: 1200000,
};
const AUTH_HOSTS = new Set(['login.tailscale.com', 'controlplane.tailscale.com']);

const $ = (id) => document.getElementById(id);

const el = {
  footBridge: $('footBridge'),
  refreshBtn: $('refreshBtn'),
  banner: $('banner'),
  screenHost: $('screenHost'),
  screenInstall: $('screenInstall'),
  screenApp: $('screenApp'),
  installError: $('installError'),
  hostRefresh: $('hostRefresh'),
  installRefresh: $('installRefresh'),
  installUpdateBtn: $('installUpdateBtn'),
  stateDot: $('stateDot'),
  stateLabel: $('stateLabel'),
  stateTitle: $('stateTitle'),
  stateSub: $('stateSub'),
  factIps: $('factIps'),
  factVersion: $('factVersion'),
  lastUpdated: $('lastUpdated'),
  authCard: $('authCard'),
  authDesc: $('authDesc'),
  authUrlBox: $('authUrlBox'),
  authHost: $('authHost'),
  authUrl: $('authUrl'),
  authOpen: $('authOpen'),
  authCopy: $('authCopy'),
  loginBtn: $('loginBtn'),
  loginHint: $('loginHint'),
  powerBtn: $('powerBtn'),
  powerPlay: document.querySelector('.power-play'),
  powerStop: document.querySelector('.power-stop'),
  checkUpdateBtn: $('checkUpdateBtn'),
  updateNote: $('updateNote'),
  repairBtn: $('repairBtn'),
  updateBtn: $('updateBtn'),
  peerCount: $('peerCount'),
  peerSummary: $('peerSummary'),
  peerEmpty: $('peerEmpty'),
  peerList: $('peerList'),
  netcheckBtn: $('netcheckBtn'),
  netEmpty: $('netEmpty'),
  netResult: $('netResult'),
  netNote: $('netNote'),
  netList: $('netList'),
  netRaw: $('netRaw'),
  outputCard: $('outputCard'),
  outputTitle: $('outputTitle'),
  outputPre: $('outputPre'),
  toast: $('toast'),
};

const ui = {
  payload: null,
  status: null,
  netLatencies: new Map(),
  busy: false,
  busyText: '',
  busyHint: '',
  refreshing: false,
  authUrl: null,
  probeBusy: false,
  updateCheckedFor: '',
  ping: null,
};

let bridge = null;
let callbackSeq = 0;
let toastTimer = null;
let pingTimer = null;

/* Root bridge: KernelSU / APatch compatible exec(command, "{}", callback) */

function detectBridge() {
  for (const key of ['ksu', 'apatch']) {
    try {
      const api = window[key];
      if (api && typeof api.exec === 'function') {
        return {
          key,
          label: key === 'ksu' ? 'KernelSU' : 'APatch',
          exec: api.exec.bind(api),
        };
      }
    } catch (_) {
      /* inaccessible host object */
    }
  }
  return null;
}

function execRaw(command, timeoutMs) {
  return new Promise((resolve, reject) => {
    const name = `__tsboxCallback${++callbackSeq}`;
    let settled = false;
    let timer = null;

    const finish = (callback) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      delete window[name];
      callback();
    };

    window[name] = (errno, stdout, stderr) => {
      finish(() => {
        resolve({
          errno: Number(errno) || 0,
          stdout: typeof stdout === 'string' ? stdout : String(stdout == null ? '' : stdout),
          stderr: typeof stderr === 'string' ? stderr : String(stderr == null ? '' : stderr),
        });
      });
    };

    timer = setTimeout(() => {
      finish(() => reject(new Error(`命令执行超时（${Math.round(timeoutMs / 1000)} 秒）`)));
    }, timeoutMs);

    try {
      bridge.exec(command, '{}', name);
    } catch (error) {
      finish(() => reject(error));
    }
  });
}

function parseJson(text) {
  return JSON.parse(String(text == null ? '' : text).trim());
}

async function callWebui(sub, timeoutMs) {
  const result = await execRaw(`sh ${WEBUI_SH} ${sub}`, timeoutMs);
  let data;
  try {
    data = parseJson(result.stdout);
  } catch (_) {
    const detail = (result.stderr || result.stdout || '').trim().slice(0, 240);
    throw new Error(`webui.sh ${sub} 未返回有效 JSON（exit ${result.errno}）${detail ? '：' + detail : ''}`);
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    throw new Error(`webui.sh ${sub} 返回了意外的数据结构`);
  }
  return data;
}

/* Feedback */

function renderBusyStatus() {
  el.stateDot.className = 'dot warn busy';
  el.stateLabel.textContent = '处理中';
  el.stateTitle.textContent = ui.busyText;
  el.stateSub.textContent = ui.busyHint;
}

function setBusy(text, hint) {
  ui.busy = true;
  ui.busyText = text;
  ui.busyHint = hint || '请稍候';
  renderBusyStatus();
  if (!el.screenInstall.hidden && text === '正在安装更新') {
    el.installUpdateBtn.textContent = '正在安装…';
  }
  syncControls();
}

function clearBusy() {
  ui.busy = false;
  ui.busyText = '';
  ui.busyHint = '';
  el.installUpdateBtn.textContent = '安装 Tailscale';
  syncControls();
  if (ui.payload && ui.payload.installed) {
    const stamp = el.lastUpdated.textContent;
    renderStatus(ui.payload);
    el.lastUpdated.textContent = stamp;
  }
}

function toast(message, tone) {
  clearTimeout(toastTimer);
  el.toast.textContent = message;
  el.toast.className = 'toast' + (tone ? ' ' + tone : '');
  el.toast.hidden = false;
  toastTimer = setTimeout(() => {
    el.toast.hidden = true;
  }, 3600);
}

function setBanner(message, tone) {
  if (!message) {
    el.banner.hidden = true;
    el.banner.textContent = '';
    return;
  }
  el.banner.textContent = message;
  el.banner.className = 'banner' + (tone === 'bad' ? ' bad' : '');
  el.banner.hidden = false;
}

function clearBanner() {
  setBanner('', '');
}

/* Controls */

function syncControls() {
  const payload = ui.payload;
  const ready = Boolean(bridge && payload && payload.installed && !ui.busy);
  const running = Boolean(payload && payload.running);

  el.refreshBtn.disabled = ui.busy || ui.refreshing || !bridge;
  el.installRefresh.disabled = ui.busy || ui.refreshing || !bridge;
  el.powerBtn.disabled = !ready;
  el.checkUpdateBtn.disabled = !ready;
  el.repairBtn.disabled = !ready || !running;
  el.updateBtn.disabled = !bridge || ui.busy;
  el.installUpdateBtn.disabled = !bridge || ui.busy;
  el.netcheckBtn.disabled = !ready || !running;
  el.loginBtn.disabled = !ready || !running;
  el.authCopy.disabled = ui.busy || !ui.authUrl;

  const powerLabel = running ? '停止服务' : '启动服务';
  el.powerBtn.setAttribute('aria-label', powerLabel);
  el.powerBtn.title = powerLabel;
  el.powerBtn.classList.toggle('on', running);
  el.powerPlay.classList.toggle('is-hidden', running);
  el.powerStop.classList.toggle('is-hidden', !running);
  el.loginHint.textContent = running
    ? '登录最长约 65 秒，请勿关闭页面'
    : '先启动服务，再开始登录';
}

function showScreen(name) {
  if (name !== 'app' && ui.ping) stopPing(false);
  el.screenHost.hidden = name !== 'host';
  el.screenInstall.hidden = name !== 'install';
  el.screenApp.hidden = name !== 'app';
}

/* Status */

function stateInfo(running, backend) {
  if (!running) {
    return { tone: 'bad', label: '已停止', title: '服务未运行', sub: 'tailscaled 守护进程未启动' };
  }
  switch (backend) {
    case 'Running':
      return { tone: 'ok', label: '已连接', title: 'Tailnet 已连接', sub: '本机可通过 Tailnet 访问其他设备' };
    case 'NeedsLogin':
      return { tone: 'warn', label: '需要登录', title: '等待授权登录', sub: '在浏览器完成 Tailscale 授权' };
    case 'Starting':
      return { tone: 'warn', label: '启动中', title: '正在建立连接', sub: 'tailscaled 正在启动' };
    case 'NoState':
      return { tone: 'warn', label: '未登录', title: '尚未初始化', sub: '完成登录后才能加入 Tailnet' };
    case 'Stopped':
      return { tone: 'bad', label: '已停止', title: '服务已停止', sub: 'tailscaled 已停止运行' };
    default:
      return { tone: 'warn', label: '运行中', title: '服务运行中', sub: '暂时无法读取详细连接状态' };
  }
}

async function refreshStatus() {
  if (!bridge || ui.refreshing) return;
  ui.refreshing = true;
  setBusy('正在刷新状态', '读取 Tailscale 连接信息');
  try {
    const data = await callWebui('status', TIMEOUTS.status);
    applyStatus(data);
  } catch (error) {
    if (ui.payload && ui.payload.installed) {
      setBanner(error.message, 'bad');
    } else {
      renderUnavailable();
      setBanner(error.message, 'bad');
    }
    toast(error.message, 'bad');
  } finally {
    ui.refreshing = false;
    clearBusy();
    syncControls();
  }
}

function applyStatus(data) {
  ui.payload = data;
  if (ui.ping && (!data.installed || !data.running)) stopPing(false);
  ui.status = data.status && typeof data.status === 'object' && !Array.isArray(data.status) ? data.status : null;

  if (!data.installed) {
    showScreen('install');
    el.installError.textContent = data.error || `未找到 ${WEBUI_SH}`;
    clearBanner();
    el.authCard.hidden = true;
    setAuthUrl(null);
    syncControls();
    return;
  }

  showScreen('app');
  renderStatus(data);
  renderAuth();
  renderPeers();
  renderMessages(data);
  syncControls();
}

function renderStatus(data) {
  const running = Boolean(data.running);
  const status = ui.status;
  const backend = status ? status.BackendState || 'Unknown' : 'Unknown';
  const info = stateInfo(running, backend);
  const self = status && status.Self && typeof status.Self === 'object' ? status.Self : null;
  const tailnet = status && status.CurrentTailnet ? status.CurrentTailnet.Name : '';

  el.stateDot.className = `dot ${info.tone}`;
  el.stateLabel.textContent = info.label;
  el.stateTitle.textContent = info.title;
  el.stateSub.textContent = running && backend === 'Running' && tailnet ? `已加入 ${tailnet}` : info.sub;

  const ips = self && Array.isArray(self.TailscaleIPs) ? self.TailscaleIPs.filter((ip) => typeof ip === 'string') : [];

  el.factIps.textContent = ips.length ? ips.join(' · ') : '—';
  const version = data.version || (status && status.Version) || '';
  if (ui.updateCheckedFor && normVersion(version) !== ui.updateCheckedFor) clearUpdateNote();
  el.factVersion.textContent = version || '—';
  el.lastUpdated.textContent = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  if (ui.busy) renderBusyStatus();
}

function renderMessages(data) {
  const parts = [];
  if (data.error) parts.push(String(data.error));
  const health = ui.status && Array.isArray(ui.status.Health) ? ui.status.Health.filter((item) => typeof item === 'string') : [];
  if (health.length) parts.push(health.join('；'));
  if (parts.length) setBanner(parts.join('；'), 'warn');
  else clearBanner();
}

function renderUnavailable() {
  showScreen('app');
  el.stateDot.className = 'dot bad';
  el.stateLabel.textContent = '不可用';
  el.stateTitle.textContent = '无法读取状态';
  el.stateSub.textContent = '确认宿主已授权 root，然后刷新重试';
  el.factIps.textContent = '—';
  el.factVersion.textContent = '—';
  clearUpdateNote();
  el.lastUpdated.textContent = '—';
  el.authCard.hidden = true;
  setAuthUrl(null);
  el.peerCount.textContent = '0';
  el.peerSummary.textContent = '';
  el.peerList.replaceChildren();
  el.peerEmpty.textContent = '无法读取设备列表';
  el.peerEmpty.hidden = false;
  syncControls();
}

function stripDot(value) {
  return typeof value === 'string' ? value.replace(/\.$/, '') : '';
}

/* Auth URL */

function sanitizeAuthUrl(raw) {
  if (typeof raw !== 'string') return null;
  const trimmed = raw.trim().replace(/[\s)\],.;:'"`]+$/g, '');
  if (!trimmed) return null;
  let url;
  try {
    url = new URL(trimmed);
  } catch (_) {
    return null;
  }
  if (url.protocol !== 'https:') return null;
  if (!AUTH_HOSTS.has(url.hostname.toLowerCase())) return null;
  if (url.username || url.password) return null;
  if (url.port && url.port !== '443') return null;
  return url.href;
}

function extractAuthUrl(text) {
  const matches = String(text == null ? '' : text).match(/https:\/\/[^\s"'<>`]+/g) || [];
  for (const match of matches) {
    const url = sanitizeAuthUrl(match);
    if (url) return url;
  }
  return null;
}

function setAuthUrl(url) {
  ui.authUrl = url || null;
  el.authUrlBox.hidden = !url;
  el.authHost.textContent = '';
  el.authUrl.textContent = '';
  el.authOpen.href = '#';
  el.loginBtn.classList.toggle('primary', !url);
  if (url) {
    el.authUrl.textContent = url;
    try {
      el.authHost.textContent = new URL(url).hostname;
    } catch (_) {
      el.authHost.textContent = '';
    }
    el.authOpen.href = url;
    el.loginBtn.textContent = '重新获取授权链接';
  } else {
    el.loginBtn.textContent = '开始登录';
  }
  syncControls();
}

function renderAuth() {
  const status = ui.status;
  const running = Boolean(ui.payload && ui.payload.running);
  const backend = status ? status.BackendState : '';

  if (!running || backend === 'Running') {
    el.authCard.hidden = true;
    setAuthUrl(null);
    return;
  }

  const statusUrl = sanitizeAuthUrl(status && status.AuthURL);
  if (statusUrl) setAuthUrl(statusUrl);

  const authNeeded =
    backend === 'NeedsLogin' ||
    backend === 'NoState' ||
    backend === 'Starting' ||
    Boolean(ui.authUrl);

  if (!authNeeded) {
    el.authCard.hidden = true;
    return;
  }

  el.authCard.hidden = false;
  el.authDesc.textContent = ui.authUrl
    ? '在浏览器完成授权后返回本页刷新；若路由未生效，点「修复路由」'
    : '此设备尚未授权，点击开始登录获取授权链接';
  el.loginBtn.textContent = ui.authUrl ? '重新获取授权链接' : '开始登录';
}

async function copyAuthUrl() {
  if (!ui.authUrl) return;
  let copied = false;
  try {
    await navigator.clipboard.writeText(ui.authUrl);
    copied = true;
  } catch (_) {
    /* clipboard API can be unavailable in WebViews */
  }
  if (!copied) {
    const area = document.createElement('textarea');
    area.value = ui.authUrl;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.top = '-1000px';
    document.body.appendChild(area);
    area.select();
    try {
      copied = document.execCommand('copy');
    } catch (_) {
      copied = false;
    }
    area.remove();
  }
  toast(copied ? '链接已复制' : '复制失败，请长按选择链接', copied ? 'ok' : 'bad');
}

/* Peers */

function renderPeers() {
  const raw = ui.status && ui.status.Peer && typeof ui.status.Peer === 'object' ? ui.status.Peer : {};
  const peers = Object.values(raw)
    .filter((peer) => peer && typeof peer === 'object')
    .map((peer) => {
      const ips = Array.isArray(peer.TailscaleIPs)
        ? peer.TailscaleIPs.filter((ip) => typeof ip === 'string')
        : [];
      const dnsName = stripDot(peer.DNSName);
      const name = peer.HostName || dnsName || ips[0] || '未知设备';
      return {
        name,
        key: typeof peer.ID === 'string' ? peer.ID : `${name}|${ips.join(',')}`,
        ip: ips.find((ip) => ip.includes('.')) || ips[0] || '',
        dnsName: dnsName && dnsName !== name ? dnsName : '',
        ips: ips.join(', '),
        os: typeof peer.OS === 'string' ? peer.OS : '',
        online: Boolean(peer.Online),
        direct: Boolean(peer.CurAddr),
        relay: typeof peer.Relay === 'string' ? peer.Relay : '',
        curAddr: typeof peer.CurAddr === 'string' ? peer.CurAddr : '',
        exitNode: Boolean(peer.ExitNode),
        lastSeen: peer.LastSeen,
      };
    });

  peers.sort((a, b) => {
    if (a.online !== b.online) return a.online ? -1 : 1;
    return a.name.localeCompare(b.name, 'zh-Hans-CN', { sensitivity: 'base' });
  });
  if (ui.ping && !peers.some((peer) => peer.key === ui.ping.key)) stopPing(false);

  const online = peers.filter((peer) => peer.online).length;
  const direct = peers.filter((peer) => peer.online && peer.direct).length;
  const relayed = peers.filter((peer) => peer.online && !peer.direct).length;
  const summary = [`${online} 在线`, `${direct} 直连`];
  if (relayed) summary.push(`${relayed} DERP`);

  el.peerCount.textContent = String(peers.length);
  el.peerSummary.textContent = peers.length ? summary.join(' · ') : '';
  el.peerEmpty.textContent = '还没有其他设备加入这个 Tailnet';
  el.peerEmpty.hidden = peers.length > 0;
  el.peerList.replaceChildren();

  for (const peer of peers) {
    const item = document.createElement('li');
    item.className = 'peer';

    const dot = document.createElement('span');
    dot.className = 'peer-dot' + (peer.online ? ' on' : '');

    const main = document.createElement('div');
    main.className = 'peer-main';

    const nameRow = document.createElement('div');
    nameRow.className = 'peer-name';
    const name = document.createElement('span');
    name.textContent = peer.name;
    nameRow.appendChild(name);
    if (peer.exitNode) {
      const tag = document.createElement('span');
      tag.className = 'tag';
      tag.textContent = '出口';
      nameRow.appendChild(tag);
    }

    const meta = document.createElement('div');
    meta.className = 'peer-meta';
    const bits = [];
    if (peer.ips) bits.push(peer.ips);
    if (peer.os) bits.push(peer.os);
    if (!peer.online) {
      const seen = relativeTime(peer.lastSeen);
      if (seen) bits.push(`最近 ${seen}`);
    }
    meta.textContent = bits.join(' · ') || '无地址';

    main.appendChild(nameRow);
    main.appendChild(meta);
    if (peer.dnsName) {
      const detail = document.createElement('div');
      detail.className = 'peer-detail';
      detail.textContent = peer.dnsName;
      main.appendChild(detail);
    }

    const path = document.createElement('div');
    path.className = 'peer-path';
    const label = document.createElement('span');
    if (!peer.online) {
      label.className = 'path-label offline';
      label.textContent = '离线';
    } else if (peer.direct) {
      label.className = 'path-label direct';
      label.textContent = '直连';
    } else if (peer.relay) {
      label.className = 'path-label';
      label.textContent = `DERP ${peer.relay.toUpperCase()}`;
    } else {
      label.className = 'path-label';
      label.textContent = '未知';
    }
    path.appendChild(label);

    let note = '—';
    if (peer.online && peer.direct) {
      note = peer.curAddr || '—';
    } else if (peer.online && peer.relay) {
      const latency = relayLatency(peer.relay);
      note = latency != null ? `${latency.toFixed(1)} ms` : '延迟未知';
    }
    const noteEl = document.createElement('span');
    noteEl.className = 'path-note';
    noteEl.textContent = note;
    noteEl.title = note;
    path.appendChild(noteEl);
    const pingActive = Boolean(ui.ping && ui.ping.key === peer.key);
    const pingBtn = document.createElement('button');
    pingBtn.type = 'button';
    pingBtn.className = 'peer-ping' + (pingActive ? ' active' : '');
    pingBtn.textContent = pingActive ? '停止' : 'Ping';
    pingBtn.disabled = !bridge || (!pingActive && (!peer.online || !ui.payload.running));
    pingBtn.title = pingActive ? `停止 Ping ${peer.name}` : (peer.online ? `Ping ${peer.name}` : '设备离线，无法 Ping');
    pingBtn.setAttribute('aria-label', pingBtn.title);
    pingBtn.setAttribute('aria-pressed', pingActive ? 'true' : 'false');
    pingBtn.addEventListener('click', () => {
      if (pingActive) stopPing();
      else startPing(peer);
    });
    path.appendChild(pingBtn);

    item.appendChild(dot);
    item.appendChild(main);
    item.appendChild(path);
    if (pingActive) item.appendChild(buildPingPanel(ui.ping));
    el.peerList.appendChild(item);
  }
}

function relayLatency(relay) {
  const code = typeof relay === 'string' ? relay.trim().toLowerCase() : '';
  if (!code) return null;
  const ms = ui.netLatencies.get(code);
  return Number.isFinite(ms) ? ms : null;
}

const PING_INTERVAL = 1500;
const PING_LIMIT = 30;
const PING_IP = /^[0-9a-fA-F:.]{3,45}$/;

function parsePingMs(output) {
  const matches = [...String(output == null ? '' : output).matchAll(/\bin\s+([0-9.]+)\s*ms\b/gi)];
  return matches.length ? Number(matches[matches.length - 1][1]) : null;
}

function startPing(peer) {
  if (ui.busy) {
    toast('正在执行其他操作', 'bad');
    return;
  }
  if (!peer.online || !peer.ip || !PING_IP.test(peer.ip)) {
    toast('该设备没有可用的 Ping 地址', 'bad');
    return;
  }
  stopPing(false);
  const state = { key: peer.key, ip: peer.ip, name: peer.name, samples: [], losses: 0, active: true };
  ui.ping = state;
  renderPeers();
  pingTick(state);
}

function stopPing(rerender = true) {
  if (ui.ping) ui.ping.active = false;
  ui.ping = null;
  if (pingTimer) {
    clearTimeout(pingTimer);
    pingTimer = null;
  }
  if (rerender) renderPeers();
}

async function pingTick(state) {
  if (ui.ping !== state || !state.active) return;
  let ms = null;
  try {
    const result = await callWebui(`ping ${state.ip}`, TIMEOUTS.ping);
    if (result.exitCode !== 0 && /unknown|unsupported|usage|invalid operation/i.test(`${result.output || ''} ${result.error || ''}`)) {
      stopPing();
      toast('当前后端不支持设备 Ping', 'bad');
      return;
    }
    if (result.exitCode === 0) ms = parsePingMs(result.output);
  } catch (_) {
    ms = null;
  }
  if (ui.ping !== state || !state.active) return;
  state.samples.push(ms);
  if (state.samples.length > PING_LIMIT) state.samples.shift();
  if (ms == null) state.losses += 1;
  updatePingPanel(state);
  pingTimer = setTimeout(() => pingTick(state), PING_INTERVAL);
}

function buildPingPanel(state) {
  const panel = document.createElement('div');
  panel.className = 'ping-panel';

  const stats = document.createElement('div');
  stats.className = 'ping-stats';
  for (const [cls, label] of [['ping-current', '当前'], ['ping-avg', '平均'], ['ping-lost', '丢失']]) {
    const span = document.createElement('span');
    const value = document.createElement('b');
    value.className = cls;
    value.textContent = '—';
    const name = document.createElement('i');
    name.textContent = label;
    span.append(value, name);
    stats.appendChild(span);
  }

  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 300 52');
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', `${state.name} 的 Ping 延迟曲线`);
  const line = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  line.setAttribute('class', 'ping-line');
  svg.appendChild(line);

  const note = document.createElement('p');
  note.className = 'ping-note';
  note.textContent = '每 2 秒 Ping 一次，最多保留 30 次';

  panel.append(stats, svg, note);
  updatePingPanel(state, panel);
  return panel;
}

function updatePingPanel(state, panel = el.peerList.querySelector('.ping-panel')) {
  if (!panel) return;
  const values = state.samples.filter((value) => Number.isFinite(value));
  const last = state.samples.length ? state.samples[state.samples.length - 1] : null;
  const current = panel.querySelector('.ping-current');
  const average = panel.querySelector('.ping-avg');
  const lost = panel.querySelector('.ping-lost');

  current.textContent = state.samples.length === 0 ? '—' : (last == null ? '超时' : `${last.toFixed(1)} ms`);
  average.textContent = values.length ? `${(values.reduce((sum, value) => sum + value, 0) / values.length).toFixed(1)} ms` : '—';
  lost.textContent = String(state.losses);

  const max = values.length ? Math.max(...values) : 0;
  const scale = Math.max(50, Math.ceil(max / 20) * 20);
  const width = 300;
  const height = 52;
  const pad = 6;
  let path = '';
  let pen = false;
  const span = Math.max(1, Math.min(PING_LIMIT, state.samples.length) - 1);
  state.samples.forEach((ms, index) => {
    if (!Number.isFinite(ms)) {
      pen = false;
      return;
    }
    const x = (index / span) * width;
    const y = height - pad - (ms / scale) * (height - pad * 2);
    path += `${pen ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`;
    pen = true;
  });
  panel.querySelector('.ping-line').setAttribute('d', path);
}

function relativeTime(value) {
  const time = Date.parse(value);
  if (!Number.isFinite(time)) return '';
  const seconds = Math.max(0, Math.floor((Date.now() - time) / 1000));
  if (seconds < 60) return '刚刚';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}

/* Netcheck */

function setNetcheckPending(pending) {
  el.netcheckBtn.textContent = pending ? '测试中…' : '测试';
  el.netcheckBtn.setAttribute('aria-busy', pending ? 'true' : 'false');
  if (!pending) return;
  if (el.netResult.hidden) {
    el.netEmpty.textContent = '正在测试，最长约 30 秒…';
    el.netEmpty.hidden = false;
  } else {
    el.netNote.textContent = '正在重新测试…';
  }
}

async function runNetcheck() {
  if (ui.busy || !bridge || !ui.payload || !ui.payload.running) return;
  setBusy('正在测试网络', '测试 DERP 延迟，最长约 30 秒');
  setNetcheckPending(true);
  try {
    const result = await callWebui('netcheck', TIMEOUTS.netcheck);
    renderNetcheck(result);
    if (result.exitCode !== 0) toast(`netcheck 失败（exit ${result.exitCode}）`, 'bad');
  } catch (error) {
    toast(error.message, 'bad');
    setBanner(error.message, 'bad');
    if (el.netResult.hidden) {
      el.netEmpty.textContent = '测试失败，请重试';
      el.netEmpty.hidden = false;
    } else {
      el.netNote.textContent = '测试失败，请重试';
    }
  } finally {
    setNetcheckPending(false);
    clearBusy();
  }
}

function renderNetcheck(result) {
  const output = String(result.output == null ? '' : result.output);
  const failed = result.exitCode !== 0;

  el.netEmpty.hidden = true;
  el.netResult.hidden = false;
  el.netRaw.textContent = output.trim() || '（无输出）';
  el.netRaw.classList.toggle('is-error', failed);
  el.netRaw.parentElement.open = failed;

  const latencies = [];
  const linePattern = /^\s*[-*]\s*([A-Za-z0-9][A-Za-z0-9_-]{1,23}):\s*([0-9.]+)\s*ms(?:\s*\(([^)]*)\))?/gm;
  let match;
  while ((match = linePattern.exec(output)) !== null) {
    const ms = Number(match[2]);
    if (Number.isFinite(ms)) {
      latencies.push({ code: match[1], ms, name: (match[3] || '').trim() });
    }
  }
  latencies.sort((a, b) => a.ms - b.ms);

  ui.netLatencies.clear();
  for (const item of latencies) ui.netLatencies.set(item.code.toLowerCase(), item.ms);

  const nearestMatch = output.match(/Nearest DERP:\s*([^\n]+)/i);
  const nearest = nearestMatch ? nearestMatch[1].trim() : '';

  el.netList.replaceChildren();

  for (const item of latencies) {
    const row = document.createElement('li');

    const region = document.createElement('span');
    region.className = 'lat-region';
    const code = document.createElement('b');
    code.textContent = item.code;
    region.appendChild(code);
    if (item.name) {
      const name = document.createElement('span');
      name.className = 'lat-name';
      name.textContent = item.name;
      region.appendChild(name);
    }

    const bar = document.createElement('span');
    bar.className = 'bar';
    const fill = document.createElement('i');
    const width = Math.max(4, Math.min(100, Math.round((item.ms / 300) * 100)));
    fill.style.width = `${width}%`;
    fill.className = item.ms < 50 ? 'fast' : item.ms < 120 ? 'mid' : 'slow';
    bar.appendChild(fill);

    const value = document.createElement('span');
    value.className = 'lat-ms';
    value.textContent = `${item.ms.toFixed(1)} ms`;

    row.appendChild(region);
    row.appendChild(bar);
    row.appendChild(value);
    el.netList.appendChild(row);
  }

  const notes = [];
  if (failed) notes.push(`netcheck 退出码 ${result.exitCode}`);
  if (nearest) notes.push(`最近 DERP：${nearest}`);
  if (!latencies.length) notes.push('未解析到 DERP 延迟，请查看原始输出');
  el.netNote.textContent = notes.join(' ');
  renderPeers();
}

/* Actions */

function showOutput(title, text, failed) {
  const body = String(text == null ? '' : text).trim();
  el.outputCard.hidden = false;
  el.outputTitle.textContent = title;
  el.outputPre.textContent = body || (failed ? '（命令失败，无输出）' : '（无输出）');
  el.outputPre.classList.toggle('is-error', Boolean(failed));
  el.outputCard.open = Boolean(failed);
}

async function runAction(sub, label) {
  if (ui.busy || !bridge) return;
  const busy = {
    start: ['正在启动服务', '正在启动 tailscaled'],
    stop: ['正在停止服务', '正在停止 tailscaled'],
    repair: ['正在修复路由', '重建路由与优先级规则'],
  }[sub] || ['正在执行', '请稍候'];
  setBusy(busy[0], busy[1]);
  let result;
  try {
    result = await callWebui(sub, TIMEOUTS.control);
  } catch (error) {
    toast(error.message, 'bad');
    setBanner(error.message, 'bad');
    return;
  } finally {
    clearBusy();
  }

  const failed = result.exitCode !== 0;
  showOutput(label, result.output, failed);
  toast(failed ? `${label}失败（exit ${result.exitCode}）` : `${label}完成`, failed ? 'bad' : 'ok');
  await refreshStatus();
}

async function runUpdate() {
  if (ui.busy || !bridge) return;
  const ok = window.confirm('从官方源下载并替换 Tailscale 二进制，可能需要数分钟；运行中的服务会短暂重启，继续？');
  if (!ok) return;

  setBusy('正在安装更新', '下载替换可能需要数分钟');
  let result;
  try {
    result = await callWebui('update', TIMEOUTS.update);
  } catch (error) {
    toast(error.message, 'bad');
    setBanner(error.message, 'bad');
    return;
  } finally {
    clearBusy();
  }

  const failed = result.exitCode !== 0;
  showOutput('安装更新', result.output, failed);
  toast(failed ? `更新失败（exit ${result.exitCode}）` : '更新完成', failed ? 'bad' : 'ok');
  if (!failed) clearUpdateNote();
  await refreshStatus();
}

function normVersion(value) {
  const match = String(value == null ? '' : value).match(/\d+\.\d+\.\d+/);
  return match ? match[0] : '';
}

function compareVersions(a, b) {
  const pa = a.split('.').map(Number);
  const pb = b.split('.').map(Number);
  for (let i = 0; i < 3; i += 1) {
    if (pa[i] !== pb[i]) return (pa[i] || 0) - (pb[i] || 0);
  }
  return 0;
}

function showUpdateNote(text, tone) {
  el.updateNote.textContent = text;
  el.updateNote.className = 'version-note' + (tone ? ' ' + tone : '');
  el.updateNote.hidden = false;
}

function clearUpdateNote() {
  ui.updateCheckedFor = '';
  el.updateNote.hidden = true;
  el.updateNote.textContent = '';
}

async function checkUpdate() {
  if (ui.busy || !bridge || !ui.payload || !ui.payload.installed) return;
  setBusy('正在检查更新', '获取最新稳定版本');
  try {
    const result = await callWebui('check-update', TIMEOUTS.checkUpdate);
    const current = normVersion(result.current || ui.payload.version);
    const stable = String(result.output || '').match(/Stable-(\d+\.\d+\.\d+)/);
    const latest = normVersion(result.latest || (stable ? stable[1] : ''));
    if (!latest) {
      showUpdateNote('无法检查更新', 'bad');
      return;
    }
    const available = typeof result.available === 'boolean'
      ? result.available
      : Boolean(current && compareVersions(latest, current) > 0);
    showUpdateNote(available ? `新版本 ${latest}` : '无可用更新', available ? 'available' : 'current');
    ui.updateCheckedFor = current;
  } catch (error) {
    showUpdateNote('无法检查更新', 'bad');
    toast(error.message, 'bad');
  } finally {
    clearBusy();
  }
}

/* Login */

async function runLogin() {
  if (ui.busy || !bridge || !ui.payload || !ui.payload.running) return;
  setAuthUrl(null);
  el.authDesc.textContent = '正在获取授权链接，最长约 65 秒';
  setBusy('正在登录并等待授权', '最长约 65 秒，请勿关闭页面');
  const probeTimer = startAuthProbe();
  let result;
  try {
    result = await callWebui('login', TIMEOUTS.login);
  } catch (error) {
    toast(error.message, 'bad');
    setBanner(error.message, 'bad');
    return;
  } finally {
    clearInterval(probeTimer);
    clearBusy();
  }

  const url = sanitizeAuthUrl(result.url) || extractAuthUrl(result.output);
  if (url) {
    setAuthUrl(url);
    el.authDesc.textContent = '在浏览器完成授权后返回本页刷新；若路由未生效，点「修复路由」';
  }
  const failed = result.exitCode !== 0;
  if (result.output) showOutput('登录', result.output, failed && !url);
  toast(
    url
      ? failed
        ? '已获取授权链接（登录等待超时）'
        : '已获取授权链接，请在浏览器完成授权'
      : failed
        ? `登录未完成（exit ${result.exitCode}）`
        : '登录完成',
    failed && !url ? 'bad' : 'ok',
  );
  await refreshStatus();
}

function startAuthProbe() {
  return setInterval(async () => {
    if (ui.probeBusy) return;
    ui.probeBusy = true;
    try {
      const data = await callWebui('status', TIMEOUTS.status);
      const url = sanitizeAuthUrl(data.status && data.status.AuthURL);
      if (url && url !== ui.authUrl) {
        setAuthUrl(url);
        el.authDesc.textContent = '在浏览器完成授权后返回本页刷新；若路由未生效，点「修复路由」';
      }
    } catch (_) {
      /* the login command itself records the useful error */
    } finally {
      ui.probeBusy = false;
    }
  }, 4000);
}

/* Boot */

function bindEvents() {
  el.refreshBtn.addEventListener('click', refreshStatus);
  el.installRefresh.addEventListener('click', refreshStatus);
  el.hostRefresh.addEventListener('click', () => window.location.reload());
  el.installUpdateBtn.addEventListener('click', runUpdate);
  el.loginBtn.addEventListener('click', runLogin);
  el.authCopy.addEventListener('click', copyAuthUrl);
  el.powerBtn.addEventListener('click', () => {
    if (!ui.payload) return;
    if (ui.payload.running) runAction('stop', '停止');
    else runAction('start', '启动');
  });
  el.checkUpdateBtn.addEventListener('click', checkUpdate);
  el.repairBtn.addEventListener('click', () => runAction('repair', '修复路由'));
  el.updateBtn.addEventListener('click', runUpdate);
  el.netcheckBtn.addEventListener('click', runNetcheck);
}

async function boot() {
  bindEvents();
  bridge = detectBridge();
  if (!bridge) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    bridge = detectBridge();
  }

  if (!bridge) {
    el.footBridge.textContent = 'Tailscale Box';
    showScreen('host');
    syncControls();
    return;
  }

  el.footBridge.textContent = `Tailscale Box · ${bridge.label}`;
  showScreen('app');
  syncControls();
  await refreshStatus();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
