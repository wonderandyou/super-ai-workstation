/* ============================================================
   标签页：本地千问生图（ComfyUI + Qwen-Image-2.1）
   右上角按钮启动后端 · 一键下载安装 · 完全离线
   ============================================================ */
(function () {
  'use strict';

  const $ = function (id) { return document.getElementById(id); };
  let status = null;
  let pollStatus = null;
  let progTimer = null;
  let started = false;

  const ASPECTS = { '1:1': [1, 1], '3:4': [3, 4], '2:3': [2, 3], '9:16': [9, 16],
                    '4:3': [4, 3], '3:2': [3, 2], '16:9': [16, 9] };

  function human(n) {
    n = Number(n) || 0;
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i <= 1 ? n.toFixed(0) : n.toFixed(2)) + ' ' + u[i];
  }

  function calcSize() {
    const a = ASPECTS[$('qAspect').value] || [1, 1];
    const mp = parseFloat($('qMp').value) || 1.0;
    const total = mp * 1e6;
    const h = Math.sqrt(total / (a[0] / a[1]));
    const w = h * a[0] / a[1];
    const r = function (x) { return Math.max(256, Math.round(x / 32) * 32); };
    return [r(w), r(h)];
  }

  function updateSizeHint() {
    const [w, h] = calcSize();
    const mp = (w * h / 1e6).toFixed(2);
    const asp = $('qAspect').value;
    // ★ 显式带上比例和总像素：避免"换比例后看着没变"的误会 ✓
    $('qSizeHint').textContent = w + '×' + h + '（' + asp + ' · ' + mp + ' MP）';
  }

  // ------------------------------------------------------------------
  //  状态
  // ------------------------------------------------------------------
  function setPill(kind, text) {
    const p = $('qPill');
    p.className = 'pill' + (kind ? ' ' + kind : '');
    $('qPillText').textContent = text;
  }

  async function refreshStatus(silent) {
    let r;
    try { r = await window.AIWS.api('/api/local/status'); }
    catch (e) {
      setPill('down', '读取失败');
      if (!silent) console.error(e);
      return null;
    }
    status = r.status;
    const c = status.comfy, hw = status.hardware;

    // 后端
    if (c.apiOk) setPill('up', '后端已就绪 · ' + (c.device || ('端口 ' + c.port)));
    else if (c.running) setPill('', '后端启动中…');
    else if (c.found) setPill('', '后端未运行');
    else setPill('down', '未安装 ComfyUI');

    $('qStartBtn').style.display = c.apiOk ? 'none' : '';
    $('qStartBtn').disabled = !c.found;
    $('qStopBtn').style.display = (c.running && !c.apiOk) || c.apiOk ? '' : 'none';

    $('qComfyPath').textContent = c.found ? c.root : '未安装';
    $('qComfyPort').textContent = c.port;
    $('qComfyRun').textContent = c.apiOk ? ('运行中 ' + (c.version || '')) : (c.running ? '启动中' : '未运行');
    $('qGpu').textContent = hw.gpu || '未检测到';
    $('qVram').textContent = hw.vram ? (hw.vram / 2 ** 30).toFixed(1) + ' GB' : '—';
    const v = $('qVerdict');
    v.textContent = hw.reason || '';
    v.style.color = ({ ok: '#1f7a4d', tight: '#9a6b00', 'too-small': '#9c2f26', 'no-gpu': '#9c2f26' })[hw.verdict] || '';

    renderNeed();
    return status;
  }

  function renderNeed() {
    if (!status) return;
    const box = $('qNeedBox');
    const items = [];
    if (status.needComfy) {
      items.push('<div class="needItem no"><span class="ic">✗</span>' +
        '<span class="nm">ComfyUI 运行环境（' + status.comfyAsset.version + ' 便携版）</span>' +
        '<span class="sz">' + status.comfyAsset.sizeText + '</span></div>');
    } else {
      items.push('<div class="needItem ok"><span class="ic">✓</span>' +
        '<span class="nm">ComfyUI 运行环境</span><span class="sz">已就绪</span></div>');
    }
    (status.models || []).forEach(function (m) {
      items.push('<div class="needItem ' + (m.ok ? 'ok' : 'no') + '">' +
        '<span class="ic">' + (m.ok ? '✓' : '✗') + '</span>' +
        '<span class="nm">' + window.AIWS.esc(m.label) + '<br><small class="muted">' + window.AIWS.esc(m.name) + '</small></span>' +
        '<span class="sz">' + m.sizeText + '</span></div>');
    });
    const missing = (status.needComfy ? status.comfyAsset.size : 0) + (status.missingBytes || 0);
    items.push('<div class="needItem" style="border-top:2px solid var(--line);margin-top:6px">' +
      '<span class="ic">Σ</span><span class="nm"><b>还需要下载</b></span>' +
      '<span class="sz"><b>' + (missing ? human(missing) : '0 B（全部就绪）') + '</b></span></div>');
    box.innerHTML = items.join('');
    $('qInstallBtn').textContent = missing ? '⬇ 一键下载并安装（' + human(missing) + '）' : '↻ 重新检查 / 修复';
  }

  // ------------------------------------------------------------------
  //  后端启停
  // ------------------------------------------------------------------
  async function startBackend() {
    $('qStartBtn').disabled = true;
    setPill('', '正在启动…');
    try {
      const r = await window.AIWS.api('/api/local/start', {});
      if (!r.ok) { setPill('down', r.error || '启动失败'); alert(r.error || '启动失败'); return; }
      setPill('', '正在加载模型…');
      // 轮询等就绪（首次加载 13 GB 权重，可能要 1~3 分钟）
      let n = 0;
      const t = setInterval(async function () {
        n++;
        const st = await refreshStatus(true);
        if (st && st.comfy.apiOk) { clearInterval(t); setPill('up', '后端已就绪 · ' + (st.comfy.device || '')); }
        else if (n > 120) { clearInterval(t); setPill('down', '启动超时，看日志'); }
        else setPill('', '正在加载模型… ' + (n * 3) + ' 秒');
      }, 3000);
    } catch (e) {
      setPill('down', '启动失败');
      alert('启动失败：' + e.message);
    } finally {
      $('qStartBtn').disabled = false;
    }
  }

  async function stopBackend() {
    if (!confirm('确定停止后端吗？停止后本地出图不能用。')) return;
    await window.AIWS.api('/api/local/stop', {});
    setTimeout(function () { refreshStatus(); }, 1500);
  }

  // ------------------------------------------------------------------
  //  安装
  // ------------------------------------------------------------------
  function jobUI(prefix) {
    return {
      prog: $(prefix + 'Prog') || $(prefix),
      bar: $(prefix + 'Bar'),
      text: $(prefix + 'Text'),
    };
  }

  async function doInstall() {
    if (!confirm('将开始下载并安装：\n\n· ComfyUI 运行环境（约 1.86 GB）\n· Qwen-Image-2.1 三个模型（约 13.27 GB）\n\n总计约 15 GB，支持断点续传。中途可以关掉页面，下次继续。\n\n确定开始吗？')) return;
    $('qInstallBtn').disabled = true;
    $('qInstallProg').style.display = 'block';
    $('qInstallStatus').textContent = '';
    $('qInstallLog').textContent = '';
    try {
      const r = await window.AIWS.api('/api/local/install', {});
      pollJob(r.job, 'qInstall');
    } catch (e) {
      $('qInstallBtn').disabled = false;
      $('qInstallStatus').textContent = '启动失败：' + e.message;
      $('qInstallStatus').className = 'status err';
    }
  }

  function pollJob(job, prefix) {
    if (progTimer) clearInterval(progTimer);
    progTimer = setInterval(async function () {
      let r;
      try { r = await window.AIWS.api('/api/local/progress?job=' + encodeURIComponent(job)); }
      catch (e) { return; }
      const bar = $(prefix + 'Bar') || $(prefix === 'qInstall' ? 'qInstallBar' : 'qBar');
      const txt = $(prefix + 'Text') || $(prefix === 'qInstall' ? 'qInstallText' : 'qProgText');
      const st = $(prefix === 'qInstall' ? 'qInstallStatus' : 'qStatus');
      if (bar) bar.style.width = (r.percent || 0) + '%';
      if (txt) txt.textContent = (r.stage || '') + (r.detail ? '　·　' + r.detail : '') + '　·　' + (r.percent || 0) + '%';
      if (prefix === 'qInstall') {
        const lg = $('qInstallLog');
        if (lg && r.log) lg.textContent = r.log.join('\n');
      }
      if (r.state === 'running') return;
      clearInterval(progTimer); progTimer = null;

      if (prefix === 'qInstall') {
        if (bar) bar.style.width = '100%';
        $('qInstallProg').style.display = 'none';
        $('qInstallBtn').disabled = false;
        if (r.state === 'done') {
          st.textContent = '安装完成 ✓ 现在可以点右上角「启动后端」了。';
          st.className = 'status ok';
        } else {
          st.textContent = '安装失败：' + (r.error || '未知错误');
          st.className = 'status err';
        }
        refreshStatus();
      } else {
        $('qProg').style.display = 'none';
        $('qGenBtn').disabled = false;
        if (r.state === 'done') { showResult(r.result); st.textContent = '生成成功 ✓（本地出图，免费）'; st.className = 'status ok'; }
        else { st.textContent = '生成失败：\n' + (r.error || '未知错误'); st.className = 'status err'; }
      }
    }, 900);
  }

  // ------------------------------------------------------------------
  //  出图
  // ------------------------------------------------------------------
  async function onGenerate() {
    const prompt = $('qPrompt').value.trim();
    if (!prompt) { setStatus('请先写画面描述'); return; }
    if (!status || !status.comfy.apiOk) {
      setStatus('后端没在运行 —— 先点右上角「⏻ 启动后端」');
      return;
    }
    $('qGenBtn').disabled = true;
    $('qResultCard').style.display = 'none';
    $('qProg').style.display = 'block';
    $('qBar').style.width = '0%';
    const [w, h] = calcSize();
    $('qStatus').textContent = '';
    $('qStatus').className = 'status';
    try {
      const _initImg = (window.__qInitImage && window.__qInitImage()) || '';
      const r = await window.AIWS.api('/api/local/generate', {
        prompt: prompt,
        aspect: $('qAspect').value,
        megapixels: parseFloat($('qMp').value),
        resolution: 1024,
        steps: parseInt($('qSteps').value, 10),
        seed: parseInt($('qSeed').value, 10) || 0,
        // ★ 图生图：有底图才带，没有就是纯文生图 ✓
        initImage: _initImg,
        denoise: parseFloat(($('qDenoise') || {}).value || 0.65)
      });
      pollJob(r.job, 'q');
    } catch (e) {
      $('qProg').style.display = 'none';
      $('qGenBtn').disabled = false;
      setStatus('提交失败：' + e.message);
    }
  }

  function setStatus(m) {
    const el = $('qStatus');
    el.textContent = m;
    el.className = 'status err';
  }

  function showResult(items) {
    if (!items || !items.length) return;
    $('qResultCard').style.display = 'block';
    $('qResultBox').innerHTML = items.map(function (it) {
      var extra = (it.seed ? '　种子 ' + window.AIWS.esc(it.seed) : '') +
        (it.initImage ? '　<b style="color:#146c3f">参考图</b>' : '');
      return '<div><img src="/api/image?path=' + encodeURIComponent(it.file) + '" alt="">' +
        '<div class="meta">' + window.AIWS.esc(it.name) + '<br>' +
        window.AIWS.esc(it.model) + '　' + window.AIWS.esc(it.reqSize) +
        '　用时 ' + window.AIWS.esc(it.seconds) + ' 秒' + extra + '　<b style="color:#1f7a4d">免费</b></div></div>';
    }).join('');
  }

  // ★ 参考图模式：重绘强度不适用（模型自己决定改动幅度）→ 直接收起来，别误导 ✓
  function applyRefMode(on) {
    var wrap = $('qDenoiseWrap'), hint = $('qRefModeHint');
    if (wrap) wrap.style.display = on ? 'none' : '';
    if (hint) hint.style.display = on ? '' : 'none';
  }

  // ------------------------------------------------------------------
  //  提示词库（和豆包共用一套数据）
  // ------------------------------------------------------------------
  function initLib() {
    const lib = window.PROMPT_LIB || [];
    $('qLibList').innerHTML = lib.slice(0, 40).map(function (it, i) {
      return '<span class="libItem" data-i="' + i + '" title="' + window.AIWS.esc(it.t.slice(0, 120)) + '">' +
        window.AIWS.esc(it.n) + '</span>';
    }).join('') || '<span class="muted">提示词库是空的</span>';
    $('qLibList').querySelectorAll('.libItem').forEach(function (el) {
      el.addEventListener('click', function () {
        const it = lib[+el.dataset.i];
        if (!it) return;
        $('qPrompt').value = ($('qPrompt').value.trim() ? $('qPrompt').value.replace(/\s+$/, '') + '，' : '') + it.t;
      });
    });
    $('qLibRandom').addEventListener('click', function () {
      if (!lib.length) return;
      $('qPrompt').value = lib[Math.floor(Math.random() * lib.length)].t;
    });
    $('qLibClear').addEventListener('click', function () { $('qPrompt').value = ''; });
  }

  // ------------------------------------------------------------------
  //  初始化
  // ------------------------------------------------------------------
  function init() {
    if (started) return;
    started = true;

    $('qStartBtn').addEventListener('click', startBackend);
    $('qStopBtn').addEventListener('click', stopBackend);
    $('qInstallBtn').addEventListener('click', doInstall);
    $('qGenBtn').addEventListener('click', onGenerate);
    $('qAspect').addEventListener('change', updateSizeHint);
    $('qMp').addEventListener('change', updateSizeHint);
    $('qSteps').addEventListener('input', function () { $('qStepsVal').textContent = this.value; });
    $('qOpenFolder').addEventListener('click', async function () {
      // ★ 千问有自己的输出目录（qwenOutDir = 桌面\千问1生图）
      //   不传 dir 的话服务端会退回豆包的 outputDir → 打开错文件夹 ✗
      let dir = '';
      try {
        const c = await window.AIWS.api('/api/config');
        const cc = (c && (c.config || c)) || {};
        dir = cc.qwenOutDir || '';
      } catch (e) {}
      const r = await window.AIWS.api('/api/openfolder', dir ? { dir: dir } : {});
      if (!r.ok) alert(r.error || '打不开');
    });

    initLib();
    updateSizeHint();
    refreshStatus();

    // 切到本标签页时刷新状态
    document.addEventListener('aiws:tab', function (e) {
      if (e.detail.tab === 'qwen') refreshStatus();
    });
  }

  document.addEventListener('aiws:ready', init);

  // ---------- ★ 图生图（可选底图）----------
  var qInitB64 = '';
  function qInitBind() {
    var pick = $('qInitPick'), file = $('qInitFile');
    if (!pick || !file || pick.__bound) return;
    pick.__bound = true;
    pick.addEventListener('click', function (ev) { ev.preventDefault(); file.click(); });
    file.addEventListener('change', function () {
      var f = (file.files || [])[0];
      if (!f) return;
      var fr = new FileReader();
      fr.onload = function () {
        qInitB64 = fr.result;
        var th = $('qInitThumb');
        if (th) { th.src = qInitB64; th.style.display = 'block'; }
        var cl = $('qInitClear');
        if (cl) cl.style.display = '';
        var nm = $('qInitName');
        if (nm) nm.textContent = f.name + '（' + Math.round(f.size / 1024) + ' KB）';
        applyRefMode(true);
      };
      fr.readAsDataURL(f);
    });
    var cl = $('qInitClear');
    if (cl) cl.addEventListener('click', function () {
      qInitB64 = '';
      file.value = '';
      var th = $('qInitThumb');
      if (th) { th.style.display = 'none'; th.src = ''; }
      cl.style.display = 'none';
      var nm = $('qInitName');
      if (nm) nm.textContent = '';
      applyRefMode(false);
    });
    var dn = $('qDenoise');
    if (dn) dn.addEventListener('input', function () {
      var v = $('qDenoiseV');
      if (v) v.textContent = dn.value;
    });
  }
  qInitBind();
  document.addEventListener('aiws:tab', function (e) {
    if (e.detail && e.detail.tab === 'qwen') setTimeout(qInitBind, 100);
  });
  document.addEventListener('aiws:ready', function () { setTimeout(qInitBind, 200); });
  window.__qInitImage = function () { return qInitB64; };
  window.__qInitBind = qInitBind;

})();
