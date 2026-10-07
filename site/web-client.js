/* The company API owns all user data. GitHub only serves interface assets. */
(() => {
  'use strict';
  const nativeFetch = window.fetch.bind(window);
  const nativeXHR = window.XMLHttpRequest;
  const repoRoot = new URL('./', location.href);
  const authKey = `h3-web-auth:${repoRoot.pathname}:v1`;
  const serverKey = `h3-web-server:${repoRoot.pathname}:v1`;
  const tickets = new Map();
  let session = null;
  let loaded = false;
  let refreshing = false;
  const missingTickets = new Set();
  let missingTimer = null;
  const mediaReferences = new Set();
  const trackedMedia = new WeakSet();
  const mediaPattern = /^\/api\/(?:assets\/[a-f0-9]{32}|jobs\/[a-f0-9]{32}\/(?:video|preview|frames|thumbnail)|(?:voice|music)\/jobs\/[a-f0-9]{32}\/audio|images\/jobs\/[a-f0-9]{32}\/image|editor\/(?:media\/[a-f0-9]{32}\/(?:file|thumbnail|preview\/file)|projects\/[a-f0-9]{32}\/archive|exports\/[a-f0-9]{32}\/file))$/;

  function normalizeServer(value) {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || !['', '/'].includes(url.pathname))
      throw new Error('請輸入完整公司主機網址，例如 http://192.168.1.20:8795。');
    if (['8188', '8190'].includes(url.port))
      throw new Error('這是引擎／共享 Gateway 網址。請使用 Studio Web 主機網址，預設連接埠為 8795。');
    return url.origin;
  }
  function readSession() { try { return JSON.parse(window.sessionStorage.getItem(authKey)); } catch { return null; } }
  function saveSession(value) { window.sessionStorage.setItem(authKey, JSON.stringify(value)); }
  function storageScope(storage) {
    function prefix() {
      if (!session?.user?.id) throw new Error('請先登入。');
      return `h3-web:${repoRoot.pathname}:${encodeURIComponent(session.server)}:${session.user.id}:`;
    }
    function keys() { const head = prefix(); return Array.from({length: storage.length}, (_, index) => storage.key(index)).filter(key => key?.startsWith(head)); }
    return {
      get length() { return keys().length; },
      key(index) { const key = keys()[index]; return key ? key.slice(prefix().length) : null; },
      getItem(key) { return storage.getItem(prefix() + key); },
      setItem(key, value) { storage.setItem(prefix() + key, String(value)); },
      removeItem(key) { storage.removeItem(prefix() + key); },
      clear() { for (const key of keys()) storage.removeItem(key); },
    };
  }
  function page(value) {
    if (typeof value !== 'string') return value;
    return value.replace(/^\/editor(?=\?|$)/, './editor.html').replace(/^\/images(?=\?|$)/, './images.html');
  }
  function apiPath(value) {
    if (typeof value !== 'string') return null;
    if (value.startsWith('/api/')) return new URL(value, session?.server || 'http://unused.invalid');
    if (session && value.startsWith(session.server + '/api/')) return new URL(value);
    return null;
  }
  function url(value) {
    if (typeof value !== 'string') return value;
    const target = apiPath(value);
    if (!target) return page(value);
    if (!session) throw new Error('請先連線公司主機。');
    const ticket = tickets.get(target.pathname);
    target.searchParams.delete('ticket');
    if (ticket) target.searchParams.set('ticket', ticket);
    else if (mediaPattern.test(target.pathname)) {
      missingTickets.add(target.pathname);
      if (!missingTimer) missingTimer = setTimeout(loadMissingTickets, 30);
    }
    return target.href;
  }
  function css(value) { return typeof value === 'string' ? value.replace(/url\((['"]?)([^)'"\s]+)\1\)/g, (_, quote, path) => `url("${url(path)}")`) : value; }
  function html(value) {
    return value.replace(/href=(['"])(\/(?:editor|images)(?:\?[^'"]*)?)\1/g, (_, quote, path) => `href=${quote}${page(path)}${quote}`)
      .replace(/\b(src|href|data-src)=(['"])(\/api\/[^'"\s]+)\2/g, (_, name, quote, path) => `${name}=${quote}${url(path)}${quote}`)
      .replace(/<(img|video|audio)\b(?![^>]*\bcrossorigin=)/g, '<$1 crossorigin="anonymous"')
      .replace(/url\((['"])(\/api\/[^)'"\s]+)\1\)/g, (_, quote, path) => `url('${url(path)}')`);
  }
  function acceptTickets(value) {
    for (const [path, ticket] of Object.entries(value || {})) tickets.set(path, ticket);
  }
  async function loadMissingTickets() {
    missingTimer = null;
    if (!session || !missingTickets.size) return;
    const paths = [...missingTickets]; missingTickets.clear();
    try {
      const value = await webAPI('/web/media-tickets', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({paths})});
      acceptTickets(value.tickets);
      updateMediaSources();
    } catch { /* Normal API refresh or a new login will retry. */ }
  }
  function rewriteJSON(value, field = '') {
    if (typeof value === 'string') return /(?:^url$|_url$)/.test(field) && apiPath(value) ? url(value) : value;
    if (Array.isArray(value)) return value.map(child => rewriteJSON(child, field));
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, child]) => [key, rewriteJSON(child, key)]));
    return value;
  }
  async function fetchAPI(value, options = {}) {
    const target = apiPath(typeof value === 'string' ? value : value?.url);
    if (!target || !session) throw new Error('此請求不是目前公司的 Studio API。');
    target.searchParams.delete('ticket');
    const headers = new Headers(options.headers || (value instanceof Request ? value.headers : undefined));
    headers.set('Authorization', `Bearer ${session.token}`);
    const response = await nativeFetch(target.href, { ...options, headers, credentials: 'omit', mode: 'cors', referrerPolicy: 'no-referrer' });
    const json = response.json.bind(response);
    response.json = async () => {
      const value = await json();
      if (value && Object.prototype.hasOwnProperty.call(value, '__h3_web_payload')) {
        acceptTickets(value.__h3_web_tickets);
        return rewriteJSON(value.__h3_web_payload);
      }
      return value;
    };
    if (response.status === 401) showLogin('登入已失效，請重新輸入個人金鑰。');
    return response;
  }
  class StudioXHR extends nativeXHR {
    open(method, value, ...rest) {
      const target = apiPath(value);
      if (!target || !session) throw new Error('此上傳不是目前公司的 Studio API。');
      target.searchParams.delete('ticket');
      super.open(method, target.href, ...rest);
      this.setRequestHeader('Authorization', `Bearer ${session.token}`);
      this.withCredentials = false;
      this.addEventListener('load', () => {
        const response = super.response;
        if (this.responseType === 'json' && response && Object.prototype.hasOwnProperty.call(response, '__h3_web_payload')) {
          acceptTickets(response.__h3_web_tickets);
          this.webResponse = rewriteJSON(response.__h3_web_payload);
        }
        if (this.status === 401) showLogin('登入已失效，請重新輸入個人金鑰。');
      });
    }
    get response() { return this.webResponse === undefined ? super.response : this.webResponse; }
  }
  // Canvas previews need anonymous CORS set before a remote source is assigned.
  for (const type of [window.HTMLImageElement, window.HTMLMediaElement]) {
    const descriptor = Object.getOwnPropertyDescriptor(type.prototype, 'src');
    if (!descriptor?.set) continue;
    Object.defineProperty(type.prototype, 'src', {
      ...descriptor,
      set(value) {
        if (apiPath(value)) {
          this.crossOrigin = 'anonymous';
          if (!trackedMedia.has(this)) { trackedMedia.add(this); mediaReferences.add(new WeakRef(this)); }
        }
        descriptor.set.call(this, url(value));
      },
    });
  }
  window.H3Web = { url, page, html, css, fetch: fetchAPI, XMLHttpRequest: StudioXHR,
    localStorage: storageScope(window.localStorage), sessionStorage: storageScope(window.sessionStorage) };

  const login = document.createElement('section');
  login.id = 'h3-web-login';
  login.setAttribute('aria-label', '連線公司 Studio');
  login.innerHTML = `<div class="h3-login-shell"><div class="h3-login-story"><div class="h3-login-mark">H3.</div><div class="h3-login-eyebrow">COMPANY STUDIO / WEB ACCESS</div><h1>你的創作工作站</h1><p>在瀏覽器整理想法、生成畫面與剪輯影片。<br>連線公司的 GPU，繼續你的專案。</p><div class="h3-login-route"><span>01 連線公司</span><span>02 驗證金鑰</span><span>03 開始創作</span></div></div><form class="h3-login-form"><h2>連線公司 Studio</h2><p>請使用管理者提供的主機網址與個人金鑰。</p><label for="h3-web-server">公司主機網址</label><input id="h3-web-server" type="url" placeholder="http://192.168.1.20:8795" required autocomplete="off" spellcheck="false"><small class="h3-login-help">請連接公司的 Wi-Fi 或有線網路。</small><label for="h3-web-key">個人金鑰</label><input id="h3-web-key" type="password" placeholder="h3g_…" required autocomplete="off" spellcheck="false"><small class="h3-login-help">金鑰只用於本次驗證。請勿共用個人金鑰。</small><button class="h3-login-submit" type="submit">連線並開啟工作室 →</button><p class="h3-login-status" role="status" aria-live="polite"></p></form></div>`;
  document.body.append(login);
  const form = login.querySelector('form');
  const serverInput = login.querySelector('#h3-web-server');
  const keyInput = login.querySelector('#h3-web-key');
  const status = login.querySelector('[role="status"]');
  try { serverInput.value = window.localStorage.getItem(serverKey) || ''; } catch {}
  serverInput.nextElementSibling.textContent = '請連接公司內網，填入 Studio Web 網址（預設 8795）。共享引擎 8190 和 ComfyUI 8188 無法直接使用。';
  function showLogin(message = '') {
    document.body.classList.add('h3-web-locked');
    login.hidden = false;
    status.textContent = message;
    backButton.hidden = !session;
  }
  const backButton = document.createElement('button');
  backButton.type = 'button'; backButton.className = 'h3-login-back';
  backButton.textContent = '返回目前工作室'; backButton.hidden = true;
  backButton.onclick = () => { login.hidden = true; document.body.classList.remove('h3-web-locked'); };
  form.append(backButton);
  async function webAPI(path, options = {}, explicitSession = session) {
    const headers = new Headers(options.headers);
    if (explicitSession?.token) headers.set('Authorization', `Bearer ${explicitSession.token}`);
    const response = await nativeFetch(explicitSession.server + path, { ...options, headers, credentials: 'omit', mode: 'cors', referrerPolicy: 'no-referrer' });
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(data.error || `HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return data;
  }
  async function activate(value) {
    if (loaded) {
      // A running editor's draft storage must retain its original identity.
      if (value.server !== session.server || value.user.id !== session.user.id)
        throw new Error('切換公司或使用者前，請先儲存專案並登出目前工作室。');
      session = value; saveSession(value);
      login.hidden = true; document.body.classList.remove('h3-web-locked');
      updateMediaSources();
      return;
    }
    session = value;
    saveSession(value);
    login.hidden = true;
    document.body.classList.remove('h3-web-locked');
    document.body.classList.add('h3-web-managed');
    loaded = true;
    const identity = document.createElement('div');
    identity.className = 'h3-web-identity';
    const name = document.createElement('span');
    name.textContent = value.user.name;
    const exit = document.createElement('button');
    exit.textContent = '登出'; exit.type = 'button';
    exit.onclick = async () => {
      await webAPI('/web/logout', { method: 'POST' }).catch(() => {});
      window.sessionStorage.removeItem(authKey);
      // Keep session identity until unload completes so editor beforeunload
      // writes its draft to the same user's namespace, even if reload is cancelled.
      location.reload();
    };
    identity.append(name, exit);
    (document.querySelector('.topbar, .editor-topbar, header') || document.body).append(identity);
    document.addEventListener('click', event => {
      if (event.target.closest('#openConnectionSettings')) {
        event.preventDefault(); event.stopImmediatePropagation();
        showLogin('更換公司或使用者前，請先儲存目前的剪輯專案。');
      }
    }, true);
    const scripts = JSON.parse(document.getElementById('h3-web-scripts').textContent);
    for (const src of scripts) {
      await new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = src; script.onload = resolve;
        script.onerror = () => reject(new Error('介面檔案載入失敗，請重新整理。'));
        document.body.append(script);
      });
    }
    setInterval(refreshMedia, 10 * 60 * 1000);
  }
  async function refreshMedia() {
    if (!session || refreshing || !tickets.size) return;
    refreshing = true;
    try {
      const paths = [...tickets.keys()].slice(-8192);
      const result = await webAPI('/web/media-tickets', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({paths}) });
      acceptTickets(result.tickets);
      updateMediaSources();
    } catch (error) { if (error.status === 401) showLogin('連線已失效，請重新登入。'); }
    finally { refreshing = false; }
  }
  function updateMediaSources() {
      const elements = new Set(document.querySelectorAll('[src],[href],[data-src],[style]'));
      for (const reference of mediaReferences) {
        const element = reference.deref();
        if (element) elements.add(element); else mediaReferences.delete(reference);
      }
      for (const element of elements) {
        for (const name of ['src', 'href', 'data-src']) {
          const old = element.getAttribute(name);
          if (!apiPath(old)) continue;
          const next = url(old);
          if (old === next) continue;
          if (name === 'src' && element instanceof HTMLMediaElement) {
            const position = element.currentTime, playing = !element.paused;
            element.addEventListener('loadedmetadata', () => { element.currentTime = position; if (playing) element.play().catch(() => {}); }, {once: true});
          }
          element.setAttribute(name, next);
        }
        if (element.style.backgroundImage) element.style.backgroundImage = css(element.style.backgroundImage);
      }
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button');
    button.disabled = true; status.textContent = '正在驗證金鑰並開啟你的工作室…';
    try {
      const server = normalizeServer(serverInput.value.trim());
      // Check the endpoint before transmitting a personal key.
      const health = await webAPI('/web/health', {signal: AbortSignal.timeout(8000)}, {server});
      if (health.service !== 'h3-studio-web')
        throw new Error('指定網址不是 Studio Web 入口。請確認主機網址與連接埠，預設為 8795。');
      const result = await webAPI('/web/login', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({key: keyInput.value.trim()}) }, {server});
      keyInput.value = '';
      if (loaded && session && (server !== session.server || result.user.id !== session.user.id)) {
        await webAPI('/web/logout', {method: 'POST'}, {...result, server}).catch(() => {});
        throw new Error('切換公司或使用者前，請先返回工作室、儲存專案並登出。');
      }
      try { window.localStorage.setItem(serverKey, server); } catch {}
      if (session?.token) await webAPI('/web/logout', {method: 'POST'}).catch(() => {});
      tickets.clear();
      await activate({...result, server});
    } catch (error) {
      status.textContent = error instanceof TypeError ? '無法連線公司主機。請確認使用 Studio Web 網址（8795）、公司內網及瀏覽器區域網路權限；同事電腦連線也需要主機防火牆開放。' :
        error.name === 'TimeoutError' ? '公司主機連線逾時。請確認內網、主機網址與 8795 防火牆規則。' :
        error instanceof SyntaxError ? '指定網址沒有提供 Studio Web API。請確認使用管理者提供的 Web 主機網址（預設 8795）。' : error.message;
    } finally { button.disabled = false; }
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshMedia(); });
  (async () => {
    const previous = readSession();
    if (!previous?.server || !previous?.token || !previous?.user?.id) return;
    try {
      previous.server = normalizeServer(previous.server);
      const state = await webAPI('/web/session', {}, previous);
      await activate({...previous, ...state});
    } catch (error) {
      if (error.status === 401) window.sessionStorage.removeItem(authKey);
      showLogin(error.status === 503 ? 'GPU 主機暫時無法連線，請稍後重新整理。' : '請重新輸入金鑰以連線公司 Studio。');
    }
  })();
})();
