'use strict';

const $ = id => document.getElementById(id);
const PDFJS_WORKER_URL = '/static/pdf.worker.min.mjs';
const PDFJS_MODULE_URL = '/static/pdf.min.mjs';

/* =========================================================
   客户端 ID
   ========================================================= */

const CLIENT_ID_KEY = 'landrop_client_id';

function getClientId(){
  let id = null;
  try{ id = localStorage.getItem(CLIENT_ID_KEY); }catch(e){}
  if(!id){
    if(window.crypto && typeof crypto.randomUUID === 'function'){
      id = crypto.randomUUID();
    } else {
      id = Date.now().toString(36) + '-' +
           Math.random().toString(36).slice(2, 10) + '-' +
           Math.random().toString(36).slice(2, 10);
    }
    try{ localStorage.setItem(CLIENT_ID_KEY, id); }catch(e){}
  }
  return id;
}

const CLIENT_ID = getClientId();

/* =========================================================
   设备名
   ========================================================= */

const DEVICE_NAME_KEY = 'landrop_device_name';

function loadDeviceName(){
  try{ return localStorage.getItem(DEVICE_NAME_KEY) || ''; }catch(e){ return ''; }
}

function saveDeviceName(name){
  try{
    if(name) localStorage.setItem(DEVICE_NAME_KEY, name);
    else localStorage.removeItem(DEVICE_NAME_KEY);
  }catch(e){}
}

/* 给所有 fetch 自动带上 X-Device-Name 头 */
(function(){
  const _origFetch = window.fetch;
  if(typeof _origFetch !== 'function') return;
  window.fetch = function(input, init){
    try{
      const name = loadDeviceName();
      if(name){
        init = init || {};
        let headers;
        if(init.headers instanceof Headers){
          headers = init.headers;
        } else if(Array.isArray(init.headers)){
          headers = new Headers(init.headers);
        } else {
          headers = new Headers(init.headers || {});
        }
        if(!headers.has('X-Device-Name')){
          try{ headers.set('X-Device-Name', encodeURIComponent(name)); }catch(e){}
        }
        init.headers = headers;
      }
    }catch(e){}
    return _origFetch.call(this, input, init);
  };
})();

/* =========================================================
   工具函数
   ========================================================= */

function fmtSize(b){
  if(b < 1024) return b + ' B';
  if(b < 1048576) return (b/1024).toFixed(1) + ' KB';
  if(b < 1073741824) return (b/1048576).toFixed(1) + ' MB';
  return (b/1073741824).toFixed(2) + ' GB';
}

function fmtTime(sec){
  const d = new Date(sec * 1000);
  const now = Date.now();
  const diff = (now - d.getTime()) / 1000;
  if(diff < 60) return '刚刚';
  if(diff < 3600) return Math.floor(diff/60) + ' 分钟前';
  if(diff < 86400) return Math.floor(diff/3600) + ' 小时前';
  return d.toLocaleDateString();
}

function fileExtClass(name){
  const m = /\.([a-z0-9]+)$/i.exec(name || '');
  const ext = m ? m[1].toLowerCase() : '';
  if(['jpg','jpeg','png','gif','webp','heic','bmp','svg','avif','ico'].includes(ext))
    return ['img', ext.slice(0,3)];
  if(['mp4','mov','mkv','avi','webm','flv','wmv','m4v'].includes(ext))
    return ['vid', ext.slice(0,3)];
  if(['mp3','wav','flac','aac','m4a','ogg','opus'].includes(ext))
    return ['aud', ext.slice(0,3)];
  if(ext === 'pdf') return ['pdf', 'PDF'];
  if(['doc','docx','docm','dot','dotx','dotm','rtf','odt','ott','fodt'].includes(ext))
    return ['doc', ext.slice(0,3)];
  if(['xls','xlsx','xlsm','xlsb','xltx','xltm','ods','ots','fods'].includes(ext))
    return ['sheet', ext.slice(0,3)];
  if(['ppt','pptx','pptm','pps','ppsx','pot','potx','potm','odp','otp','fodp'].includes(ext))
    return ['slide', ext.slice(0,3)];
  if(['zip','rar','7z','tar','gz','bz2','xz'].includes(ext))
    return ['zip', ext.slice(0,3)];
  if(['js','ts','jsx','tsx','py','rb','go','rs','java','kt','swift','c','cpp',
      'h','hpp','cs','php','sh','bash','zsh','sql','html','htm','css','scss',
      'xml','json','yaml','yml','toml','ini','conf'].includes(ext))
    return ['code', ext.slice(0,3)];
  if(['txt','md','log'].includes(ext)) return ['txt', ext.slice(0,3)];
  return ['', ext ? ext.slice(0,3) : 'FILE'];
}

function initials(name){
  if(!name) return '?';
  const s = name.replace(/[·•].*$/, '').trim();
  return (s[0] || '?').toUpperCase();
}

function escapeHtml(s){
  return String(s).replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function cssEscape(s){
  if(window.CSS && CSS.escape) return CSS.escape(s);
  return String(s).replace(/[^a-zA-Z0-9_-]/g, c => '\\' + c);
}

function toast(msg){
  const el = document.createElement('div');
  el.textContent = msg;
  el.style.cssText =
    'position:fixed;bottom:28px;left:50%;transform:translateX(-50%);' +
    'background:#111827;color:#fff;padding:10px 22px;border-radius:10px;' +
    'font-size:13.5px;font-weight:500;z-index:999;' +
    'box-shadow:0 10px 30px rgba(0,0,0,.25);';
  document.body.appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity .25s';
    el.style.opacity = '0';
    setTimeout(() => el.remove(), 250);
  }, 1800);
}

/* =========================================================
   微信下载引导
   ========================================================= */

function isWechat(){
  return /MicroMessenger/i.test(navigator.userAgent);
}

function downloadUrl(name){
  return '/download/' + encodeURIComponent(name);
}

function handleDownloadClick(e, name){
  if(!isWechat()) return;
  e.preventDefault();
  showWechatDownloadGuide(name);
}

function showWechatDownloadGuide(name){
  const url = location.origin + '/dl?name=' + encodeURIComponent(name);

  const old = document.getElementById('wxDownloadGuide');
  if(old) old.remove();

  const mask = document.createElement('div');
  mask.id = 'wxDownloadGuide';
  mask.className = 'modal-mask';
  mask.innerHTML =
    '<div class="modal" style="max-width:420px;text-align:center;">' +
      '<h3 style="margin-bottom:8px;">微信内无法直接下载</h3>' +
      '<p style="color:var(--dim);font-size:13.5px;margin:0 0 16px;line-height:1.6;">' +
        '请点击右上角 <b>···</b> → <b>在浏览器打开</b>，<br>' +
        '或复制下方链接到浏览器访问：' +
      '</p>' +
      '<div style="display:flex;gap:8px;margin-bottom:16px;">' +
        '<input id="wxDlUrl" readonly ' +
               'style="flex:1;min-width:0;padding:9px 10px;' +
                      'border:1px solid var(--border-2);border-radius:8px;' +
                      'font-size:12.5px;background:var(--bg-sub);' +
                      'color:var(--text);box-sizing:border-box;">' +
        '<button class="mini-btn primary" id="wxDlCopy" ' +
                'style="flex:0 0 auto;">复制</button>' +
      '</div>' +
      '<div class="modal-actions" style="justify-content:center;">' +
        '<button data-act="close">关闭</button>' +
      '</div>' +
    '</div>';

  document.body.appendChild(mask);

  const input = mask.querySelector('#wxDlUrl');
  input.value = url;

  mask.querySelector('#wxDlCopy').onclick = async () => {
    let ok = false;
    try{
      if(navigator.clipboard && navigator.clipboard.writeText){
        await navigator.clipboard.writeText(url);
        ok = true;
      }
    }catch(e){}
    if(!ok){
      try{
        input.removeAttribute('readonly');
        input.select();
        input.setSelectionRange(0, 99999);
        ok = document.execCommand('copy');
        input.setAttribute('readonly', '');
      }catch(e){}
    }
    toast(ok ? '已复制，请粘贴到浏览器打开' : '复制失败，请长按链接手动复制');
  };

  mask.querySelector('[data-act="close"]').onclick = () => mask.remove();
  mask.addEventListener('click', e => {
    if(e.target === mask) mask.remove();
  });
}

/* =========================================================
   SVG 图标
   ========================================================= */

const CHECK_SVG =
  '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" ' +
  'stroke="currentColor" stroke-width="3.5" stroke-linecap="round" ' +
  'stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';

function iconSvg(kind){
  if(kind === 'pause') return '<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>';
  if(kind === 'play')  return '<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
  if(kind === 'file')  return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>';
  if(kind === 'close') return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
  if(kind === 'download') return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
  if(kind === 'music') return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>';
  if(kind === 'check') return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
  return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
}

/* =========================================================
   IndexedDB
   ========================================================= */

const DB_NAME = 'landrop';
const DB_STORE = 'blobs';
const DB_VERSION = 1;
let _dbPromise = null;

function openDB(){
  if(_dbPromise) return _dbPromise;
  _dbPromise = new Promise((resolve, reject) => {
    let req;
    try{ req = indexedDB.open(DB_NAME, DB_VERSION); }
    catch(e){ reject(e); return; }
    req.onupgradeneeded = e => {
      const db = e.target.result;
      if(!db.objectStoreNames.contains(DB_STORE)){
        db.createObjectStore(DB_STORE, { keyPath: 'key' });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
    req.onblocked = () => reject(new Error('IndexedDB blocked'));
  });
  return _dbPromise;
}

function makeBlobKey(name, size){ return JSON.stringify([name, size]); }

async function saveBlobToDB(name, size, file){
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(DB_STORE, 'readwrite');
    tx.objectStore(DB_STORE).put({
      key: makeBlobKey(name, size),
      name: file.name, size: file.size,
      type: file.type || '', lastModified: file.lastModified || 0,
      blob: file,
    });
    tx.oncomplete = () => resolve(true);
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
}

async function loadBlobFromDB(name, size){
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(DB_STORE, 'readonly');
    const req = tx.objectStore(DB_STORE).get(makeBlobKey(name, size));
    req.onsuccess = () => resolve(req.result || null);
    req.onerror = () => reject(req.error);
  });
}

async function deleteBlobFromDB(name, size){
  try{
    const db = await openDB();
    return await new Promise((resolve) => {
      const tx = db.transaction(DB_STORE, 'readwrite');
      tx.objectStore(DB_STORE).delete(makeBlobKey(name, size));
      tx.oncomplete = () => resolve(true);
      tx.onerror = () => resolve(false);
      tx.onabort = () => resolve(false);
    });
  }catch(e){ return false; }
}

function recordToFile(rec){
  if(!rec || !rec.blob) return null;
  if(rec.blob instanceof File) return rec.blob;
  try{
    return new File([rec.blob], rec.name, {
      type: rec.type || '', lastModified: rec.lastModified || Date.now(),
    });
  }catch(e){ return null; }
}

/* =========================================================
   状态
   ========================================================= */

let queue = [];
let uid = 0;
let clients = [];
let files = [];
let _fileElCache = new Map();

const filePage = {
  offset: 0,
  limit: 60,
  total: 0,
  counts: { all: 0, img: 0, vid: 0, aud: 0, doc: 0, zip: 0, other: 0 },
};

const TARGET_KEY = 'landrop_target';
const TARGET_NAME_KEY = 'landrop_target_name';
const QUEUE_KEY = 'landrop_queue';
const FILE_FILTER_KEY = 'landrop_file_filter';

const queuePanel = $('queuePanel');
const queueList = $('queueList');
const queueSummary = $('queueSummary');
const fileInput = $('fileInput');
const targetSelect = $('targetSelect');
const fileList = $('fileList');
const fileCount = $('fileCount');
const targetDropdown = $('targetDropdown');
const targetBtn = $('targetBtn');
const targetBtnText = $('targetBtnText');
const targetMenu = $('targetMenu');

const filesPager = $('filesPager');
const pagerInfo = $('pagerInfo');
const pagerFirst = $('pagerFirst');
const pagerPrev = $('pagerPrev');
const pagerNext = $('pagerNext');
const pagerLast = $('pagerLast');

const fileFilter = {
  category: 'all',
  search: '',
  sort: 'mtime_desc',
};

let fileSelectMode = false;
const selectedFiles = new Set();

const FILE_CATEGORIES = [
  { id: 'all',   label: '全部' },
  { id: 'img',   label: '图片' },
  { id: 'vid',   label: '视频' },
  { id: 'aud',   label: '音频' },
  { id: 'doc',   label: '文档' },
  { id: 'zip',   label: '压缩包' },
  { id: 'other', label: '其他' },
];

const FILE_SORT_OPTIONS = [
  { id: 'mtime_desc', label: '最新上传' },
  { id: 'mtime_asc',  label: '最早上传' },
  { id: 'size_desc',  label: '大文件优先' },
  { id: 'size_asc',   label: '小文件优先' },
  { id: 'name_asc',   label: '名称 A → Z' },
  { id: 'name_desc',  label: '名称 Z → A' },
];

const CATEGORY_EXT_MAP = {
  img: new Set(['jpg','jpeg','png','gif','webp','heic','bmp','svg','avif','ico']),
  vid: new Set(['mp4','mov','mkv','avi','webm','flv','wmv','m4v']),
  aud: new Set(['mp3','wav','flac','aac','m4a','ogg','opus']),
  doc: new Set([
    'pdf',
    'doc','docx','rtf','odt',
    'xls','xlsx','csv','ods',
    'ppt','pptx','odp',
    'txt','md','markdown','log',
    'js','mjs','ts','tsx','jsx','py','rb','go','rs','java','kt','swift',
    'c','cc','cpp','h','hpp','cs','php','sh','bash','zsh','sql',
    'html','htm','css','scss','less','xml','json','yaml','yml','toml',
    'ini','conf','cfg','env','vue','svelte'
  ]),
  zip: new Set(['zip','rar','7z','tar','gz','bz2','xz']),
};

let pendingRestoreId = null;
let pendingBatchRestore = false;

/* =========================================================
   队列元数据持久化
   ========================================================= */

function saveQueue(){
  try{
    const items = queue
      .filter(q => q.status !== 'done')
      .map(q => ({
        id: q.id, name: q.fileName, size: q.fileSize,
        lastModified: q.fileLastModified,
        status: (q.status === 'uploading' || q.status === 'pending')
                  ? 'paused' : q.status,
        offset: q.offset || 0,
        target: q.target || '',
        targetLabel: q.targetLabel || '所有人',
      }));
    localStorage.setItem(QUEUE_KEY, JSON.stringify({ uid: uid, items: items }));
  }catch(e){}
}

function loadSavedQueue(){
  try{
    const raw = localStorage.getItem(QUEUE_KEY);
    if(!raw) return null;
    const data = JSON.parse(raw);
    if(!data || !Array.isArray(data.items)) return null;
    return data;
  }catch(e){ return null; }
}

function restoreQueue(){
  const data = loadSavedQueue();
  if(!data || !data.items.length) return;
  let maxId = uid;
  for(const it of data.items){
    if(!it || !it.name || !it.size) continue;
    if(it.status === 'done') continue;
    if(it.id > maxId) maxId = it.id;
    queue.push({
      id: it.id || (++uid), file: null,
      fileName: it.name, fileSize: it.size,
      fileLastModified: it.lastModified || 0,
      status: 'paused', offset: it.offset || 0,
      speed: 0, error: null, xhr: null,
      target: it.target || '',
      targetLabel: it.targetLabel || '所有人',
      needsFile: false, blobState: 'unknown',
    });
  }
  if(maxId > uid) uid = maxId;
  if(queue.length) renderQueue();
}

/* =========================================================
   发送目标
   ========================================================= */

function loadSavedTarget(){
  try{
    const v = localStorage.getItem(TARGET_KEY);
    return v === null ? '' : v;
  }catch(e){ return ''; }
}

function loadSavedTargetName(){
  try{ return localStorage.getItem(TARGET_NAME_KEY) || ''; }catch(e){ return ''; }
}

function saveTarget(v, name){
  try{
    localStorage.setItem(TARGET_KEY, v);
    if(typeof name === 'string') localStorage.setItem(TARGET_NAME_KEY, name);
  }catch(e){}
}

function currentTarget(){ return targetSelect.value; }

function targetLabel(){
  const v = currentTarget();
  if(v === '') return '所有人';
  const idx = targetSelect.selectedIndex;
  const opt = idx >= 0 ? targetSelect.options[idx] : null;
  if(opt && opt.dataset && opt.dataset.offline === '1'){
    return opt.textContent.replace(/^（已离线）\s*/, '') || v;
  }
  const c = clients.find(x => x.id === v);
  if(c) return c.name;
  return loadSavedTargetName() || v;
}

function updateDropzoneState(){
  const dz = $('dropzone');
  if(dz) dz.classList.remove('disabled');
}

targetSelect.addEventListener('change', () => {
  const idx = targetSelect.selectedIndex;
  const opt = idx >= 0 ? targetSelect.options[idx] : null;
  let name = '';
  if(opt){
    if(opt.dataset && opt.dataset.offline === '1'){
      name = loadSavedTargetName();
    } else if(opt.dataset && opt.dataset.name){
      name = opt.dataset.name;
    } else {
      name = opt.textContent.split(' · ')[0] || opt.textContent;
    }
  }
  saveTarget(targetSelect.value, name);
  updateDropzoneState();
  refreshTargetUI();
});

function refreshTargetUI(){
  const idx = targetSelect.selectedIndex;
  const cur = idx >= 0 ? targetSelect.options[idx] : null;

  let btnText = '所有人（共享）';
  if(cur){
    if(cur.dataset.offline === '1'){
      btnText = cur.textContent;
    } else if(cur.dataset.name){
      btnText = cur.dataset.name;
    } else {
      btnText = cur.textContent;
    }
  }
  targetBtnText.textContent = btnText;

  targetMenu.innerHTML = '';
  for(const opt of targetSelect.options){
    if(opt.value === '__none__') continue;

    const li = document.createElement('li');
    li.className = 'target-item';
    li.dataset.value = opt.value;
    li.setAttribute('role', 'option');
    if(opt === cur){
      li.classList.add('sel');
      li.setAttribute('aria-selected', 'true');
    }
    if(opt.dataset.offline === '1') li.classList.add('offline');

    let name, ip = '';
    if(opt.dataset.offline === '1'){
      name = opt.textContent;
    } else if(opt.dataset.name){
      name = opt.dataset.name;
      ip = opt.dataset.ip || '';
    } else {
      name = opt.textContent;
    }

    li.innerHTML =
      '<div class="target-item-name">' + escapeHtml(name) + '</div>' +
      (ip ? '<div class="target-item-ip">' + escapeHtml(ip) + '</div>' : '') +
      (opt === cur ? '<div class="target-item-check">✓</div>' : '');

    li.addEventListener('click', e => {
      e.stopPropagation();
      selectTarget(opt.value);
    });
    targetMenu.appendChild(li);
  }
}

function selectTarget(value){
  if(targetSelect.value === value){
    closeTargetMenu();
    return;
  }
  targetSelect.value = value;
  targetSelect.dispatchEvent(new Event('change', { bubbles: true }));
  closeTargetMenu();
}

function openTargetMenu(){
  refreshTargetUI();
  targetMenu.hidden = false;
  targetBtn.setAttribute('aria-expanded', 'true');
  setTimeout(() => {
    document.addEventListener('click', onDocClickClose, true);
    document.addEventListener('keydown', onEscClose, true);
  }, 0);
}

function closeTargetMenu(){
  if(targetMenu.hidden) return;
  targetMenu.hidden = true;
  targetBtn.setAttribute('aria-expanded', 'false');
  document.removeEventListener('click', onDocClickClose, true);
  document.removeEventListener('keydown', onEscClose, true);
}

function onDocClickClose(e){
  if(!targetMenu.hidden && !e.target.closest('#targetDropdown')){
    closeTargetMenu();
  }
}

function onEscClose(e){
  if(e.key === 'Escape' && !targetMenu.hidden){
    e.preventDefault();
    closeTargetMenu();
  }
}

targetBtn.addEventListener('click', e => {
  e.stopPropagation();
  if(targetMenu.hidden) openTargetMenu();
  else closeTargetMenu();
});

/* =========================================================
   上传队列 UI
   ========================================================= */

function statusText(item){
  if(item.needsFile) return '需要重新选择文件 · 已传 ' + fmtSize(item.offset || 0);
  if(item.blobState === 'loading') return '正在准备文件…';
  if(item.status === 'done')   return '✓ 完成';
  if(item.status === 'failed') return '✗ ' + (item.error || '失败');
  if(item.status === 'paused') return '⏸ 已暂停 · ' + fmtSize(item.offset);
  if(item.status === 'uploading'){
    const pct = item.fileSize ? (item.offset / item.fileSize * 100) : 0;
    let t = pct.toFixed(1) + '%';
    if(item.speed > 1024){
      const remain = (item.fileSize - item.offset) / item.speed;
      t += ' · ' + fmtSize(item.speed) + '/s · 剩余 ' + remain.toFixed(0) + 's';
    }
    return t;
  }
  if(item.offset > 0) return '等待续传 · 已传 ' + fmtSize(item.offset);
  return '等待中';
}

function renderQueue(){
  queuePanel.hidden = queue.length === 0;
  queueList.innerHTML = '';
  for(const item of queue) queueList.appendChild(buildQueueEl(item));
  updateQueueSummary();
  saveQueue();
}

function buildQueueEl(item){
  const li = document.createElement('li');
  li.className = 'qitem ' + (item.needsFile ? 'paused' : item.status);
  li.dataset.id = item.id;

  const head = document.createElement('div');
  head.className = 'qhead';

  const nameEl = document.createElement('div');
  nameEl.className = 'qname';
  nameEl.textContent = item.fileName || (item.file ? item.file.name : '?');
  nameEl.title = nameEl.textContent;

  const btn = document.createElement('button');
  btn.className = 'qbtn';
  if(item.status === 'done'){
    btn.innerHTML = iconSvg('check');
    btn.disabled = true;
  } else if(item.needsFile){
    btn.innerHTML = iconSvg('file');
    btn.title = '重新选择同一个文件以继续上传';
    btn.onclick = () => {
      pendingRestoreId = item.id;
      pendingBatchRestore = false;
      fileInput.click();
    };
  } else if(item.status === 'uploading' || item.status === 'pending'){
    btn.innerHTML = iconSvg('pause');
    btn.title = '暂停';
    btn.onclick = () => pauseItem(item.id);
  } else {
    btn.innerHTML = iconSvg('play');
    btn.title = '继续';
    btn.onclick = () => resumeItem(item.id);
  }

  const delBtn = document.createElement('button');
  delBtn.className = 'qbtn danger';
  delBtn.title = '移除';
  delBtn.innerHTML = iconSvg('close');
  delBtn.onclick = () => removeItem(item.id);

  head.append(nameEl, btn, delBtn);

  const bar = document.createElement('div');
  bar.className = 'qbar';
  const inner = document.createElement('div');
  const pct = item.fileSize ? (item.offset / item.fileSize * 100) : 0;
  inner.style.width = pct.toFixed(2) + '%';
  bar.appendChild(inner);

  const meta = document.createElement('div');
  meta.className = 'qmeta';
  const sizeEl = document.createElement('span');
  sizeEl.textContent = fmtSize(item.fileSize) + ' → ' + (item.targetLabel || '所有人');
  const statusEl = document.createElement('span');
  statusEl.className = 'qmeta-status';
  statusEl.textContent = statusText(item);
  meta.append(sizeEl, statusEl);

  li.append(head, bar, meta);
  return li;
}

function updateQueueItem(item){
  const el = queueList.querySelector('.qitem[data-id="' + item.id + '"]');
  if(!el) return;
  const bar = el.querySelector('.qbar > div');
  if(bar){
    const pct = item.fileSize ? (item.offset / item.fileSize * 100) : 0;
    bar.style.width = pct.toFixed(2) + '%';
  }
  const st = el.querySelector('.qmeta-status');
  if(st) st.textContent = statusText(item);
}

function updateQueueSummary(){
  if(!queue.length){ queueSummary.textContent = ''; return; }
  const total = queue.length;
  const done = queue.filter(q => q.status === 'done').length;
  const doneBytes = queue.filter(q => q.status === 'done')
                         .reduce((s,q) => s + q.fileSize, 0);
  const totalBytes = queue.reduce((s,q) => s + q.fileSize, 0);
  queueSummary.textContent = done + ' / ' + total
    + ' 完成 · ' + fmtSize(doneBytes) + ' / ' + fmtSize(totalBytes);
}

/* =========================================================
   文件选择器回调
   ========================================================= */

fileInput.addEventListener('change', () => {
  const list = Array.from(fileInput.files);
  const isBatch = pendingBatchRestore;
  const singleId = pendingRestoreId;
  pendingBatchRestore = false;
  pendingRestoreId = null;

  if(isBatch) batchBindFiles(list);
  else if(singleId !== null) bindSingleRestore(singleId, list);
  else addFiles(list);
  fileInput.value = '';
});

function bindSingleRestore(id, list){
  const item = queue.find(q => q.id === id);
  if(!item || !item.needsFile){ addFiles(list); return; }
  const idx = list.findIndex(f => f.name === item.fileName && f.size === item.fileSize);
  if(idx < 0){
    toast('选择的文件与待续传文件不一致，已按新文件处理');
    addFiles(list); return;
  }
  const f = list[idx];
  list.splice(idx, 1);
  item.file = f;
  item.fileLastModified = f.lastModified;
  item.needsFile = false;
  item.blobState = 'ready';
  item.status = 'pending';
  item.error = null;
  saveBlobToDB(item.fileName, item.fileSize, f).catch(() => {});
  if(list.length) addFiles(list);
  else { renderQueue(); startNextIfIdle(); }
}

function batchBindFiles(list){
  const needItems = queue.filter(q => q.needsFile);
  if(!needItems.length){ if(list.length) addFiles(list); return; }
  const remaining = new Set(needItems.map(q => q.id));
  const leftover = [];
  for(const f of list){
    const match = needItems.find(q =>
      remaining.has(q.id) && q.fileName === f.name && q.fileSize === f.size);
    if(match){
      match.file = f;
      match.fileLastModified = f.lastModified;
      match.needsFile = false;
      match.blobState = 'ready';
      match.status = 'pending';
      match.error = null;
      saveBlobToDB(match.fileName, match.fileSize, f).catch(() => {});
      remaining.delete(match.id);
    } else leftover.push(f);
  }
  const bound = needItems.length - remaining.size;
  if(bound > 0) toast('已恢复 ' + bound + ' 个文件');
  if(remaining.size > 0) toast('还有 ' + remaining.size + ' 个文件未选择');
  if(leftover.length) addFiles(leftover);
  else { renderQueue(); startNextIfIdle(); }
}

/* =========================================================
   新增文件
   ========================================================= */

function addFiles(fileList){
  if(!fileList || !fileList.length) return;
  const t = currentTarget();
  const label = targetLabel();
  const pendingSaves = [];
  let changed = false;

  for(const f of fileList){
    const need = queue.find(q =>
      q.needsFile && q.fileName === f.name && q.fileSize === f.size);
    if(need){
      need.file = f;
      need.fileLastModified = f.lastModified;
      need.needsFile = false;
      need.blobState = 'ready';
      need.status = 'pending';
      need.error = null;
      pendingSaves.push(saveBlobToDB(f.name, f.size, f).catch(() => {}));
      changed = true;
      continue;
    }
    const dup = queue.find(q =>
      !q.needsFile && q.fileName === f.name && q.fileSize === f.size &&
      q.fileLastModified === f.lastModified && q.status !== 'done');
    if(dup) continue;

    queue.push({
      id: ++uid, file: f, fileName: f.name, fileSize: f.size,
      fileLastModified: f.lastModified, status: 'pending',
      offset: 0, speed: 0, error: null, xhr: null,
      target: t, targetLabel: label,
      needsFile: false, blobState: 'ready',
    });
    pendingSaves.push(saveBlobToDB(f.name, f.size, f).catch(() => {}));
    changed = true;
  }

  if(changed){ renderQueue(); startNextIfIdle(); }
  Promise.all(pendingSaves).catch(() => {});
}

/* =========================================================
   按需从 IndexedDB 取回文件
   ========================================================= */

async function ensureFileLoaded(item){
  if(item.file) return true;
  if(item.blobState === 'missing') return false;
  item.blobState = 'loading';
  updateQueueItem(item);
  try{
    const rec = await loadBlobFromDB(item.fileName, item.fileSize);
    if(rec){
      const f = recordToFile(rec);
      if(f){
        item.file = f;
        item.fileLastModified = f.lastModified || item.fileLastModified;
        item.needsFile = false;
        item.blobState = 'ready';
        return true;
      }
    }
    item.blobState = 'missing';
    item.needsFile = true;
    return false;
  }catch(e){
    item.blobState = 'missing';
    item.needsFile = true;
    return false;
  }
}

/* =========================================================
   上传调度
   ========================================================= */

async function startNextIfIdle(){
  if(queue.some(q => q.status === 'uploading')) return;
  const next = queue.find(q => q.status === 'pending');
  if(!next){ updateQueueSummary(); return; }

  if(!next.file){
    const ok = await ensureFileLoaded(next);
    if(!ok){
      next.status = 'paused';
      renderQueue();
      startNextIfIdle();
      return;
    }
  }

  next.status = 'uploading';
  renderQueue();

  try{
    const st = await queryOffset(next.fileName, next.fileSize);
    if(st.complete){
      next.status = 'done';
      next.offset = next.fileSize;
      renderQueue();
      deleteBlobFromDB(next.fileName, next.fileSize).catch(() => {});
      refreshFiles(true);
      startNextIfIdle();
      return;
    }
    next.offset = st.offset || 0;
    next.speed = 0;
    renderQueue();

    const result = await uploadSlice(next);
    renderQueue();
    if(result === 'done'){
      deleteBlobFromDB(next.fileName, next.fileSize).catch(() => {});
      refreshFiles(true);
    }
    startNextIfIdle();
  }catch(e){
    next.status = 'failed';
    next.error = e.message || '未知错误';
    next.xhr = null;
    renderQueue();
    startNextIfIdle();
  }
}

async function queryOffset(name, size){
  const r = await fetch(
    '/upload/status?name=' + encodeURIComponent(name)
    + '&size=' + size
    + '&cid=' + encodeURIComponent(CLIENT_ID),
    {cache: 'no-store'});
  if(!r.ok) return {offset: 0, complete: false};
  return await r.json();
}

function uploadSlice(item){
  return new Promise(resolve => {
    const file = item.file;
    if(!file){ resolve('failed'); return; }
    const startOffset = item.offset || 0;
    const blob = file.slice(startOffset);

    const xhr = new XMLHttpRequest();
    item.xhr = xhr;

    let url = '/upload?name=' + encodeURIComponent(file.name)
            + '&size=' + file.size
            + '&offset=' + startOffset
            + '&cid=' + encodeURIComponent(CLIENT_ID);
    if(item.target) url += '&to=' + encodeURIComponent(item.target);

    xhr.open('POST', url);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    try{
      const devName = loadDeviceName();
      if(devName){
        xhr.setRequestHeader('X-Device-Name', encodeURIComponent(devName));
      }
    }catch(e){}

    let lastT = Date.now(), lastB = startOffset;

    xhr.upload.onprogress = e => {
      if(!e.lengthComputable) return;
      item.offset = startOffset + e.loaded;
      const now = Date.now();
      const dt = (now - lastT) / 1000;
      if(dt > 0.3){
        const inst = (item.offset - lastB) / dt;
        item.speed = item.speed ? (item.speed * 0.6 + inst * 0.4) : inst;
        lastT = now;
        lastB = item.offset;
      }
      updateQueueItem(item);
    };

    xhr.onload = () => {
      item.xhr = null;
      let resp = null;
      try { resp = JSON.parse(xhr.responseText); } catch(e) { resp = null; }
      if (resp && resp.complete === true) {
        item.status = 'done';
        item.offset = file.size;
        item.speed = 0;
        resolve('done');
        return;
      }
      if (resp && resp.complete === false) {
        item.status = 'paused';
        if (typeof resp.offset === 'number') item.offset = resp.offset;
        item.speed = 0;
        resolve('paused');
        return;
      }
      item.status = 'failed';
      item.error = 'HTTP ' + xhr.status;
      resolve('failed');
    };
    xhr.onerror = () => {
      item.xhr = null; item.status = 'failed';
      item.error = '网络错误'; resolve('failed');
    };
    xhr.onabort = () => {
      item.xhr = null; item.status = 'paused';
      item.speed = 0; resolve('paused');
    };
    xhr.send(blob);
  });
}

/* =========================================================
   暂停 / 继续 / 移除
   ========================================================= */

function pauseItem(id){
  const item = queue.find(q => q.id === id);
  if(!item || item.needsFile) return;
  if(item.status === 'uploading' && item.xhr) item.xhr.abort();
  else if(item.status === 'pending'){ item.status = 'paused'; renderQueue(); }
}

function resumeItem(id){
  const item = queue.find(q => q.id === id);
  if(!item) return;
  if(item.needsFile){
    pendingRestoreId = item.id;
    pendingBatchRestore = false;
    fileInput.click();
    return;
  }
  if(item.status !== 'paused' && item.status !== 'failed') return;
  item.status = 'pending';
  item.error = null;
  renderQueue();
  startNextIfIdle();
}

function pauseAll(){
  for(const item of queue){
    if(item.needsFile) continue;
    if(item.status === 'uploading' && item.xhr) item.xhr.abort();
    else if(item.status === 'pending') item.status = 'paused';
  }
  renderQueue();
}

async function resumeAll(){
  for(const item of queue){
    if(item.needsFile) continue;
    if(item.status === 'paused' || item.status === 'failed'){
      item.status = 'pending';
      item.error = null;
    }
  }
  renderQueue();

  const unknownItems = queue.filter(q => q.needsFile && q.status !== 'done');
  if(unknownItems.length){
    let recovered = 0;
    for(const item of unknownItems){
      const ok = await ensureFileLoaded(item);
      if(ok){ item.status = 'paused'; recovered++; }
    }
    if(recovered) toast('已从缓存恢复 ' + recovered + ' 个文件');
    const stillMissing = queue.filter(q => q.needsFile && q.status !== 'done');
    if(stillMissing.length){
      pendingBatchRestore = true;
      pendingRestoreId = null;
      for(const it of stillMissing) it.status = 'pending';
      renderQueue();
      toast('还有 ' + stillMissing.length + ' 个文件找不到缓存，请手动选择');
      fileInput.click();
      return;
    }
  }
  startNextIfIdle();
}

function clearDone(){
  queue = queue.filter(q => q.status !== 'done');
  renderQueue();
}

function notifyServerCancel(item){
  if(!item || item.status === 'done') return;
  const name = item.fileName;
  const size = item.fileSize;
  if(!name || !size) return;
  try{
    fetch('/upload/cancel', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ name, size, cid: CLIENT_ID }),
      keepalive: true,
    }).catch(() => {});
  }catch(e){}
}

function removeItem(id){
  const idx = queue.findIndex(q => q.id === id);
  if(idx < 0) return;
  const item = queue[idx];
  if(item.xhr){ try{ item.xhr.abort(); }catch(e){} }
  notifyServerCancel(item);
  deleteBlobFromDB(item.fileName, item.fileSize).catch(() => {});
  queue.splice(idx, 1);
  renderQueue();
  startNextIfIdle();
}

function clearAll(){
  if(!queue.length) return;
  for(const item of queue){
    if(item.xhr){ try{ item.xhr.abort(); }catch(e){} }
    notifyServerCancel(item);
    deleteBlobFromDB(item.fileName, item.fileSize).catch(() => {});
  }
  queue = [];
  renderQueue();
}

$('pauseAllBtn').onclick = pauseAll;
$('resumeAllBtn').onclick = resumeAll;
$('clearDoneBtn').onclick = clearDone;
$('clearAllBtn').onclick = clearAll;

/* =========================================================
   文件列表：工具栏
   ========================================================= */

const fileSearch = $('fileSearch');
const fileSearchClear = $('fileSearchClear');
const fileTabs = $('fileTabs');
const fileSortDropdown = $('fileSortDropdown');
const fileSortBtn = $('fileSortBtn');
const fileSortLabel = $('fileSortLabel');
const fileSortMenu = $('fileSortMenu');

const fileHeadNormal = $('fileHeadNormal');
const fileHeadSelect = $('fileHeadSelect');
const fileSelectBtn = $('fileSelectBtn');
const fileSelectExitBtn = $('fileSelectExitBtn');
const fileSelectedInfo = $('fileSelectedInfo');
const fileSelectAllBtn = $('fileSelectAllBtn');
const fileZipBtn = $('fileZipBtn');
const filesCard = document.querySelector('.files-card');

function fileRenderKey(f){
  return f.mtime + '|' + f.size + '|' + (f.added_at || 0) + '|' +
         (f.from_name || '') + '|' +
         (f.to_clients || []).join(',') + '|' +
         (f.to_names || []).join(',') + '|' +
         (f.is_mine ? '1' : '0');
}

const _HIDE_ORDER = 999999;

function renderFiles(){
  fileCount.textContent = filePage.total;
  updateFileTabCounts();
  pruneSelected();

  const list = files;
  const visibleNames = new Set(list.map(f => f.name));

  const emptyEl = fileList.querySelector('.empty, .files-no-result');
  if(emptyEl) emptyEl.remove();

  if(!list.length){
    for(const el of _fileElCache.values()){
      if(el.style.display !== 'none') el.style.display = 'none';
      el.style.order = String(_HIDE_ORDER);
    }
    if(!filePage.total){
      _fileElCache.clear();
      fileList.innerHTML =
        '<li class="empty">' +
        '<div class="empty-icon">' +
        '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
        '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>' +
        '<polyline points="14 2 14 8 20 8"/></svg>' +
        '</div>' +
        '还没有文件</li>';
    } else {
      const li = document.createElement('li');
      li.className = 'files-no-result';
      li.textContent = fileFilter.search
        ? '没有匹配的文件'
        : '该分类下暂无文件';
      fileList.appendChild(li);
    }
    updateFileSelectInfo(list);
    return;
  }

  for(let i = 0; i < list.length; i++){
    const f = list[i];
    const rk = fileRenderKey(f);
    let el = _fileElCache.get(f.name);

    if(!el || el._renderKey !== rk){
      const newEl = buildFileEl(f);
      newEl._renderKey = rk;
      if(el && el.parentNode){
        el.parentNode.replaceChild(newEl, el);
      }
      el = newEl;
      _fileElCache.set(f.name, el);
    }

    const isSelected = selectedFiles.has(f.name);
    if(el.classList.contains('selected') !== isSelected){
      el.classList.toggle('selected', isSelected);
      const chk = el.querySelector('.file-check');
      if(chk) chk.innerHTML = isSelected ? CHECK_SVG : '';
    }

    if(!el.parentNode){
      fileList.appendChild(el);
    }

    if(el.style.display === 'none') el.style.display = '';
    el.style.order = String(i);
  }

  for(const [name, el] of _fileElCache){
    if(!visibleNames.has(name)){
      if(el.style.display !== 'none') el.style.display = 'none';
      el.style.order = String(_HIDE_ORDER);
    }
  }

  updateFileSelectInfo(list);
}

function updateFileTabCounts(){
  const counts = filePage.counts;
  const tabs = fileTabs.querySelectorAll('.files-tab');
  for(const tab of tabs){
    const cat = tab.dataset.cat;
    const cnt = counts[cat] || 0;
    const cntEl = tab.querySelector('.files-tab-count');
    if(cntEl) cntEl.textContent = cnt;
    tab.classList.toggle('is-active', cat === fileFilter.category);
  }
}

function pruneSelected(){
  // 分页后不再本地清理，退出多选时统一清空
}

/* ---- 搜索框（debounce） ---- */
let _searchTimer = null;
fileSearch.addEventListener('input', () => {
  fileSearchClear.hidden = !fileSearch.value;
  clearTimeout(_searchTimer);
  _searchTimer = setTimeout(() => {
    fileFilter.search = fileSearch.value;
    filePage.offset = 0;
    refreshFiles(true);
  }, 300);
});
fileSearchClear.addEventListener('click', () => {
  clearTimeout(_searchTimer);
  fileSearch.value = '';
  fileFilter.search = '';
  fileSearchClear.hidden = true;
  filePage.offset = 0;
  refreshFiles(true);
  fileSearch.focus();
});

/* ---- 分类 tabs ---- */
fileTabs.addEventListener('click', e => {
  const tab = e.target.closest('.files-tab');
  if(!tab) return;
  const cat = tab.dataset.cat;
  if(fileFilter.category === cat) return;
  fileFilter.category = cat;
  saveFileFilter();
  filePage.offset = 0;

  for(const t of fileTabs.querySelectorAll('.files-tab')){
    t.classList.toggle('is-active', t.dataset.cat === cat);
  }

  refreshFiles(true);
});

/* ---- 排序下拉 ---- */

let _fileSortItemsBuilt = false;

function buildFileSortMenuOnce(){
  if(_fileSortItemsBuilt) return;
  fileSortMenu.innerHTML = '';

  for(const opt of FILE_SORT_OPTIONS){
    const li = document.createElement('li');
    li.className = 'files-sort-item';
    li.dataset.id = opt.id;
    li.setAttribute('role', 'option');
    li.setAttribute('aria-selected', 'false');

    const check = document.createElement('div');
    check.className = 'files-sort-item-check';

    const lbl = document.createElement('div');
    lbl.className = 'files-sort-item-label';
    lbl.textContent = opt.label;

    li.append(check, lbl);
    li.addEventListener('click', e => {
      e.stopPropagation();
      if(fileFilter.sort !== opt.id){
        fileFilter.sort = opt.id;
        saveFileFilter();
        updateFileSortMenuSelection();
        filePage.offset = 0;
        refreshFiles(true);
      }
      closeFileSortMenu();
    });
    fileSortMenu.appendChild(li);
  }
  _fileSortItemsBuilt = true;
}

function updateFileSortMenuSelection(){
  const cur = FILE_SORT_OPTIONS.find(o => o.id === fileFilter.sort)
              || FILE_SORT_OPTIONS[0];
  if(fileSortLabel.textContent !== cur.label){
    fileSortLabel.textContent = cur.label;
  }

  for(const li of fileSortMenu.children){
    const id = li.dataset.id;
    const isSel = (id === fileFilter.sort);
    const had = li.classList.contains('sel');
    if(isSel !== had){
      li.classList.toggle('sel', isSel);
      li.setAttribute('aria-selected', isSel ? 'true' : 'false');
      const check = li.querySelector('.files-sort-item-check');
      if(check) check.textContent = isSel ? '✓' : '';
    }
  }
}

function refreshFileSortUI(){
  buildFileSortMenuOnce();
  updateFileSortMenuSelection();
}

function warmupFileSortMenu(){
  buildFileSortMenuOnce();
  if(!fileSortMenu.hidden) return;

  fileSortMenu.style.visibility = 'hidden';
  fileSortMenu.hidden = false;
  void fileSortMenu.offsetHeight;
  fileSortMenu.hidden = true;
  fileSortMenu.style.visibility = '';
}

function openFileSortMenu(){
  buildFileSortMenuOnce();
  updateFileSortMenuSelection();
  fileSortMenu.hidden = false;
  fileSortBtn.setAttribute('aria-expanded', 'true');
  setTimeout(() => {
    document.addEventListener('click', onDocClickFileSort, true);
    document.addEventListener('keydown', onEscFileSort, true);
  }, 0);
}

function closeFileSortMenu(){
  if(fileSortMenu.hidden) return;
  fileSortMenu.hidden = true;
  fileSortBtn.setAttribute('aria-expanded', 'false');
  document.removeEventListener('click', onDocClickFileSort, true);
  document.removeEventListener('keydown', onEscFileSort, true);
}

function onDocClickFileSort(e){
  if(!fileSortMenu.hidden && !e.target.closest('#fileSortDropdown')){
    closeFileSortMenu();
  }
}

function onEscFileSort(e){
  if(e.key === 'Escape' && !fileSortMenu.hidden){
    e.preventDefault();
    closeFileSortMenu();
  }
}

fileSortBtn.addEventListener('click', e => {
  e.stopPropagation();
  if(fileSortMenu.hidden) openFileSortMenu();
  else closeFileSortMenu();
});

/* ---- 筛选状态持久化 ---- */
function loadFileFilter(){
  try{
    const raw = localStorage.getItem(FILE_FILTER_KEY);
    if(!raw) return;
    const data = JSON.parse(raw);
    if(!data || typeof data !== 'object') return;
    if(data.category && FILE_CATEGORIES.some(c => c.id === data.category)){
      fileFilter.category = data.category;
    }
    if(data.sort && FILE_SORT_OPTIONS.some(s => s.id === data.sort)){
      fileFilter.sort = data.sort;
    }
  }catch(e){}
}

function saveFileFilter(){
  try{
    localStorage.setItem(FILE_FILTER_KEY, JSON.stringify({
      category: fileFilter.category,
      sort: fileFilter.sort,
    }));
  }catch(e){}
}

/* =========================================================
   分页控件
   ========================================================= */

function renderPager(){
  const totalPages = Math.max(1, Math.ceil(filePage.total / filePage.limit));
  const curPage = Math.floor(filePage.offset / filePage.limit) + 1;

  if(filePage.total <= filePage.limit){
    filesPager.hidden = true;
    return;
  }
  filesPager.hidden = false;
  pagerInfo.textContent = curPage + ' / ' + totalPages;
  pagerFirst.disabled = (curPage <= 1);
  pagerPrev.disabled = (curPage <= 1);
  pagerNext.disabled = (curPage >= totalPages);
  pagerLast.disabled = (curPage >= totalPages);
}

function goToPage(page){
  const totalPages = Math.max(1, Math.ceil(filePage.total / filePage.limit));
  page = Math.max(1, Math.min(page, totalPages));
  const newOffset = (page - 1) * filePage.limit;
  if(newOffset === filePage.offset) return;
  filePage.offset = newOffset;
  refreshFiles(true);

  try{
    const card = filesCard;
    if(card){
      const top = card.getBoundingClientRect().top + window.scrollY - 12;
      window.scrollTo({top: top, behavior: 'smooth'});
    }
  }catch(e){}
}

pagerFirst.addEventListener('click', () => goToPage(1));
pagerPrev.addEventListener('click', () => {
  const curPage = Math.floor(filePage.offset / filePage.limit) + 1;
  goToPage(curPage - 1);
});
pagerNext.addEventListener('click', () => {
  const curPage = Math.floor(filePage.offset / filePage.limit) + 1;
  goToPage(curPage + 1);
});
pagerLast.addEventListener('click', () => {
  const totalPages = Math.max(1, Math.ceil(filePage.total / filePage.limit));
  goToPage(totalPages);
});

/* =========================================================
   多选模式
   ========================================================= */

function updateFileSelectInfo(list){
  if(!list) list = files;
  const n = selectedFiles.size;
  fileSelectedInfo.textContent = '已选 ' + n + ' 项';

  fileZipBtn.disabled = (n === 0);

  const allSelected = list.length > 0 &&
                      list.every(f => selectedFiles.has(f.name));
  fileSelectAllBtn.textContent = allSelected ? '取消全选' : '全选';
}

function enterFileSelectMode(){
  if(fileSelectMode) return;
  fileSelectMode = true;
  filesCard.classList.add('select-mode');
  fileHeadNormal.hidden = true;
  fileHeadSelect.hidden = false;
  updateFileSelectInfo();
}

function exitFileSelectMode(){
  if(!fileSelectMode) return;
  fileSelectMode = false;
  selectedFiles.clear();
  filesCard.classList.remove('select-mode');
  fileHeadNormal.hidden = false;
  fileHeadSelect.hidden = true;

  for(const el of _fileElCache.values()){
    if(el.classList.contains('selected')){
      el.classList.remove('selected');
      const chk = el.querySelector('.file-check');
      if(chk) chk.innerHTML = '';
    }
  }

  updateFileSelectInfo();
}

function toggleFileSelection(name){
  if(!fileSelectMode) return;

  if(selectedFiles.has(name)) selectedFiles.delete(name);
  else selectedFiles.add(name);

  const el = _fileElCache.get(name);
  if(el){
    const now = selectedFiles.has(name);
    el.classList.toggle('selected', now);
    const chk = el.querySelector('.file-check');
    if(chk) chk.innerHTML = now ? CHECK_SVG : '';
  }

  updateFileSelectInfo();
}

function toggleSelectAll(){
  const list = files;
  const allSelected = list.length > 0 &&
                      list.every(f => selectedFiles.has(f.name));
  if(allSelected){
    for(const f of list) selectedFiles.delete(f.name);
  } else {
    for(const f of list) selectedFiles.add(f.name);
  }

  for(const f of list){
    const el = _fileElCache.get(f.name);
    if(!el) continue;
    const now = selectedFiles.has(f.name);
    if(el.classList.contains('selected') !== now){
      el.classList.toggle('selected', now);
      const chk = el.querySelector('.file-check');
      if(chk) chk.innerHTML = now ? CHECK_SVG : '';
    }
  }

  updateFileSelectInfo(list);
}

async function downloadSelectedAsZip(){
  const names = Array.from(selectedFiles);
  if(!names.length) return;

  if(isWechat()){
    toast('微信内无法打包下载，请使用浏览器打开');
    return;
  }

  const btn = fileZipBtn;
  const origHTML = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '打包中…';

  try{
    const res = await fetch('/api/zip', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ names }),
    });

    if(!res.ok){
      let msg = 'HTTP ' + res.status;
      try{
        const j = await res.json();
        if(j && j.error) msg = j.error;
      }catch(e){}
      toast('打包失败：' + msg);
      return;
    }

    const blob = await res.blob();

    let filename = 'lanshare.zip';
    const cd = res.headers.get('Content-Disposition') || '';
    const m = /filename\*=UTF-8''([^;]+)/i.exec(cd);
    if(m){
      try{ filename = decodeURIComponent(m[1]); }catch(e){}
    }

    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1500);

    toast('已开始下载 ' + names.length + ' 个文件');
    exitFileSelectMode();
  }catch(e){
    toast('打包失败：' + (e.message || '未知错误'));
  }finally{
    btn.innerHTML = origHTML;
    updateFileSelectInfo();
  }
}

fileSelectBtn.addEventListener('click', enterFileSelectMode);
fileSelectExitBtn.addEventListener('click', exitFileSelectMode);
fileSelectAllBtn.addEventListener('click', toggleSelectAll);
fileZipBtn.addEventListener('click', downloadSelectedAsZip);

/* =========================================================
   文件列表数据拉取
   ========================================================= */

let _lastFilesKey = null;
let _filesReqId = 0;

async function refreshFiles(force){
  const myReq = ++_filesReqId;

  try{
    let url = '/api/files'
            + '?limit=' + filePage.limit
            + '&offset=' + filePage.offset
            + '&category=' + encodeURIComponent(fileFilter.category)
            + '&sort=' + encodeURIComponent(fileFilter.sort);
    if(fileFilter.search){
      url += '&search=' + encodeURIComponent(fileFilter.search);
    }

    const res = await fetch(url, {cache: 'no-cache'});
    if(!res.ok) return;

    const data = await res.json();
    if(myReq !== _filesReqId) return;

    const list = Array.isArray(data.items) ? data.items : [];
    const total = typeof data.total === 'number' ? data.total : 0;

    filePage.total = total;
    if(data.counts && typeof data.counts === 'object'){
      filePage.counts = data.counts;
    }

    if(total > 0 && filePage.offset >= total){
      const newOffset = Math.floor((total - 1) / filePage.limit) * filePage.limit;
      if(newOffset !== filePage.offset){
        filePage.offset = newOffset;
        return refreshFiles(true);
      }
    }
    if(total === 0 && filePage.offset > 0){
      filePage.offset = 0;
      return refreshFiles(true);
    }

    const key = list.map(f =>
      f.name + '\x00' + f.mtime + '\x00' + f.size + '\x00' +
      (f.added_at || 0) + '\x00' +
      (f.to_clients || []).join(',') + '\x00' + (f.is_mine ? '1' : '0')
    ).join('\x01')
      + '\x02' + filePage.offset
      + '\x02' + total
      + '\x02' + (filePage.counts.all || 0);

    if(!force && key === _lastFilesKey){
      return;
    }
    _lastFilesKey = key;

    files = list;

    const validNames = new Set(list.map(f => f.name));
    for(const [name, el] of _fileElCache){
      if(!validNames.has(name)){
        if(el.parentNode) el.parentNode.removeChild(el);
        _fileElCache.delete(name);
      }
    }

    renderFiles();
    renderPager();
  }catch(e){}
}

/* =========================================================
   缩略图延迟重试（应对"生成中"的占位图）
   ========================================================= */

const THUMB_PENDING_MARK  = 'thumb-pending';
const THUMB_RETRY_DELAY   = 800;
const THUMB_MAX_RETRY     = 15;

function _scheduleThumbRetry(img){
  if(img._thumbRetryTimer){
    clearTimeout(img._thumbRetryTimer);
    img._thumbRetryTimer = null;
  }
  if((img._thumbRetryCount || 0) >= THUMB_MAX_RETRY) return;
  if(!img.isConnected) return;

  img._thumbRetryCount = (img._thumbRetryCount || 0) + 1;
  img._thumbRetryTimer = setTimeout(() => {
    img._thumbRetryTimer = null;
    if(!img.isConnected) return;
    if(img._thumbReady) return;
    const base = img._thumbBaseSrc;
    if(!base) return;
    const sep = base.includes('?') ? '&' : '?';
    img.src = base + sep + '_r=' + Date.now();
  }, THUMB_RETRY_DELAY);
}

/* =========================================================
   文件图标 / 列表项
   ========================================================= */

function buildFileIcon(f){
  const kind = previewKind(f.name);
  const [cls, ext] = fileExtClass(f.name);

  if(kind === 'image'){
    const wrap = document.createElement('div');
    wrap.className = 'file-thumb';

    const img = document.createElement('img');
    img.loading = 'lazy';
    img.decoding = 'async';
    img.alt = '';

    const thumbUrl = '/thumb/' + encodeURIComponent(f.name)
                   + '?v=' + (f.mtime || 0);
    img._thumbBaseSrc = thumbUrl;
    img._thumbReady = false;
    img._thumbRetryCount = 0;
    img.src = thumbUrl;

    img.onload = () => {
      const cur = img.currentSrc || img.src || '';
      if(cur.indexOf(THUMB_PENDING_MARK) >= 0){
        img._thumbReady = false;
        _scheduleThumbRetry(img);
      } else {
        img._thumbReady = true;
        if(img._thumbRetryTimer){
          clearTimeout(img._thumbRetryTimer);
          img._thumbRetryTimer = null;
        }
      }
    };

    img.onerror = () => {
      wrap.className = 'file-icon' + (cls ? ' ' + cls : '');
      wrap.innerHTML = '';
      wrap.textContent = ext;
    };

    wrap.appendChild(img);
    return wrap;
  }

  const icon = document.createElement('div');
  icon.className = 'file-icon' + (cls ? ' ' + cls : '');
  icon.textContent = ext;
  return icon;
}

function buildFileEl(f){
  const li = document.createElement('li');
  li.className = 'file-item';
  li.dataset.name = f.name;

  const leading = document.createElement('div');
  leading.className = 'file-leading';

  const icon = buildFileIcon(f);
  const check = document.createElement('div');
  check.className = 'file-check';

  leading.append(icon, check);

  const meta = document.createElement('div');
  meta.className = 'file-meta';

  const nameEl = document.createElement('div');
  nameEl.className = 'file-name';
  nameEl.textContent = f.name;
  nameEl.title = '点击预览';

  const info = document.createElement('div');
  info.className = 'file-info';

  const sizeSpan = document.createElement('span');
  sizeSpan.textContent = fmtSize(f.size);
  info.appendChild(sizeSpan);
  info.appendChild(span('sep', '·'));
  info.appendChild(span(null, fmtTime(f.added_at || f.mtime)));

  if(f.from_name){
    info.appendChild(span('sep', '·'));
    info.appendChild(span(null, '来自 ' + f.from_name));
  }

  const toCount = (f.to_clients && f.to_clients.length) || 0;
  if(toCount > 0){
    const pill = document.createElement('span');
    pill.className = 'pill dir';
    if(f.is_mine){
      if(toCount === 1){
        const nm = (f.to_names && f.to_names[0]) || '指定设备';
        pill.textContent = '仅发给 ' + nm;
      } else {
        pill.textContent = '仅发给 ' + toCount + ' 台设备';
      }
    } else {
      pill.textContent = '发给你';
    }
    info.appendChild(pill);
  } else if(f.is_mine){
    const pill = document.createElement('span');
    pill.className = 'pill mine';
    pill.textContent = '我上传的 · 公开';
    info.appendChild(pill);
  }

  meta.append(nameEl, info);

  const actions = document.createElement('div');
  actions.className = 'file-actions';

  const a = document.createElement('a');
  a.className = 'icon-btn primary';
  a.href = downloadUrl(f.name);
  a.setAttribute('download', f.name);
  a.title = '下载';
  a.innerHTML = iconSvg('download');
  a.addEventListener('click', e => handleDownloadClick(e, f.name));
  actions.appendChild(a);

  if(f.is_mine){
    const permBtn = document.createElement('button');
    permBtn.className = 'icon-btn';
    permBtn.title = '谁可以看';
    permBtn.innerHTML = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      '<rect x="3" y="11" width="18" height="11" rx="2"/>' +
      '<path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>';
    permBtn.onclick = (e) => { e.stopPropagation(); openPermModal(f); };
    actions.appendChild(permBtn);
  }

  li.append(leading, meta, actions);

  li.addEventListener('click', e => {
    if(e.target.closest('button, a')) return;
    if(fileSelectMode){
      toggleFileSelection(f.name);
    } else if(e.target.closest('.file-name') ||
              e.target.closest('.file-thumb') ||
              e.target.closest('.file-icon')){
      openPreview(f);
    }
  });

  return li;
}

function span(cls, text){
  const s = document.createElement('span');
  if(cls) s.className = cls;
  s.textContent = text;
  return s;
}

/* =========================================================
   文件预览
   ========================================================= */

const PREVIEW_IMAGE_EXT = new Set(['jpg','jpeg','png','gif','webp','bmp','svg','ico','avif']);
const PREVIEW_VIDEO_EXT = new Set(['mp4','webm','ogv','mov']);
const PREVIEW_AUDIO_EXT = new Set(['mp3','wav','ogg','oga','m4a','flac','aac','opus']);
const PREVIEW_PDF_EXT   = new Set(['pdf']);
const PREVIEW_OOXML_EXT = new Set([
  'docx', 'docm', 'dotx', 'dotm',
  'doc', 'dot', 'rtf',
  'odt', 'ott', 'fodt',
  'xlsx', 'xlsm', 'xltx', 'xltm',
  'xls', 'xlsb',
  'ods', 'ots', 'fods',
  'pptx', 'pptm', 'potx', 'potm',
  'ppt', 'pps', 'ppsx', 'pot',
  'odp', 'otp', 'fodp',
]);

const PREVIEW_DOCX_EXT = new Set(['docx', 'dotx', 'docm', 'dotm']);
const PREVIEW_TEXT_EXT  = new Set([
  'txt','md','markdown','log','json','xml','yaml','yml','toml','ini','conf','cfg',
  'html','htm','css','scss','less','js','mjs','ts','tsx','jsx','vue','svelte',
  'py','rb','go','rs','java','kt','swift','c','cc','cpp','h','hpp','cs','php',
  'sh','bash','zsh','fish','bat','ps1','sql','csv','tsv','env','gitignore','editorconfig'
]);
const MAX_TEXT_PREVIEW = 2 * 1024 * 1024;

let previewMaskEl = null;
let previewCleanupFns = [];          // 整个预览生命周期（键盘监听、移除 mask）
let _previewBodyCleanupFns = [];     // 当前 body 内容的清理（每次切换前执行）

// 当前预览状态（用于切换时复用 DOM）
let _previewFile = null;
let _previewModal = null;
let _previewBody = null;
let _previewTitleEl = null;
let _previewSubEl = null;
let _previewPrevBtn = null;
let _previewNextBtn = null;
let _previewDlBtn = null;
let _previewNav = { prev: null, next: null };

function addBodyCleanup(fn){ _previewBodyCleanupFns.push(fn); }
function runBodyCleanups(){
  for(const fn of _previewBodyCleanupFns){
    try{ fn(); }catch(e){}
  }
  _previewBodyCleanupFns = [];
}

function extOf(name){
  const m = /\.([a-z0-9]+)$/i.exec(name || '');
  return m ? m[1].toLowerCase() : '';
}

function previewKind(name){
  const ext = extOf(name);
  if(PREVIEW_IMAGE_EXT.has(ext)) return 'image';
  if(PREVIEW_VIDEO_EXT.has(ext)) return 'video';
  if(PREVIEW_AUDIO_EXT.has(ext)) return 'audio';
  if(PREVIEW_PDF_EXT.has(ext))   return 'pdf';
  if(PREVIEW_OOXML_EXT.has(ext)) return 'ooxml';
  if(PREVIEW_TEXT_EXT.has(ext))  return 'text';
  if(!ext && name && !name.startsWith('.')) return 'text';
  return 'other';
}

/* ---------------- PDF.js 渲染（供 pdf 和 ooxml→pdf 共用） ---------------- */

function renderPdfInto(container, url, file){
  const wrap = document.createElement('div');
  wrap.className = 'preview-pdfjs';
  container.appendChild(wrap);

  let destroyed = false;
  addBodyCleanup(() => { destroyed = true; });

  const loading = document.createElement('div');
  loading.className = 'pdfjs-loading';
  loading.textContent = '正在加载 PDF…';
  wrap.appendChild(loading);

  (async () => {
    try {
      const pdfjsLib = await import(PDFJS_MODULE_URL);
      pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_WORKER_URL;

      const pdf = await pdfjsLib.getDocument({ url }).promise;
      if (destroyed) return;

      wrap.innerHTML = '';

      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const availW = Math.max(280, wrap.clientWidth - 24);

      for (let i = 1; i <= pdf.numPages; i++){
        if (destroyed) return;
        const page = await pdf.getPage(i);

        const baseVp = page.getViewport({ scale: 1 });
        const cssScale = Math.min(1, availW / baseVp.width);
        const cssVp = page.getViewport({ scale: cssScale });

        const canvas = document.createElement('canvas');
        canvas.className = 'pdfjs-page';
        canvas.style.width  = Math.floor(cssVp.width) + 'px';
        canvas.style.height = Math.floor(cssVp.height) + 'px';
        canvas.width  = Math.floor(cssVp.width  * dpr);
        canvas.height = Math.floor(cssVp.height * dpr);

        const ctx = canvas.getContext('2d');
        await page.render({
          canvasContext: ctx,
          viewport: page.getViewport({ scale: cssScale * dpr }),
        }).promise;

        if (destroyed) return;
        wrap.appendChild(canvas);
      }

      const tip = document.createElement('div');
      tip.className = 'pdfjs-pages';
      tip.textContent = '共 ' + pdf.numPages + ' 页';
      wrap.appendChild(tip);
    } catch (e){
      if (destroyed) return;
      wrap.innerHTML = '';
      showPreviewFallback(container, file,
        'PDF 加载失败：' + (e && e.message ? e.message : '未知错误'));
    }
  })();
}

/* ---------------- docx-preview 前端渲染 ---------------- */

async function renderDocxWithPreview(file, body, modal){
  const wrap = document.createElement('div');
  wrap.className = 'preview-docx';
  wrap.innerHTML = '<div class="pdfjs-loading">正在解析文档…</div>';
  body.appendChild(wrap);

  let aborted = false;
  addBodyCleanup(() => { aborted = true; });

  if(!window.docx || typeof window.docx.renderAsync !== 'function'){
    wrap.remove();
    fallbackOoxmlToServer(file, body, modal);
    return;
  }

  try {
    const res = await fetch(
      '/preview/' + encodeURIComponent(file.name),
      {cache: 'no-store'}
    );
    if(!res.ok) throw new Error('HTTP ' + res.status);
    const blob = await res.blob();
    if(aborted) return;

    wrap.innerHTML = '';

    await window.docx.renderAsync(blob, wrap, null, {
      className: 'docx-viewer',
      inWrapper: true,
      breakPages: true,
      renderHeaders: true,
      renderFooters: true,
      renderFootnotes: true,
      renderEndnotes: true,
      ignoreWidth: false,
      ignoreHeight: false,
      ignoreFonts: false,
      ignoreLastRenderedPageBreak: true,
      experimental: false,
      useBase64URL: false,
      debug: false,
    });
  } catch(e){
    if(aborted) return;
    wrap.remove();
    console.warn('docx-preview 失败，回退到服务端：', e);
    fallbackOoxmlToServer(file, body, modal);
  }
}

/* ---------------- 服务端兜底（xlsx / pptx / docx 回退共用） ---------------- */

function fallbackOoxmlToServer(file, body, modal){
  const wrap = document.createElement('div');
  wrap.className = 'preview-ooxml';
  wrap.innerHTML = '<div class="pdfjs-loading">正在解析文档…</div>';
  body.appendChild(wrap);

  let aborted = false;
  addBodyCleanup(() => { aborted = true; });

  fetch('/api/ooxml?name=' + encodeURIComponent(file.name),
        {cache: 'no-store'})
    .then(r => r.json().then(j => ({ok: r.ok, status: r.status, data: j})))
    .then(({ok, status, data}) => {
      if(aborted) return;
      if(!ok || !data || data.error){
        wrap.remove();
        showPreviewFallback(body, file,
          (data && data.error) || ('HTTP ' + status));
        return;
      }
      if(data.mode === 'pdf' && data.url){
        modal.classList.remove('kind-ooxml');
        modal.classList.add('kind-pdf');
        body.innerHTML = '';
        renderPdfInto(body, data.url, file);
      } else if(data.mode === 'html' && data.html){
        wrap.innerHTML = data.html;
      } else {
        wrap.remove();
        showPreviewFallback(body, file, '无法解析文档');
      }
    })
    .catch(e => {
      if(aborted) return;
      wrap.remove();
      showPreviewFallback(body, file,
        '加载失败：' + (e.message || '未知错误'));
    });
}

/* ---------------- 打开预览 ---------------- */

function openPreview(file){
  // 预览层已经打开 → 平滑切换，不重建骨架
  if(_previewFile && previewMaskEl && _previewModal){
    switchPreview(file);
    return;
  }
  _openPreviewFirstTime(file);
}

function _openPreviewFirstTime(file){
  const kind = previewKind(file.name);

  const mask = document.createElement('div');
  mask.className = 'preview-mask';

  const modal = document.createElement('div');
  modal.className = 'preview-modal kind-' + kind;

  const head = document.createElement('div');
  head.className = 'preview-head';

  const titleBox = document.createElement('div');
  titleBox.className = 'preview-title-box';

  const titleEl = document.createElement('div');
  titleEl.className = 'preview-title';

  const subEl = document.createElement('div');
  subEl.className = 'preview-sub';

  titleBox.append(titleEl, subEl);

  const actions = document.createElement('div');
  actions.className = 'preview-actions';

  // ---- 上一个 ----
  const prevBtn = document.createElement('button');
  prevBtn.type = 'button';
  prevBtn.className = 'preview-btn';
  prevBtn.innerHTML =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<polyline points="15 18 9 12 15 6"/></svg><span>上一个</span>';
  prevBtn.onclick = () => {
    if(_previewNav.prev) openPreview(_previewNav.prev);
  };

  // ---- 下一个 ----
  const nextBtn = document.createElement('button');
  nextBtn.type = 'button';
  nextBtn.className = 'preview-btn';
  nextBtn.innerHTML =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<polyline points="9 18 15 12 9 6"/></svg><span>下一个</span>';
  nextBtn.onclick = () => {
    if(_previewNav.next) openPreview(_previewNav.next);
  };

  // ---- 下载 ----
  const dl = document.createElement('a');
  dl.className = 'preview-btn primary';
  dl.innerHTML = iconSvg('download') + '<span>下载</span>';
  dl.addEventListener('click', e => {
    if(_previewFile) handleDownloadClick(e, _previewFile.name);
  });

  // ---- 关闭 ----
  const closeBtn = document.createElement('button');
  closeBtn.className = 'preview-btn';
  closeBtn.type = 'button';
  closeBtn.innerHTML = iconSvg('close') + '<span>关闭</span>';
  closeBtn.onclick = closePreview;

  actions.append(prevBtn, nextBtn, dl, closeBtn);
  head.append(titleBox, actions);

  const body = document.createElement('div');
  body.className = 'preview-body';

  modal.append(head, body);
  mask.appendChild(modal);

  mask.addEventListener('click', e => {
    if(e.target === mask) closePreview();
  });
  // ---- 触摸左右滑动切图（仅图片类型） ----
  let touchStartX = 0;
  let touchStartY = 0;
  let touchActive = false;

  mask.addEventListener('touchstart', e => {
    if(e.touches.length !== 1) return;
    touchStartX = e.touches[0].clientX;
    touchStartY = e.touches[0].clientY;
    touchActive = true;
  }, {passive: true});

  mask.addEventListener('touchend', e => {
    if(!touchActive) return;
    touchActive = false;
    if(e.changedTouches.length !== 1) return;
    if(!_previewFile) return;
    if(previewKind(_previewFile.name) !== 'image') return;

    const dx = e.changedTouches[0].clientX - touchStartX;
    const dy = e.changedTouches[0].clientY - touchStartY;

    // 水平位移要够大，且明显比垂直大，才认为是滑动
    if(Math.abs(dx) < 50) return;
    if(Math.abs(dy) > Math.abs(dx)) return;

    if(dx > 0 && _previewNav.prev){
      openPreview(_previewNav.prev);   // 右滑 → 上一张
    } else if(dx < 0 && _previewNav.next){
      openPreview(_previewNav.next);   // 左滑 → 下一张
    }
  }, {passive: true});
  
  document.body.appendChild(mask);
  document.body.classList.add('preview-open');

  document.addEventListener('keydown', onPreviewKey, true);
  previewCleanupFns.push(() => {
    document.removeEventListener('keydown', onPreviewKey, true);
  });

  // ---- 保存引用 ----
  previewMaskEl    = mask;
  _previewModal    = modal;
  _previewBody     = body;
  _previewTitleEl  = titleEl;
  _previewSubEl    = subEl;
  _previewPrevBtn  = prevBtn;
  _previewNextBtn  = nextBtn;
  _previewDlBtn    = dl;
  _previewFile     = null;

  // 首次渲染内容
  switchPreview(file);
}

/* 预加载当前图片前后各 N 张图片，让切换无网络等待 */
const _PRELOAD_RANGE = 2;

function preloadAdjacentImages(idx){
  if(idx < 0) return;
  for(let d = 1; d <= _PRELOAD_RANGE; d++){
    if(idx - d >= 0){
      const f = files[idx - d];
      if(f && previewKind(f.name) === 'image'){
        const im = new Image();
        im.decoding = 'async';
        im.src = '/preview/' + encodeURIComponent(f.name);
      }
    }
    if(idx + d < files.length){
      const f = files[idx + d];
      if(f && previewKind(f.name) === 'image'){
        const im = new Image();
        im.decoding = 'async';
        im.src = '/preview/' + encodeURIComponent(f.name);
      }
    }
  }
}

function switchPreview(file){
  const modal = _previewModal;
  const body  = _previewBody;
  if(!modal || !body) return;

  // 1. 清理上一次 body 里的资源
  runBodyCleanups();

  // 2. 计算上一个/下一个（与当前列表顺序一致）
  const idx = files.findIndex(f => f.name === file.name);
  const prevFile = (idx > 0) ? files[idx - 1] : null;
  const nextFile = (idx >= 0 && idx < files.length - 1) ? files[idx + 1] : null;
  _previewNav = { prev: prevFile, next: nextFile };

  // 3. 更新标题栏
  _previewTitleEl.textContent = file.name;
  _previewTitleEl.title = file.name;

  let subText = fmtSize(file.size) + ' · ' +
                fmtTime(file.added_at || file.mtime);
  if(idx >= 0 && files.length > 1){
    subText += ' · ' + (idx + 1) + ' / ' + files.length;
  }
  _previewSubEl.textContent = subText;

  // 4. 更新按钮状态
  _previewPrevBtn.disabled = !prevFile;
  _previewPrevBtn.title = prevFile
    ? ('上一个：' + prevFile.name + '  (←)')
    : '已经是第一个';

  _previewNextBtn.disabled = !nextFile;
  _previewNextBtn.title = nextFile
    ? ('下一个：' + nextFile.name + '  (→)')
    : '已经是最后一个';

  _previewDlBtn.href = downloadUrl(file.name);
  _previewDlBtn.setAttribute('download', file.name);

  // 5. 更新 modal kind（只有变化时才写，避免重排）
  const kind = previewKind(file.name);
  const newCls = 'preview-modal kind-' + kind;
  if(modal.className !== newCls){
    modal.className = newCls;
  }

  // 6. 清空 body 并重绘
  body.innerHTML = '';

  // 图片类：不做淡入动画（预加载后直接出现，更接近原生手感）
  // 其他类型：保留淡淡的过渡
  if(kind === 'image'){
    body.classList.remove('preview-switching');
  } else {
    body.classList.remove('preview-switching');
    void body.offsetWidth;
    body.classList.add('preview-switching');
  }

  _previewFile = file;
  renderPreviewContent(file, body, modal);

  // 7. 预加载相邻图片
  if(kind === 'image'){
    preloadAdjacentImages(idx);
  }
}

function renderPreviewContent(file, body, modal){
  const kind = previewKind(file.name);
  const previewUrl = '/preview/' + encodeURIComponent(file.name);

  if(kind === 'image'){
    const wrap = document.createElement('div');
    wrap.className = 'preview-image-wrap';

    const img = document.createElement('img');
    img.className = 'preview-image';
    img.alt = file.name;
    img.decoding = 'async';
    img.draggable = false;
    img.onerror = () => showPreviewFallback(body, file, '图片加载失败');
    img.onload = () => {
      requestAnimationFrame(() => setupImageZoom(wrap, img, body));
    };

    wrap.appendChild(img);
    body.appendChild(wrap);

    // wrap 自己处理点击遮罩关闭（点击空白区域）
    img.src = previewUrl;
  }else if(kind === 'video'){
    const v = document.createElement('video');
    v.className = 'preview-video';
    v.src = previewUrl;
    v.controls = true;
    v.autoplay = false;
    v.preload = 'metadata';
    body.appendChild(v);
    addBodyCleanup(() => { try{ v.pause(); v.src = ''; }catch(e){} });

  } else if(kind === 'audio'){
    const wrap = document.createElement('div');
    wrap.className = 'preview-audio-wrap';
    const ic = document.createElement('div');
    ic.className = 'preview-audio-icon';
    ic.innerHTML = iconSvg('music');
    const a = document.createElement('audio');
    a.src = previewUrl;
    a.controls = true;
    a.preload = 'metadata';
    wrap.append(ic, a);
    body.appendChild(wrap);
    addBodyCleanup(() => { try{ a.pause(); a.src = ''; }catch(e){} });

  } else if(kind === 'pdf'){
    renderPdfInto(body, previewUrl, file);

  } else if(kind === 'ooxml'){
    const ext = extOf(file.name);
    if(PREVIEW_DOCX_EXT.has(ext)){
      renderDocxWithPreview(file, body, modal);
    } else {
      fallbackOoxmlToServer(file, body, modal);
    }

  } else if(kind === 'text'){
    if(file.size > MAX_TEXT_PREVIEW){
      showPreviewFallback(body, file,
        '文件过大，不支持在线预览（' + fmtSize(file.size) + '）');
    } else {
      const pre = document.createElement('pre');
      pre.className = 'preview-text';
      pre.textContent = '加载中…';
      body.appendChild(pre);
      let aborted = false;
      addBodyCleanup(() => { aborted = true; });
      fetch(previewUrl, {cache: 'no-store'})
        .then(r => {
          if(!r.ok) throw new Error('HTTP ' + r.status);
          return r.text();
        })
        .then(text => {
          if(aborted) return;
          pre.textContent = text || '(空文件)';
        })
        .catch(e => {
          if(aborted) return;
          pre.textContent = '加载失败：' + (e.message || '未知错误');
          pre.classList.add('error');
        });
    }

  } else {
    showPreviewFallback(body, file, '该文件类型暂不支持在线预览');
  }
}

/* =========================================================
   图片缩放 / 平移 / 双击
   ---------------------------------------------------------
   - PC:   滚轮缩放（以光标为中心）、双击 1x↔2x、
           放大后按住左键拖拽平移
   - 触屏: 双指捏合缩放（以双指中心为焦点）、
           单指拖拽平移（仅放大后）、双击 1x↔2x
   - 切换到其他文件时，wrap 会被重建，所以状态自然重置
   ========================================================= */

function setupImageZoom(wrap, img, body){
  const MIN_SCALE = 1;
  const MAX_SCALE = 8;
  const DBL_SCALE = 2;
  const DBL_TAP_MS = 280;
  const WHEEL_STEP = 1.15;

  const state = {
    scale: 1,
    tx: 0,
    ty: 0,
    imgW: 0,
    imgH: 0,
    wrapW: 0,
    wrapH: 0,
    wrapLeft: 0,
    wrapTop: 0,
  };

  // ---------------------------------------------------------
  //  rAF 节流：事件里只改 state，写 DOM 交给下一帧
  // ---------------------------------------------------------
  let _applyRaf = 0;
  function scheduleApply(){
    if(_applyRaf) return;
    _applyRaf = requestAnimationFrame(() => {
      _applyRaf = 0;
      apply();
    });
  }
  function cancelApply(){
    if(_applyRaf){
      cancelAnimationFrame(_applyRaf);
      _applyRaf = 0;
    }
  }

  // ---------------------------------------------------------
  //  缩放提示气泡
  // ---------------------------------------------------------
  const badge = document.createElement('div');
  badge.className = 'preview-zoom-badge';
  wrap.appendChild(badge);
  let badgeTimer = null;
  function showBadge(){
    badge.textContent = Math.round(state.scale * 100) + '%';
    badge.classList.add('show');
    if(badgeTimer) clearTimeout(badgeTimer);
    badgeTimer = setTimeout(() => {
      badge.classList.remove('show');
      badgeTimer = null;
    }, 900);
  }

  // ---------------------------------------------------------
  //  测量（缓存 rect，避免高频 touchmove 里 layout thrashing）
  // ---------------------------------------------------------
  function measure(){
    const r = wrap.getBoundingClientRect();
    state.wrapW = r.width;
    state.wrapH = r.height;
    state.wrapLeft = r.left;
    state.wrapTop = r.top;
    state.imgW = img.clientWidth;
    state.imgH = img.clientHeight;
  }

  // ---------------------------------------------------------
  //  平移边界限制
  // ---------------------------------------------------------
  function clampT(tx, ty, scale){
    const dw = state.imgW * scale;
    const dh = state.imgH * scale;
    const maxTx = Math.max(0, (dw - state.wrapW) / 2);
    const maxTy = Math.max(0, (dh - state.wrapH) / 2);
    if(tx >  maxTx) tx =  maxTx;
    if(tx < -maxTx) tx = -maxTx;
    if(ty >  maxTy) ty =  maxTy;
    if(ty < -maxTy) ty = -maxTy;
    return [tx, ty];
  }

  // ---------------------------------------------------------
  //  实际写 DOM（只在 rAF 回调里跑）
  // ---------------------------------------------------------
  let _wasZoomed = false;
  function apply(){
    const c = clampT(state.tx, state.ty, state.scale);
    state.tx = c[0];
    state.ty = c[1];
    img.style.transform =
      'translate3d(' + state.tx + 'px,' + state.ty + 'px,0) ' +
      'scale(' + state.scale + ')';

    // 只在状态真正翻转时才动 class，避免每帧触样式重算
    const isZoomed = state.scale > 1.001;
    if(isZoomed !== _wasZoomed){
      _wasZoomed = isZoomed;
      wrap.classList.toggle('is-zoomed', isZoomed);
    }
  }

  // ---------------------------------------------------------
  //  以 (px, py) 为焦点缩放，px/py 是 wrap 内坐标
  // ---------------------------------------------------------
  function zoomAt(px, py, newScale){
    newScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, newScale));
    if(Math.abs(newScale - state.scale) < 0.0001) return;

    const cx = state.wrapW / 2;
    const cy = state.wrapH / 2;
    const k = newScale / state.scale;
    const dx = px - cx;
    const dy = py - cy;
    state.tx = dx - (dx - state.tx) * k;
    state.ty = dy - (dy - state.ty) * k;
    state.scale = newScale;
    scheduleApply();
  }

  function reset(){
    state.scale = 1;
    state.tx = 0;
    state.ty = 0;
    scheduleApply();
  }

  measure();
  apply();

  // =========================================================
  //  PC: 滚轮缩放
  // =========================================================
  const onWheel = e => {
    e.preventDefault();
    const mx = e.clientX - state.wrapLeft;
    const my = e.clientY - state.wrapTop;
    const factor = e.deltaY < 0 ? WHEEL_STEP : (1 / WHEEL_STEP);
    zoomAt(mx, my, state.scale * factor);
    showBadge();
  };
  wrap.addEventListener('wheel', onWheel, {passive: false});

  // =========================================================
  //  PC: 双击 1x ↔ 2x
  // =========================================================
  const onDblClick = e => {
    e.preventDefault();
    if(state.scale > 1.001){
      reset();
    } else {
      zoomAt(e.clientX - state.wrapLeft,
             e.clientY - state.wrapTop,
             DBL_SCALE);
    }
    showBadge();
  };
  wrap.addEventListener('dblclick', onDblClick);

  // =========================================================
  //  PC: 拖拽平移（仅放大后）
  // =========================================================
  let dragging = false;
  let dragStart = null;

  const onMouseDown = e => {
    if(e.button !== 0) return;
    if(state.scale <= 1.001) return;
    dragging = true;
    dragStart = {
      x: e.clientX,
      y: e.clientY,
      tx: state.tx,
      ty: state.ty,
    };
    img.style.transition = 'none';
    // 强制同步 flush：让浏览器立刻把上面这行 transition:none 应用上，
    // 否则头几个 mousemove 里还带着 0.2s 的补间，手感会发黏
    void img.offsetWidth;
    wrap.classList.add('is-dragging');
    e.preventDefault();
  };

  const onMouseMove = e => {
    if(!dragging) return;
    state.tx = dragStart.tx + (e.clientX - dragStart.x);
    state.ty = dragStart.ty + (e.clientY - dragStart.y);
    // PC 端 mousemove 已被浏览器按帧合并，直接同步 apply 最跟手。
    // 若用 rAF 再节流，相当于每次再推迟一帧，会明显"黏"。
    apply();
  };

  const onMouseUp = () => {
    if(!dragging) return;
    dragging = false;
    dragStart = null;
    img.style.transition = '';
    wrap.classList.remove('is-dragging');
  };

  wrap.addEventListener('mousedown', onMouseDown);
  window.addEventListener('mousemove', onMouseMove);
  window.addEventListener('mouseup', onMouseUp);

  // =========================================================
  //  触屏: 单指平移 / 双指捏合 / 双击
  // =========================================================
  let touch = null;
  let lastTapTime = 0;

  const onTouchStart = e => {
    if(e.touches.length === 1){
      const t = e.touches[0];
      touch = {
        mode: 'pan',
        startX: t.clientX,
        startY: t.clientY,
        startTx: state.tx,
        startTy: state.ty,
        moved: false,
      };
      img.style.transition = 'none';
    } else if(e.touches.length === 2){
      const t1 = e.touches[0];
      const t2 = e.touches[1];
      const dist = Math.hypot(t1.clientX - t2.clientX,
                              t1.clientY - t2.clientY);
      const cx = (t1.clientX + t2.clientX) / 2;
      const cy = (t1.clientY + t2.clientY) / 2;
      touch = {
        mode: 'pinch',
        startDist: dist,
        startScale: state.scale,
        startTx: state.tx,
        startTy: state.ty,
        startCx: cx,
        startCy: cy,
      };
      img.style.transition = 'none';
    }
  };

  const onTouchMove = e => {
    if(!touch) return;

    if(touch.mode === 'pan' && e.touches.length === 1){
      const t = e.touches[0];
      const dx = t.clientX - touch.startX;
      const dy = t.clientY - touch.startY;
      if(Math.abs(dx) > 4 || Math.abs(dy) > 4) touch.moved = true;
      if(state.scale <= 1.001) return;
      state.tx = touch.startTx + dx;
      state.ty = touch.startTy + dy;
      scheduleApply();          // ← 关键
      e.preventDefault();

    } else if(touch.mode === 'pinch' && e.touches.length === 2){
      const t1 = e.touches[0];
      const t2 = e.touches[1];
      const dist = Math.hypot(t1.clientX - t2.clientX,
                              t1.clientY - t2.clientY);
      const cx = (t1.clientX + t2.clientX) / 2;
      const cy = (t1.clientY + t2.clientY) / 2;

      const ratio = dist / Math.max(1, touch.startDist);
      let newScale = touch.startScale * ratio;
      newScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, newScale));

      // 用缓存的 wrapLeft/wrapTop，避免 getBoundingClientRect 触发同步 layout
      const smx = touch.startCx - state.wrapLeft;
      const smy = touch.startCy - state.wrapTop;
      const wcx = state.wrapW / 2;
      const wcy = state.wrapH / 2;
      const k = newScale / touch.startScale;

      let tx = (smx - wcx) - ((smx - wcx) - touch.startTx) * k;
      let ty = (smy - wcy) - ((smy - wcy) - touch.startTy) * k;
      tx += (cx - touch.startCx);
      ty += (cy - touch.startCy);

      state.scale = newScale;
      state.tx = tx;
      state.ty = ty;
      scheduleApply();          // ← 关键
      e.preventDefault();
      showBadge();
    }
  };

  const onTouchEnd = e => {
    if(!touch) return;

    if(touch.mode === 'pan' && !touch.moved && e.touches.length === 0){
      const now = Date.now();
      if(now - lastTapTime < DBL_TAP_MS){
        if(state.scale > 1.001){
          reset();
        } else {
          zoomAt(state.wrapW / 2, state.wrapH / 2, DBL_SCALE);
        }
        showBadge();
        lastTapTime = 0;
      } else {
        lastTapTime = now;
      }
    }

    if(e.touches.length === 0){
      touch = null;
      img.style.transition = '';
    }
  };

  wrap.addEventListener('touchstart', onTouchStart, {passive: true});
  wrap.addEventListener('touchmove',  onTouchMove,  {passive: false});
  wrap.addEventListener('touchend',   onTouchEnd,   {passive: true});
  wrap.addEventListener('touchcancel', () => {
    touch = null;
    img.style.transition = '';
  }, {passive: true});

  // =========================================================
  //  尺寸变化时重新测量（旋转屏幕 / 改窗口大小）
  // =========================================================
  let resizeTimer = null;
  const onResize = () => {
    if(resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      measure();
      apply();
    }, 120);
  };
  window.addEventListener('resize', onResize);

  // =========================================================
  //  清理
  // =========================================================
  addBodyCleanup(() => {
    cancelApply();
    window.removeEventListener('mousemove', onMouseMove);
    window.removeEventListener('mouseup', onMouseUp);
    window.removeEventListener('resize', onResize);
    if(badgeTimer) clearTimeout(badgeTimer);
    if(resizeTimer) clearTimeout(resizeTimer);
  });
}

function onPreviewKey(e){
  if(e.key === 'Escape'){
    e.preventDefault();
    closePreview();
    return;
  }
  if(e.key === 'ArrowLeft' && _previewNav.prev){
    e.preventDefault();
    openPreview(_previewNav.prev);
    return;
  }
  if(e.key === 'ArrowRight' && _previewNav.next){
    e.preventDefault();
    openPreview(_previewNav.next);
  }
}

function closePreview(){
  // 先跑 body 级别清理（停视频等）
  runBodyCleanups();

  // 再跑整个预览级别清理（键盘监听）
  for(const fn of previewCleanupFns){
    try{ fn(); }catch(e){}
  }
  previewCleanupFns = [];

  if(previewMaskEl){
    try{ previewMaskEl.remove(); }catch(e){}
    previewMaskEl = null;
  }

  // 清空所有缓存引用
  _previewModal   = null;
  _previewBody    = null;
  _previewTitleEl = null;
  _previewSubEl   = null;
  _previewPrevBtn = null;
  _previewNextBtn = null;
  _previewDlBtn   = null;
  _previewFile    = null;
  _previewNav     = { prev: null, next: null };

  document.body.classList.remove('preview-open');
}

function showPreviewFallback(body, file, msg){
  body.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'preview-fallback';
  const ic = document.createElement('div');
  ic.className = 'preview-fallback-icon';
  ic.innerHTML = iconSvg('file');
  const text = document.createElement('div');
  text.className = 'preview-fallback-text';
  text.textContent = msg;
  const btn = document.createElement('a');
  btn.className = 'preview-btn primary';
  btn.href = downloadUrl(file.name);
  btn.setAttribute('download', file.name);
  btn.innerHTML = iconSvg('download') + '<span>下载文件</span>';
  btn.addEventListener('click', e => handleDownloadClick(e, file.name));
  wrap.append(ic, text, btn);
  body.appendChild(wrap);
}

/* =========================================================
   扫码连接
   ========================================================= */

let _qrModalEl = null;

function _onQrKey(e){
  if(e.key === 'Escape'){ e.preventDefault(); closeQrModal(); }
}

function closeQrModal(){
  document.removeEventListener('keydown', _onQrKey, true);
  if(_qrModalEl){
    try{ _qrModalEl.remove(); }catch(e){}
    _qrModalEl = null;
  }
}

async function copyText(text){
  try{
    if(navigator.clipboard && navigator.clipboard.writeText){
      await navigator.clipboard.writeText(text);
      return true;
    }
  }catch(e){}
  try{
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;top:-9999px;opacity:0;';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  }catch(e){ return false; }
}

async function openQrModal(){
  closeQrModal();

  const mask = document.createElement('div');
  mask.className = 'modal-mask';
  mask.innerHTML =
    '<div class="modal qr-modal">' +
      '<h3>扫码连接</h3>' +
      '<p class="qr-modal-hint">用相机或浏览器扫描，即可打开同一页面</p>' +
      '<div class="qr-modal-box" id="qrModalBox">' +
        '<div class="qr-modal-loading">生成中…</div>' +
      '</div>' +
      '<div class="qr-modal-url" id="qrModalUrl"></div>' +
      '<div class="modal-actions" style="justify-content:center;">' +
        '<button type="button" data-act="copy">复制链接</button>' +
        '<button type="button" class="primary" data-act="close">关闭</button>' +
      '</div>' +
    '</div>';

  const box   = mask.querySelector('#qrModalBox');
  const urlEl = mask.querySelector('#qrModalUrl');
  let qrUrl = '';

  mask.addEventListener('click', e => {
    if(e.target === mask){ closeQrModal(); return; }
    const btn = e.target.closest('[data-act]');
    if(!btn) return;
    const act = btn.dataset.act;
    if(act === 'close') closeQrModal();
    else if(act === 'copy'){
      if(!qrUrl){ toast('链接尚未生成'); return; }
      copyText(qrUrl).then(ok =>
        toast(ok ? '已复制，去粘贴给其他设备' : '复制失败，请长按选择'));
    }
  });

  document.body.appendChild(mask);
  _qrModalEl = mask;
  document.addEventListener('keydown', _onQrKey, true);

  try{
    const r = await fetch('/api/qr.json', {cache: 'no-store'});
    if(!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    if(!_qrModalEl) return;
    qrUrl = j.url || '';
    urlEl.textContent = qrUrl;
    box.innerHTML = j.svg
      ? j.svg
      : '<div class="qr-modal-loading">二维码生成失败</div>';
  }catch(e){
    if(!_qrModalEl) return;
    box.innerHTML = '<div class="qr-modal-loading">二维码生成失败</div>';
    urlEl.textContent = e.message || '加载失败';
  }
}

$('qrOpenBtn').addEventListener('click', openQrModal);

/* =========================================================
   在线设备 & 发送目标
   ========================================================= */

const peerPill = $('peerPill');
const peerPillText = $('peerPillText');

let _clientsLoaded = false;

async function refreshClients(){
  try{
    const r = await fetch('/api/clients', {cache: 'no-store'});
    const list = await r.json();
    clients = list || [];
    _clientsLoaded = true;
    renderTargets();
    renderPeerPill();
  }catch(e){}
}

let _lastTargetsKey = null;
let _lastWarnedOffline = null;

function renderTargets(){
  const prevSel = targetSelect.value;
  const savedSel = loadSavedTarget();
  let wanted;
  if(prevSel && prevSel !== '__none__') wanted = prevSel;
  else wanted = savedSel;

  const others = clients.filter(c => !c.is_self);
  const fp = others.map(c => c.id + ':' + c.name).join('|');

  if(fp !== _lastTargetsKey){
    _lastTargetsKey = fp;
    targetSelect.innerHTML = '';

    const optAll = document.createElement('option');
    optAll.value = '';
    optAll.textContent = '所有人（共享）';
    optAll.dataset.name = '所有人（共享）';
    optAll.dataset.kind = 'all';
    targetSelect.appendChild(optAll);

    for(const c of others){
      const opt = document.createElement('option');
      opt.value = c.id;
      opt.textContent = c.name + ' · ' + c.ip;
      opt.dataset.name = c.name;
      opt.dataset.ip = c.ip;
      opt.dataset.kind = 'device';
      targetSelect.appendChild(opt);
    }
  }

  const has = [...targetSelect.options].some(o => o.value === wanted);
  if(has) targetSelect.value = wanted;
  else if(wanted){
    const oldOffline = targetSelect.querySelector('option[data-offline="1"]');
    if(oldOffline) oldOffline.remove();
    const off = document.createElement('option');
    off.value = wanted;
    off.dataset.offline = '1';
    const savedName = loadSavedTargetName();
    off.textContent = savedName ? '（已离线）' + savedName : '（已离线）' + wanted;
    targetSelect.appendChild(off);
    targetSelect.value = wanted;
    if(_clientsLoaded && _lastWarnedOffline !== wanted){
      _lastWarnedOffline = wanted;
      toast('之前选择的接收者已离线，文件暂不会发送');
    }
  } else targetSelect.value = '';

  updateDropzoneState();

  const chipName = $('deviceChipName');
  const chipEl   = $('deviceChip');
  const self = clients.find(c => c.is_self);
  const label = self ? self.name : '识别中…';
  if(chipName && chipName.textContent !== label){
    chipName.textContent = label;
  }
  if(chipEl){
    chipEl.title = self
      ? '点击修改设备名：' + self.name
      : '点击修改设备名';
  }

  refreshTargetUI();
}

function renderPeerPill(){
  const n = clients.length;
  if(n === 0) peerPill.hidden = true;
  else {
    peerPill.hidden = false;
    peerPillText.textContent = n + ' 台设备在线';
  }
}

/* =========================================================
   谁可以看（权限弹窗）
   ========================================================= */

function openPermModal(file){
  const others = clients.filter(c => !c.is_self);
  const mask = document.createElement('div');
  mask.className = 'modal-mask';

  const modal = document.createElement('div');
  modal.className = 'modal';

  const currentTo = new Set(file.to_clients || []);
  let publicMode = currentTo.size === 0;
  const selectedSet = new Set(currentTo);

  modal.innerHTML =
    '<h3>谁可以看</h3>' +
    '<div class="modal-file">' + escapeHtml(file.name) + '</div>' +
    '<div class="perm-modes">' +
      '<label class="perm-mode">' +
        '<input type="radio" name="pmode" value="public" ' +
          (publicMode ? 'checked' : '') + '>' +
        '<span>所有人（公开）</span>' +
      '</label>' +
      '<label class="perm-mode">' +
        '<input type="radio" name="pmode" value="limited" ' +
          (!publicMode ? 'checked' : '') + '>' +
        '<span>仅限指定的设备</span>' +
      '</label>' +
    '</div>' +
    '<ul class="modal-list" id="permList"></ul>' +
    '<div class="modal-actions">' +
      '<button data-act="cancel">取消</button>' +
      '<button class="primary" data-act="save">保存</button>' +
    '</div>';

  const list = modal.querySelector('#permList');

  function renderList(){
    if(publicMode){ list.style.display = 'none'; return; }
    list.style.display = '';
    list.innerHTML = '';
    if(!others.length){
      list.innerHTML = '<li class="perm-empty">当前没有其他在线设备</li>';
      return;
    }
    for(const c of others){
      const li = document.createElement('li');
      li.dataset.id = c.id;
      if(selectedSet.has(c.id)) li.classList.add('sel');
      li.innerHTML =
        '<div class="avatar">' + escapeHtml(initials(c.name)) + '</div>' +
        '<div class="info">' +
          '<div class="name">' + escapeHtml(c.name) + '</div>' +
          '<div class="ip">' + escapeHtml(c.ip) + '</div>' +
        '</div>' +
        '<div class="check">' + (selectedSet.has(c.id) ? '✓' : '') + '</div>';
      li.onclick = () => {
        if(selectedSet.has(c.id)) selectedSet.delete(c.id);
        else selectedSet.add(c.id);
        renderList();
      };
      list.appendChild(li);
    }
  }
  renderList();

  modal.addEventListener('change', e => {
    if(e.target.name === 'pmode'){
      publicMode = (e.target.value === 'public');
      renderList();
    }
  });

  modal.addEventListener('click', e => {
    const act = e.target.dataset ? e.target.dataset.act : null;
    if(act === 'cancel') document.body.removeChild(mask);
    else if(act === 'save'){
      const finalTo = publicMode ? [] : Array.from(selectedSet);
      document.body.removeChild(mask);
      savePermission(file, finalTo);
    }
  });

  mask.addEventListener('click', e => {
    if(e.target === mask) document.body.removeChild(mask);
  });

  mask.appendChild(modal);
  document.body.appendChild(mask);
}

async function savePermission(file, toClients){
  try{
    const r = await fetch('/api/permission', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: file.name, to_clients: toClients}),
    });
    const j = await r.json();
    if(!r.ok || j.error){ toast('保存失败：' + (j.error || r.status)); return; }
    if(toClients.length === 0) toast('已设为公开');
    else if(toClients.length === 1) toast('已设为仅 1 台设备可见');
    else toast('已设为仅 ' + toClients.length + ' 台设备可见');
    refreshFiles(true);
  }catch(e){ toast('保存失败：' + e.message); }
}

/* =========================================================
   修改设备名
   ========================================================= */

function openDeviceNameModal(){
  const current = loadDeviceName() || '';

  const mask = document.createElement('div');
  mask.className = 'modal-mask';
  mask.innerHTML =
    '<div class="modal" style="max-width:380px;">' +
      '<h3>设置设备名</h3>' +
      '<div class="modal-file" style="margin-bottom:14px;">' +
        '这个名字其他设备也能看到，方便对方辨认你。留空则使用默认名。' +
      '</div>' +
      '<input type="text" class="dev-name-input" id="devNameInput" ' +
             'maxlength="24" autocomplete="off" ' +
             'placeholder="例如：张三的手机">' +
      '<div class="modal-actions" style="margin-top:16px;">' +
        '<button type="button" data-act="cancel">取消</button>' +
        '<button type="button" class="primary" data-act="save">保存</button>' +
      '</div>' +
    '</div>';

  document.body.appendChild(mask);
  const input = mask.querySelector('#devNameInput');
  input.value = current;

  const close = () => {
    try{ mask.remove(); }catch(e){}
  };

  mask.addEventListener('click', e => {
    if(e.target === mask){ close(); return; }
    const btn = e.target.closest('[data-act]');
    if(!btn) return;
    const act = btn.dataset.act;
    if(act === 'cancel'){ close(); }
    else if(act === 'save'){
      const v = (input.value || '').trim();
      saveDeviceName(v);
      close();
      refreshClients();
      toast(v ? '设备名已更新' : '已恢复默认设备名');
    }
  });

  input.addEventListener('keydown', e => {
    if(e.key === 'Enter'){
      e.preventDefault();
      const btn = mask.querySelector('[data-act="save"]');
      if(btn) btn.click();
    }
  });

  setTimeout(() => {
    try{ input.focus(); input.select(); }catch(e){}
  }, 50);
}

/* =========================================================
   拖拽上传
   ========================================================= */

function isFileDrag(e){
  if(!e.dataTransfer) return false;
  const types = e.dataTransfer.types;
  if(!types) return false;
  for(let i = 0; i < types.length; i++){
    if(types[i] === 'Files') return true;
  }
  return false;
}

window.addEventListener('dragenter', e => {
  if(isFileDrag(e)) e.preventDefault();
}, false);
window.addEventListener('dragover', e => {
  if(isFileDrag(e)){
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  }
}, false);
window.addEventListener('drop', e => {
  if(!isFileDrag(e)) return;
  e.preventDefault();
  const dt = e.dataTransfer;
  if(!dt) return;
  const arr = [];
  if(dt.items && dt.items.length){
    for(const it of dt.items){
      if(it.kind === 'file'){
        const f = it.getAsFile();
        if(f) arr.push(f);
      }
    }
  } else if(dt.files){
    for(const f of dt.files) arr.push(f);
  }
  addFiles(arr);
}, false);

document.addEventListener('dragover', e => {
  if(isFileDrag(e)) e.preventDefault();
});
document.addEventListener('drop', e => {
  if(isFileDrag(e)) e.preventDefault();
});

/* =========================================================
   启动
   ========================================================= */

loadFileFilter();
restoreQueue();
refreshClients();
(function initDeviceNameUI(){
  const chip = $('deviceChip');
  if(!chip) return;
  chip.addEventListener('click', openDeviceNameModal);
})();
refreshFiles(true);
refreshTargetUI();
refreshFileSortUI();

if('requestIdleCallback' in window){
  requestIdleCallback(warmupFileSortMenu, {timeout: 2000});
} else {
  setTimeout(warmupFileSortMenu, 800);
}

setInterval(refreshClients, 3000);
setInterval(refreshFiles, 4000);