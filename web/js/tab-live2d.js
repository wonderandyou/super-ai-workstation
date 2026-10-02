/* ==========================================================================
   Live2D 制作 —— 立绘 → 分层 PSD → moc3（2026-10-02）
   拆层两种方式：本地引擎（本机显卡）/ 在线官方演示（作者 ModelScope）
   后两步走本机 PSD2Live 的 MCP
   ========================================================================== */
(function () {
  var AIWS = window.AIWS = window.AIWS || {};
  var $ = function (id) { return document.getElementById(id); };

  var S = {
    engines: [], engine: 'local', image: '', imageName: '',
    job: null, timer: null, inited: false, psds: [], status: null, lastLog: ''
  };

  function setStatus(t, cls) {
    var el = $('l2dStatus');
    if (el) { el.textContent = t || ''; el.className = 'status' + (cls ? ' ' + cls : ''); }
  }
  function setBar(p) {
    if ($('l2dBar')) $('l2dBar').style.width = Math.max(0, Math.min(100, p || 0)) + '%';
  }
  function showProg(on) {
    var el = $('l2dProg');
    if (el) el.style.display = on ? 'block' : 'none';
  }
  function logReset(t) {
    if ($('l2dLog')) $('l2dLog').textContent = t || '（还没开始）';
    S.lastLog = '';
  }
  function logFrom(lines) {
    var el = $('l2dLog');
    if (!el || !lines || !lines.length) return;
    var txt = lines.join('\n');
    if (txt === S.lastLog) return;
    S.lastLog = txt;
    el.textContent = txt;
    el.scrollTop = el.scrollHeight;
  }

  // ---------------- 状态 ----------------
  async function loadStatus() {
    try {
      var r = await AIWS.api('/api/live2d/status');
      S.status = r;
      if (!r.ok) { setStatus(r.error || '模块没加载', 'err'); return; }
      S.engines = r.engines || [];
      var pick = S.engines.filter(function (e) { return e.ready; })[0];
      if (!S.engines.filter(function (e) { return e.id === S.engine && e.ready; }).length) {
        S.engine = pick ? pick.id : (S.engines[0] || {}).id || 'local';
      }
      renderEngines();
      var p = r.psd2live || {};
      var pill = $('l2dPill'), txt = $('l2dPillText');
      if (pill && txt) {
        if (p.running) {
          pill.className = 'pill up';
          txt.textContent = p.loaded ? 'PSD2Live 已就绪（有旧工程）' : 'PSD2Live 已就绪';
        } else {
          pill.className = 'pill';
          txt.textContent = 'PSD2Live 没在跑（做的时候会自动拉起）';
        }
      }
      if ($('l2dPaths')) {
        var v = r.vram || {};
        var vtxt = (v.freeMb != null)
          ? '<br>显卡空闲显存：<b style="color:' + (v.freeMb >= (v.needMb || 5600) ? '#146c3f' : '#a8322a') + '">' +
            (v.freeMb / 1024).toFixed(1) + ' GB</b>／本地拆层要 ' + ((v.needMb || 5600) / 1024).toFixed(1) + ' GB' +
            (v.freeMb >= (v.needMb || 5600) ? ' ✓' : '（点上面「🧹 腾显存」）')
          : '';
        $('l2dPaths').innerHTML = '拆层素材：<code>' + AIWS.esc(r.matDir || '') + '</code><br>' +
          '模型成品：<code>' + AIWS.esc(r.outDir || '') + '</code>' + vtxt;
      }
    } catch (e) {
      setStatus('读状态失败：' + (e.message || e), 'err');
    }
  }

  function renderEngines() {
    var box = $('l2dEngines');
    if (!box) return;
    box.innerHTML = S.engines.map(function (e) {
      var on = e.id === S.engine;
      var icon = e.id === 'local' ? '🖥' : '☁️';
      return '<button class="mini' + (on ? ' on' : '') + '" data-eng="' + e.id + '"' +
        (e.ready ? '' : ' title="还没就绪：' + AIWS.esc(e.note || '') + '"') + '>' +
        icon + ' ' + AIWS.esc(e.name) + (e.ready ? ' ✓' : '（未就绪）') + '</button>';
    }).join('');
    Array.prototype.forEach.call(box.querySelectorAll('[data-eng]'), function (b) {
      b.onclick = function () {
        S.engine = b.getAttribute('data-eng');
        renderEngines();
      };
    });
    var cur = S.engines.filter(function (e) { return e.id === S.engine; })[0];
    if ($('l2dEngineNote')) {
      $('l2dEngineNote').innerHTML = cur
        ? '<b>' + AIWS.esc(cur.name) + '</b> —— ' + AIWS.esc(cur.note || '')
        : '';
    }
  }

  async function loadWorks() {
    var box = $('l2dWorks');
    if (!box) return;
    try {
      var r = await AIWS.api('/api/live2d/works');
      var ws = r.works || [];
      if (!ws.length) { box.innerHTML = '<span class="hint">还没有作品</span>'; return; }
      box.innerHTML = ws.map(function (w, i) {
        return '<div class="l2dWork">' +
          '<div class="l2dWorkTop">' +
          '<span class="l2dWorkName">' + AIWS.esc(w.name) + '</span>' +
          '<span class="hint">' + w.count + ' 个文件' +
          (w.hasMoc3 ? ' · 有 moc3 ✓' : ' · 没有 moc3') + '</span>' +
          '</div>' +
          '<div class="l2dWorkBtns">' +
          '<button class="mini" data-open="' + i + '">打开目录</button>' +
          (w.hasMoc3 ? '<button class="mini" data-moc3="' + i + '">打开 moc3</button>' : '') +
          '</div></div>';
      }).join('');
      Array.prototype.forEach.call(box.querySelectorAll('[data-open]'), function (b) {
        b.onclick = function () {
          var w = ws[parseInt(b.getAttribute('data-open'), 10)];
          AIWS.api('/api/openfolder', { dir: w.dir });
        };
      });
      Array.prototype.forEach.call(box.querySelectorAll('[data-moc3]'), function (b) {
        b.onclick = function () {
          var w = ws[parseInt(b.getAttribute('data-moc3'), 10)];
          AIWS.api('/api/openfile', { file: w.moc3 });
        };
      });
    } catch (e) {
      box.innerHTML = '<span class="hint">读不到：' + AIWS.esc(e.message || e) + '</span>';
    }
  }

  // ---------------- 选图 ----------------
  function setImage(path, name) {
    S.image = path || '';
    S.imageName = name || '';
    if ($('l2dFileName')) $('l2dFileName').textContent = name ? name : '';
    if ($('l2dClear')) $('l2dClear').style.display = path ? '' : 'none';
    if ($('l2dThumb')) {
      if (path) {
        $('l2dThumb').src = '/api/live2d/thumb?path=' + encodeURIComponent(path) + '&t=' + Date.now();
        $('l2dThumb').style.display = '';
      } else {
        $('l2dThumb').style.display = 'none';
      }
    }
    if (path && $('l2dName') && !$('l2dName').value.trim()) {
      var base = (name || '').replace(/\.[^.]+$/, '');
      $('l2dName').value = base;
    }
  }

  function uploadFile(f) {
    if (!f) return;
    if (f.size > 40 * 1024 * 1024) { setStatus('图超过 40 MB', 'err'); return; }
    setStatus('正在导入立绘…', 'busy');
    var fr = new FileReader();
    fr.onload = function () {
      AIWS.api('/api/live2d/upload', { name: f.name, data: fr.result }).then(function (r) {
        if (!r.ok) { setStatus(r.error || '导入失败', 'err'); return; }
        setImage(r.path, r.name);
        setStatus('已导入：' + r.name + '（' + r.sizeText + '）', 'ok');
      }).catch(function (e) { setStatus('导入失败：' + (e.message || e), 'err'); });
    };
    fr.readAsDataURL(f);
  }

  // ---------------- 跑 ----------------
  async function go() {
    if (!S.image) { setStatus('先选一张立绘', 'err'); return; }
    var name = ($('l2dName') || {}).value || '';
    var box = {
      image: S.image, name: name.trim(), engine: S.engine,
      resolution: parseInt((($('l2dRes') || {}).value) || '1024', 10),
      seed: parseInt((($('l2dSeed') || {}).value) || '42', 10),
      tblr: !!((($('l2dTblr') || {}).checked)),
      allow_restart: !!((($('l2dRestart') || {}).checked))
    };
    if ($('l2dGo')) $('l2dGo').disabled = true;
    showProg(true); setBar(0); logReset('（启动中…）');
    setStatus('提交任务…', 'busy');
    try {
      var r = await AIWS.api('/api/live2d/make', box);
      if (!r.ok) { setStatus(r.error || '提交失败', 'err'); if ($('l2dGo')) $('l2dGo').disabled = false; return; }
      S.job = r.job;
      try { localStorage.setItem('aiws.l2dLastJob', r.job); } catch (e) {}
      if (!box.name && r.name && $('l2dName')) $('l2dName').value = r.name;
      setStatus('任务已提交，正在跑…', 'busy');
      poll();
    } catch (e) {
      setStatus('提交失败：' + (e.message || e), 'err');
      if ($('l2dGo')) $('l2dGo').disabled = false;
    }
  }

  function poll() {
    if (S.timer) clearTimeout(S.timer);
    S.timer = setTimeout(async function () {
      if (!S.job) return;
      var j;
      try {
        j = await AIWS.api('/api/live2d/progress?job=' + encodeURIComponent(S.job));
      } catch (e) { poll(); return; }
      if (!j.ok) {
        // 任务真的找不到了（连磁盘留档都没有）
        setStatus((j.error || '任务丢了') + '　—— 点「开始制作」重跑一次即可', 'err');
        if ($('l2dGo')) $('l2dGo').disabled = false;
        loadWorks();
        return;
      }
      setBar(j.pct);
      if (j.state === 'running' || j.state === 'queued') setStatus(j.text || '跑着…', 'busy');
      logFrom(j.log);
      if (j.state === 'interrupted') {
        // ★ 工作站重启过：进度跟丢了，但要说清楚，别只丢一句红字
        setStatus(j.text || '任务被中断（工作站重启过）', 'err');
        loadWorks(); loadStatus();
        if ($('l2dGo')) $('l2dGo').disabled = false;
        return;
      }
      if (j.state === 'done') {
        showProg(true); setBar(100);
        setStatus('完成 ✓  ' + (j.files || []).length + ' 个文件 → ' + j.outDir, 'ok');
        renderFiles(j);
        loadWorks();
        if ($('l2dGo')) $('l2dGo').disabled = false;
        return;
      }
      if (j.state === 'error') {
        setStatus('出错：' + (j.error || ''), 'err');
        if ($('l2dGo')) $('l2dGo').disabled = false;
        return;
      }
      poll();
    }, 1200);
  }

  function renderFiles(j) {
    var box = $('l2dFiles');
    if (!box) return;
    var files = j.files || [];
    if (!files.length) { box.innerHTML = '<span class="hint">看目录：' + AIWS.esc(j.outDir || '') + '</span>'; return; }
    box.innerHTML = files.map(function (f, i) {
      var base = f.replace(/\\/g, '/').split('/').pop();
      var key = base.toLowerCase().indexOf('.moc3') >= 0;
      return '<div class="l2dFile' + (key ? ' key' : '') + '">' +
        '<span class="l2dFileNm">' + AIWS.esc(base) + (key ? '（模型本体）' : '') + '</span>' +
        '<button class="mini" data-f="' + i + '">打开</button></div>';
    }).join('') + '<div class="l2dWorkBtns"><button class="mini primary" id="l2dOpenOut2">打开成品目录</button></div>';
    Array.prototype.forEach.call(box.querySelectorAll('[data-f]'), function (b) {
      b.onclick = function () { AIWS.api('/api/openfile', { file: files[parseInt(b.getAttribute('data-f'), 10)] }); };
    });
    var o = $('l2dOpenOut2');
    if (o) o.onclick = function () { AIWS.api('/api/openfolder', { dir: j.outDir }); };
  }

  // ---------------- 已有 PSD ----------------
  async function openPsdPanel() {
    var panel = $('l2dPsdPanel');
    if (!panel) return;
    panel.style.display = '';
    try {
      var r = await AIWS.api('/api/live2d/psds');
      S.psds = r.psds || [];
      var sel = $('l2dPsdList');
      if (sel) {
        sel.innerHTML = '<option value="">（选一个…）</option>' + S.psds.map(function (p) {
          return '<option value="' + AIWS.esc(p.path) + '">' + AIWS.esc(p.name) + ' · ' + p.mb + ' MB</option>';
        }).join('');
        sel.onchange = function () { if ($('l2dPsdPath')) $('l2dPsdPath').value = sel.value; };
      }
    } catch (e) { /* 忽略 */ }
  }

  async function goPsd() {
    var p = (($('l2dPsdPath') || {}).value || '').trim();
    if (!p) { setStatus('先选一个 PSD 或粘个路径', 'err'); return; }
    var name = (($('l2dName') || {}).value || '').trim();
    showProg(true); setBar(0); logReset('（导入中…）');
    setStatus('提交…', 'busy');
    try {
      var r = await AIWS.api('/api/live2d/import-psd', {
        psd: p, name: name, allow_restart: !!((($('l2dRestart') || {}).checked))
      });
      if (!r.ok) { setStatus(r.error || '提交失败', 'err'); return; }
      S.job = r.job;
      try { localStorage.setItem('aiws.l2dLastJob', r.job); } catch (e) {}
      poll();
    } catch (e) { setStatus('提交失败：' + (e.message || e), 'err'); }
  }

  // ---------------- 绑定 ----------------
  function bind() {
    if ($('l2dRefresh')) $('l2dRefresh').onclick = function () { loadStatus(); loadWorks(); };
    if ($('l2dFreeVram')) $('l2dFreeVram').onclick = function () {
      var b = $('l2dFreeVram');
      b.disabled = true;
      setStatus('正在让 ComfyUI 等程序让出显存（不会关掉它们）…', 'busy');
      AIWS.api('/api/live2d/free-vram', {}).then(function (r) {
        b.disabled = false;
        if (!r.ok) { setStatus('腾显存失败：' + (r.error || ''), 'err'); return; }
        var before = r.before != null ? (r.before / 1024).toFixed(1) + ' GB' : '?';
        var after = (r.freeMb != null ? r.freeMb : r.after) != null
          ? ((r.freeMb != null ? r.freeMb : r.after) / 1024).toFixed(1) + ' GB' : '?';
        var enough = (r.freeMb != null) && (r.freeMb >= (r.needMb || 5600));
        var who = (r.consumers && r.consumers.length)
          ? '　占着显卡的：' + r.consumers.join('、') : '';
        setStatus('显存：' + before + ' → ' + after +
                  (enough ? ' ✓ 可以跑本地引擎了'
                          : '（还不够。可关掉壁纸引擎 / QQ / 多余的 Edge 标签，' +
                            '或改用「在线官方演示」那条路 —— 它不占显存）') + who,
                  enough ? 'ok' : 'err');
        loadStatus();
      }).catch(function (e) {
        b.disabled = false;
        setStatus('腾显存失败：' + (e.message || e), 'err');
      });
    };
    if ($('l2dOpenOut')) $('l2dOpenOut').onclick = function () {
      AIWS.api('/api/openfolder', { dir: (S.status || {}).outDir });
    };
    if ($('l2dFile')) $('l2dFile').onchange = function () {
      var f = (this.files || [])[0]; this.value = ''; uploadFile(f);
    };
    if ($('l2dClear')) $('l2dClear').onclick = function () { setImage('', ''); };
    if ($('l2dGo')) $('l2dGo').onclick = go;
    if ($('l2dPickPsd')) $('l2dPickPsd').onclick = openPsdPanel;
    if ($('l2dPsdGo')) $('l2dPsdGo').onclick = goPsd;
    if ($('l2dPsdCancel')) $('l2dPsdCancel').onclick = function () { $('l2dPsdPanel').style.display = 'none'; };

    var drop = $('l2dDrop');
    if (drop) {
      ['dragenter', 'dragover'].forEach(function (ev) {
        drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add('hot'); });
      });
      ['dragleave', 'drop'].forEach(function (ev) {
        drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove('hot'); });
      });
      drop.addEventListener('drop', function (e) {
        var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
        if (f) uploadFile(f);
      });
    }
  }

  function boot() {
    if (S.inited) return;
    S.inited = true;
    bind();
    loadStatus();
    loadWorks();
    restoreLastJob();
  }

  // ★ 刷新页面/重启工作站后，把「上次那个任务」接回来
  //   （2026-10-02：以前一刷新就只说「没有这个任务」，用户不知道发生了什么）
  async function restoreLastJob() {
    var jid = '';
    try { jid = localStorage.getItem('aiws.l2dLastJob') || ''; } catch (e) { jid = ''; }
    if (!jid) return;
    try {
      var j = await AIWS.api('/api/live2d/progress?job=' + encodeURIComponent(jid));
      if (!j.ok) return;
      S.job = jid;
      logFrom(j.log);
      if (j.state === 'running' || j.state === 'queued') {
        showProg(true); setBar(j.pct);
        setStatus('接着看上一条任务：' + (j.text || '跑着…'), 'busy');
        poll();
      } else if (j.state === 'interrupted') {
        showProg(true); setBar(j.pct);
        setStatus(j.text || '上次的任务被中断了（工作站重启过）', 'err');
      } else if (j.state === 'done') {
        showProg(true); setBar(100);
        setStatus('上次的任务已完成 ✓', 'ok');
        renderFiles(j);
      }
    } catch (e) { /* 忽略：只是恢复，失败不影响用 */ }
  }

  document.addEventListener('aiws:tab', function (ev) {
    if (ev && ev.detail && ev.detail.tab === 'live2d') boot();
  });
  document.addEventListener('aiws:ready', function () {
    if ((location.hash || '').indexOf('live2d') >= 0) setTimeout(boot, 300);
  });
})();

