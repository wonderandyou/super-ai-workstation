/* ============================================================
   标签页：豆包 Seedream 生图
   —— 从原「豆包Seedream生图.hta」移植
   接口：POST https://ark.cn-beijing.volces.com/api/v3/images/generations
   （实际由本机后端转发，避免把 Key 暴露在浏览器里）
   ============================================================ */
(function () {
  'use strict';

  const $ = function (id) { return document.getElementById(id); };
  let libCat = '*';          // 当前分类
  let libMode = 'append';    // append | replace
  let undoStack = [];
  let pollTimer = null;
  let tickTimer = null;
  let refs = [];             // [{name, dataUri}]

  // ------------------------------------------------------------------
  //  价格估算（与服务端同一套规则）
  // ------------------------------------------------------------------
  function priceOf(model, size, refCount) {
    const cfg = window.AIWS.config || {};
    const prices = cfg.prices || {};
    let key = null;
    Object.keys(prices).sort(function (a, b) { return b.length - a.length; }).forEach(function (k) {
      if (!key && String(model).toLowerCase().indexOf(k.toLowerCase()) === 0) key = k;
    });
    if (!key) return 0;
    let band = prices[key].low || 0;
    const s = String(size).toUpperCase();
    let high = (s === '2K' || s === '4K');
    const m = /^(\d{3,5})x(\d{3,5})$/.exec(s);
    if (m) high = Math.max(+m[1], +m[2]) >= 2000;
    if (high) band = prices[key].high || band;
    let total = Number(band) || 0;
    if (refCount > 1) total += (Number(cfg.refCostPerImage) || 0) * (refCount - 1);
    return total;
  }

  function currentSize() {
    const v = $('size').value;
    if (v === 'custom') return ($('customSize').value || '').trim() || '2K';
    return v;
  }

  function updateCost() {
    const c = priceOf($('model').value, currentSize(), refs.length);
    $('costHint').textContent = '预计 ' + window.AIWS.money(c) + ' 元' +
      (refs.length > 1 ? '（含 ' + (refs.length - 1) + ' 张参考图）' : '');
  }

  // ------------------------------------------------------------------
  //  提示词库
  // ------------------------------------------------------------------
  function initLib() {
    const lib = window.PROMPT_LIB || [];
    const cats = [];
    lib.forEach(function (it) { if (cats.indexOf(it.c) < 0) cats.push(it.c); });

    const box = $('libCats');
    box.innerHTML = ['<span class="chip on" data-cat="*">全部</span>']
      .concat(cats.map(function (c) {
        return '<span class="chip" data-cat="' + window.AIWS.esc(c) + '">' + window.AIWS.esc(c) + '</span>';
      })).join('');
    box.querySelectorAll('.chip').forEach(function (el) {
      el.addEventListener('click', function () {
        libCat = el.dataset.cat;
        box.querySelectorAll('.chip').forEach(function (x) { x.classList.toggle('on', x === el); });
        renderLib();
      });
    });

    $('libRandom').addEventListener('click', function () {
      const list = filtered();
      if (!list.length) return;
      pushUndo();
      $('prompt').value = list[Math.floor(Math.random() * list.length)].t;
      updateCost();
    });
    $('libUndo').addEventListener('click', function () {
      if (!undoStack.length) return;
      $('prompt').value = undoStack.pop();
      updateCost();
    });
    $('libClear').addEventListener('click', function () {
      pushUndo();
      $('prompt').value = '';
      updateCost();
    });

    renderLib();
  }

  function filtered() {
    const lib = window.PROMPT_LIB || [];
    return libCat === '*' ? lib : lib.filter(function (it) { return it.c === libCat; });
  }

  function renderLib() {
    const list = filtered();
    $('libList').innerHTML = list.map(function (it, i) {
      return '<span class="libItem" data-i="' + (window.PROMPT_LIB || []).indexOf(it) +
             '" title="' + window.AIWS.esc(it.c) + '：' + window.AIWS.esc(it.t.slice(0, 120)) + '">' +
             window.AIWS.esc(it.n) + '</span>';
    }).join('') || '<span class="muted">这个分类下还没有条目</span>';

    $('libList').querySelectorAll('.libItem').forEach(function (el) {
      el.addEventListener('click', function (ev) {
        const it = (window.PROMPT_LIB || [])[+el.dataset.i];
        if (!it) return;
        const replace = ev.shiftKey || libMode === 'replace';
        pushUndo();
        if (replace || !$('prompt').value.trim()) {
          $('prompt').value = it.t;
        } else {
          $('prompt').value = $('prompt').value.replace(/\s+$/, '') + '，' + it.t;
        }
        updateCost();
      });
      el.addEventListener('contextmenu', function (ev) {   // 右键 = 覆盖
        ev.preventDefault();
        const it = (window.PROMPT_LIB || [])[+el.dataset.i];
        if (!it) return;
        pushUndo();
        $('prompt').value = it.t;
        updateCost();
      });
    });
  }

  function pushUndo() {
    const v = $('prompt').value;
    if (undoStack[undoStack.length - 1] !== v) undoStack.push(v);
    if (undoStack.length > 40) undoStack.shift();
  }

  // ------------------------------------------------------------------
  //  参考图
  // ------------------------------------------------------------------
  function renderRefs() {
    const row = $('refRow');
    if (!refs.length) {
      row.innerHTML = '<span class="muted">还没有参考图</span>';
      return;
    }
    row.innerHTML = refs.map(function (r, i) {
      return '<div class="refItem">' +
        '<img class="thumb" src="' + r.dataUri + '" alt="">' +
        '<input type="text" value="' + window.AIWS.esc(r.name) + '" readonly>' +
        '<button class="del" data-i="' + i + '">移除</button>' +
        '</div>';
    }).join('');
    row.querySelectorAll('.del').forEach(function (b) {
      b.addEventListener('click', function () {
        refs.splice(+b.dataset.i, 1);
        renderRefs(); updateCost();
      });
    });
  }

  function initRefs() {
    $('refAdd').addEventListener('click', function () { $('refFile').click(); });
    $('refFile').addEventListener('change', function () {
      const files = Array.from($('refFile').files || []);
      $('refFile').value = '';
      if (!files.length) return;
      if (refs.length + files.length > 10) { alert('最多 10 张参考图'); return; }
      let pending = files.length;
      files.forEach(function (f) {
        if (f.size > 30 * 1024 * 1024) { alert('「' + f.name + '」超过 30MB，已跳过'); if (!--pending) { renderRefs(); updateCost(); } return; }
        const fr = new FileReader();
        fr.onload = function () {
          refs.push({ name: f.name, dataUri: fr.result });
          if (!--pending) { renderRefs(); updateCost(); }
        };
        fr.onerror = function () { if (!--pending) { renderRefs(); updateCost(); } };
        fr.readAsDataURL(f);
      });
    });
  }

  // ------------------------------------------------------------------
  //  生成
  // ------------------------------------------------------------------
  function setStatus(msg, kind) {
    const el = $('status');
    el.textContent = msg || '';
    el.className = 'status' + (kind ? ' ' + kind : '');
  }

  function startProgress() {
    $('progress').style.display = 'block';
    let sec = 0;
    const total = currentSize() === '2K' ? 110 : 60;
    tickTimer = setInterval(function () {
      sec++;
      const pct = Math.min(94, Math.round(100 * (1 - Math.exp(-sec / (total * 0.45)))));
      $('barFill').style.width = pct + '%';
      $('progText').textContent = '已等待 ' + sec + ' 秒　·　预计 ' +
        (currentSize() === '2K' ? '1~2 分钟' : '40 秒左右') + '　·　请勿关闭页面';
    }, 1000);
  }

  function stopProgress() {
    if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
    $('barFill').style.width = '100%';
    $('progress').style.display = 'none';
  }

  async function onGenerate() {
    const prompt = $('prompt').value.trim();
    if (!prompt) { setStatus('请先写画面描述', 'err'); $('prompt').focus(); return; }
    if (!(window.AIWS.config || {}).hasKey) {
      setStatus('还没设置 API Key → 去「⚙️ 设置」标签页填一个', 'err');
      return;
    }
    const size = currentSize();
    if ($('size').value === 'custom' && !/^\d{3,5}x\d{3,5}$/.test(size)) {
      setStatus('自定义尺寸格式应为 宽x高，例如 2048x1152', 'err');
      return;
    }

    $('genBtn').disabled = true;
    $('resultCard').style.display = 'none';
    setStatus('正在提交…', 'busy');
    startProgress();

    try {
      const r = await window.AIWS.api('/api/generate', {
        prompt: prompt,
        model: $('model').value,
        size: size,
        customSize: $('customSize').value.trim(),
        output_format: $('fmt').value,
        watermark: $('wm').checked,
        images: refs.map(function (x) { return x.dataUri; })
      });
      pollJob(r.job);
    } catch (e) {
      stopProgress();
      $('genBtn').disabled = false;
      setStatus('提交失败：' + e.message, 'err');
    }
  }

  function pollJob(job) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(async function () {
      let r;
      try { r = await window.AIWS.api('/api/progress?job=' + encodeURIComponent(job)); }
      catch (e) { return; }
      if (r.state === 'running') {
        if (r.stage) $('progText').textContent = r.stage + '　·　已等待 ' + r.elapsed + ' 秒';
        return;
      }
      clearInterval(pollTimer); pollTimer = null;
      stopProgress();
      $('genBtn').disabled = false;
      if (r.state === 'done') {
        showResult(r.result);
        setStatus('生成成功 ✓', 'ok');
      } else {
        setStatus('生成失败：\n' + (r.error || '未知错误'), 'err');
      }
    }, 900);
  }

  function showResult(items) {
    if (!items || !items.length) return;
    $('resultCard').style.display = 'block';
    const total = items.reduce(function (a, x) { return a + (Number(x.cost) || 0); }, 0);
    $('resultBox').innerHTML = items.map(function (it) {
      const url = '/api/image?path=' + encodeURIComponent(it.file);
      return '<div>' +
        '<img src="' + url + '" alt="">' +
        '<div class="meta">' + window.AIWS.esc(it.name) + '<br>' +
        window.AIWS.esc(it.model) + '　' + window.AIWS.esc(it.outSize || it.reqSize) +
        '　用时 ' + window.AIWS.esc(it.seconds) + ' 秒' +
        (it.refs ? '　参考图 ' + it.refs + ' 张' : '') + '</div>' +
        '</div>';
    }).join('');
    if (total > 0) setStatus('生成成功 ✓　本次约 ' + window.AIWS.money(total) + ' 元', 'ok');
  }

  // ------------------------------------------------------------------
  //  初始化
  // ------------------------------------------------------------------
  function init() {
    const cfg = window.AIWS.config || {};

    // 模型下拉
    const models = cfg.models || window.DEFAULT_MODELS || [];
    $('model').innerHTML = models.map(function (m) {
      return '<option value="' + window.AIWS.esc(m) + '">' + window.AIWS.esc(m) + '</option>';
    }).join('');
    if (cfg.model && models.indexOf(cfg.model) >= 0) $('model').value = cfg.model;

    $('size').value = cfg.size || '2K';
    $('customSize').value = cfg.customSize || '';
    $('fmt').value = cfg.format || 'png';
    $('wm').checked = !!cfg.watermark;
    $('customSizeBox').style.display = ($('size').value === 'custom') ? 'block' : 'none';

    $('size').addEventListener('change', function () {
      $('customSizeBox').style.display = (this.value === 'custom') ? 'block' : 'none';
      window.AIWS.saveConfig({ size: this.value }).catch(function () {});
      updateCost();
    });
    $('model').addEventListener('change', function () {
      window.AIWS.saveConfig({ model: this.value }).catch(function () {});
      updateCost();
    });
    $('fmt').addEventListener('change', function () {
      window.AIWS.saveConfig({ format: this.value }).catch(function () {});
    });
    $('wm').addEventListener('change', function () {
      window.AIWS.saveConfig({ watermark: this.checked }).catch(function () {});
    });
    $('customSize').addEventListener('input', updateCost);
    $('prompt').addEventListener('input', updateCost);

    $('genBtn').addEventListener('click', onGenerate);
    $('openFolder').addEventListener('click', async function () {
      const r = await window.AIWS.api('/api/openfolder', {});
      if (!r.ok) alert(r.error || '打不开');
    });

    // 提示词库的「覆盖模式」提示
    const head = document.querySelector('.libHead .libTitle');
    if (head) {
      head.title = '点击 = 追加到描述；右键 或 Shift+点击 = 覆盖';
      head.style.cursor = 'help';
    }

    initLib();
    initRefs();
    renderRefs();
    updateCost();
  }

  document.addEventListener('aiws:ready', init);
})();
