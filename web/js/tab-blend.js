/* AI 溶图 —— 工作站的「🎨 AI 溶图」标签页
   ------------------------------------------------------------------
   后端：/api/blend/status | progress | file | upload | run | openfolder
   两个引擎（界面上选，列表由后端给）：
     algo = 方案 A 纯算法合成（Reinhard 色调迁移 + 边缘羽化 + 接触阴影），秒级、零依赖
     qwen = 方案 B Qwen-Image-Edit-2509-Fusion（走 ComfyUI，扩散模型真融合），1~2 分钟
*/
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var timer = null;

  function setStatus(m, k) {
    var e = $('blStatus');
    if (!e) return;
    e.textContent = m || '';
    e.className = 'status' + (k ? ' ' + k : '');
  }
  async function api(path, body) {
    var r = await fetch(path, body ? {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    } : undefined);
    return r.json();
  }
  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  }

  async function loadStatus() {
    try {
      var s = await api('/api/blend/status');
      var pill = $('blPill');
      if (!s.ok) {
        if (pill) { pill.textContent = '● 不可用'; pill.style.background = '#fdecec'; pill.style.color = '#a32020' }
        setStatus(s.error || '后端没起来', 'err');
        return;
      }
      if (pill) {
        pill.textContent = '● 就绪';
        pill.style.background = '#e8f7ee'; pill.style.color = '#177b3f';
      }
      var bg = $('blBg'), bc = bg.value;
      bg.innerHTML = (s.backgrounds || []).length
        ? '<option value="">（选一张背景）</option>' + s.backgrounds.map(function (v) {
            return '<option value="' + esc(v.name) + '">' + esc(v.name) + '　' + v.sizeText + '</option>';
          }).join('')
        : '<option value="">（还没有背景，点下面上传）</option>';
      if (bc) bg.value = bc;

      var fg = $('blFg'), fc = fg.value;
      fg.innerHTML = (s.subjects || []).length
        ? '<option value="">（选一张主体）</option>' + s.subjects.map(function (v) {
            var tag = v.hasAlpha === false ? '　⚠️没有透明区' : (v.hasAlpha ? '　✓透明底' : '');
            return '<option value="' + esc(v.name) + '">' + esc(v.name) +
              '　' + (v.size || '') + tag + '</option>';
          }).join('')
        : '<option value="">（还没有主体，先抠图再回来）</option>';
      if (fc) fg.value = fc;

      // 引擎选择器（列表由后端给，前端不写死）
      var eng = $('blEngine');
      if (eng && !eng.dataset.filled) {
        eng.innerHTML = (s.engines || [{ id: 'algo', name: '方案 A · 算法合成' }])
          .map(function (e) {
            return '<option value="' + esc(e.id) + '">' + esc(e.name) + '</option>';
          }).join('');
        eng.dataset.filled = '1';
        eng.onchange = applyEngine;
      }
      lastStatus = s;
      applyEngine();
      preview();
    } catch (e) { setStatus('读取失败：' + e.message, 'err') }
  }

  var lastStatus = null;

  /* 按引擎切界面：方案 B 自己决定构图和光影，位置/大小那堆滑块就不显示了 */
  function applyEngine() {
    var eng = $('blEngine');
    var id = eng ? eng.value : 'algo';
    var list = (lastStatus && lastStatus.engines) || [];
    var cur = null;
    for (var i = 0; i < list.length; i++) { if (list[i].id === id) cur = list[i]; }
    var isAI = (id === 'qwen');

    if ($('blEngineHint')) $('blEngineHint').textContent = cur ? (cur.hint || '') : '';
    if ($('blAlgoParams')) $('blAlgoParams').style.display = isAI ? 'none' : '';
    if ($('blFlipWrap')) $('blFlipWrap').style.display = isAI ? 'none' : '';
    if ($('blParamsHint')) {
      $('blParamsHint').textContent = isAI
        ? '方案 B 自己决定构图和光影 —— 这些位置参数不生效'
        : '方案 A 用这些参数手动摆';
    }
    var cb = $('blComfy');
    if (cb) {
      if (!isAI) { cb.style.display = 'none'; }
      else {
        cb.style.display = '';
        var ok = !!(lastStatus && lastStatus.comfyOk);
        cb.className = 'status ' + (ok ? 'ok' : 'err');
        cb.innerHTML = ok
          ? '✓ ' + esc((lastStatus && lastStatus.comfyMsg) || 'ComfyUI 在跑') +
            '　第一次跑要加载 19 GB 模型，会慢一些（可能 2~3 分钟），之后就快了。'
          : '⚠️ ' + esc((lastStatus && lastStatus.comfyMsg) || 'ComfyUI 没在跑') +
            '　→ 去「🐋 本地千问生图」点「启动引擎」；或用 ' +
            'D:\\AI工作站\\_start_comfy_nocudnn.bat 启动（必须带那个 cuDNN 修复参数）。';
      }
    }
    if ($('blGo')) {
      $('blGo').textContent = isAI ? '开始溶图（方案 B · 约 1~2 分钟）' : '开始溶图';
    }
  }

  function preview() {
    var b = $('blBg').value, f = $('blFg').value;
    if (b) {
      $('blBgPreview').style.display = 'block';
      $('blBgImg').src = '/api/blend/file?kind=bg&f=' + encodeURIComponent(b);
    } else { $('blBgPreview').style.display = 'none' }

    if (f) {
      $('blFgPreview').style.display = 'block';
      $('blFgImg').src = '/api/blend/file?kind=fg&f=' + encodeURIComponent(f);
      var sel = $('blFg');
      var opt = sel.options[sel.selectedIndex];
      $('blFgHint').textContent = opt ? opt.textContent.trim() : '';
      if (opt && /没有透明区/.test(opt.textContent)) {
        $('blFgHint').textContent += ' —— 这张图整张不透明，溶图效果会很硬。点「✂️ 前往抠图」先抠一张。';
        $('blFgHint').style.color = '#a32020';
      } else { $('blFgHint').style.color = '' }
    } else { $('blFgPreview').style.display = 'none'; $('blFgHint').textContent = '' }
  }

  async function upload(kind) {
    var inp = kind === 'bg' ? $('blBgFile') : $('blFgFile');
    var f = (inp.files || [])[0];
    inp.value = '';
    if (!f) return;
    if (f.size > 40 * 1024 * 1024) { setStatus('图片超过 40 MB', 'err'); return }
    setStatus('正在上传…', '');
    var fr = new FileReader();
    fr.onload = async function () {
      try {
        var r = await api('/api/blend/upload', { kind: kind, name: f.name, data: fr.result });
        if (!r.ok) { setStatus(r.error, 'err'); return }
        setStatus('已导入：' + r.name + '（' + r.sizeText + '）', 'ok');
        await loadStatus();
        if (kind === 'bg') $('blBg').value = r.name; else $('blFg').value = r.name;
        preview();
      } catch (e) { setStatus('上传失败：' + e.message, 'err') }
    };
    fr.readAsDataURL(f);
  }

  async function go() {
    if (!$('blBg').value) { setStatus('先选一张背景（风景照）', 'err'); return }
    if (!$('blFg').value) { setStatus('先选一张主体（抠好的透明底图）', 'err'); return }
    $('blGo').disabled = true;
    $('blResultCard').style.display = 'none';
    $('blProg').style.display = 'block';
    $('blBar').style.width = '0%';
    $('blLog').textContent = '';
    setStatus('');
    try {
      var r = await api('/api/blend/run', {
        engine: $('blEngine') ? $('blEngine').value : 'algo',
        extra: $('blExtra') ? $('blExtra').value : '',
        bg: $('blBg').value,
        fg: $('blFg').value,
        scale: parseFloat($('blScale').value),
        x: parseFloat($('blX').value),
        y: parseFloat($('blY').value),
        flip: $('blFlip').checked,
        colorMatch: parseFloat($('blColorMatch').value),
        feather: parseFloat($('blFeather').value),
        shadow: parseFloat($('blShadow').value),
        saturation: parseFloat($('blSat').value),
        brightness: parseFloat($('blBri').value),
        format: $('blFmt').value,
        name: ($('blName').value || '').trim()
      });
      if (!r.ok) {
        setStatus(r.error || '提交失败', 'err');
        $('blGo').disabled = false; $('blProg').style.display = 'none'; return;
      }
      poll(r.job);
    } catch (e) { setStatus('失败：' + e.message, 'err'); $('blGo').disabled = false }
  }

  function poll(jid) {
    if (timer) clearInterval(timer);
    timer = setInterval(function () {
      api('/api/blend/progress?job=' + jid).then(function (q) {
        if (!q.ok) return;
        $('blBar').style.width = (q.percent || 0) + '%';
        $('blProgText').textContent = (q.stage || '') + (q.detail ? '　·　' + q.detail : '') +
          '　·　已等 ' + q.elapsed + ' 秒';
        $('blLog').textContent = (q.log || []).join('\n');
        $('blLog').scrollTop = $('blLog').scrollHeight;
        if (q.state === 'running') return;
        clearInterval(timer);
        $('blGo').disabled = false;
        if (q.state === 'done') {
          var x = q.result || {};
          setStatus('✓ 完成：' + x.name + '（' + x.sizeText + '　' + x.w + '×' + x.h +
            '　用时 ' + x.seconds + ' 秒）', 'ok');
          $('blResultCard').style.display = 'block';
          $('blResult').innerHTML =
            '<img style="width:100%;border-radius:12px;border:1px solid var(--line)" src="/api/blend/file?kind=out&f=' +
            encodeURIComponent(x.name) + '">' +
            '<div class="row" style="margin-top:10px;flex-wrap:wrap">' +
            '<span class="muted" style="flex:1">' + x.sizeText +
            '　·　文件已经在你本地了</span>' +
            '<button class="mini" id="blResFolder">📂 打开出片目录</button>' +
            '<a class="mini" style="text-decoration:none" download href="/api/blend/file?kind=out&f=' +
            encodeURIComponent(x.name) + '">💾 下载到下载文件夹</a></div>' +
            '<p class="muted" style="font-size:12.5px;margin-top:6px">' +
            '「打开出片目录」直接去文件夹拿原图（推荐）；' +
            '「下载」只是把本机这份再复制到你的下载文件夹，不联网。</p>';
          var fb = document.getElementById('blResFolder');
          if (fb) fb.onclick = async function () {
            var r = await api('/api/blend/openfolder', {});
            if (!r.ok) setStatus('打不开：' + (r.error || ''), 'err');
          };
        } else {
          setStatus('失败：' + (q.error || ''), 'err');
        }
      }).catch(function () {});
    }, 700);
  }

  /* 两个上传口各绑一个独立拖拽区。
     ★ 必须 stopPropagation —— 否则全局拖拽处理器也会接一手，
       变成「拖到主体区却传成了背景」。 */
  function bindDrop(zoneId, fileInputId, kind) {
    var dz = $(zoneId);
    if (!dz || dz.dataset.wired) return;
    dz.dataset.wired = '1';
    ['dragenter', 'dragover'].forEach(function (ev) {
      dz.addEventListener(ev, function (e) {
        e.preventDefault(); e.stopPropagation();
        dz.classList.add('dragover');
      });
    });
    ['dragleave', 'drop'].forEach(function (ev) {
      dz.addEventListener(ev, function (e) {
        e.preventDefault(); e.stopPropagation();
        if (ev === 'dragleave' && dz.contains(e.relatedTarget)) return;
        dz.classList.remove('dragover');
      });
    });
    dz.addEventListener('drop', function (e) {
      e.preventDefault(); e.stopPropagation();
      var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (!f) { setStatus('没识别到文件 —— 从文件夹直接拖进来最稳', 'err'); return }
      if ((f.type || '').indexOf('image/') !== 0) {
        setStatus('「' + f.name + '」不是图片', 'err'); return;
      }
      if (f.size > 40 * 1024 * 1024) { setStatus('图片超过 40 MB', 'err'); return }
      var inp = $(fileInputId);
      try {
        var dt = new DataTransfer();
        dt.items.add(f);
        inp.files = dt.files;
        inp.dispatchEvent(new Event('change', { bubbles: true }));
      } catch (err) {
        setStatus('这个浏览器不支持拖拽赋值，请点按钮选文件', 'err');
      }
      dz.classList.add('busy');
      setTimeout(function () { dz.classList.remove('busy') }, 1200);
    });
    dz.addEventListener('click', function () { $(fileInputId).click() });
  }

  function init() {
    if (!document.body.contains($('blGo')) || $('blGo').dataset.wired) return;
    $('blGo').dataset.wired = '1';
    $('blGo').onclick = go;
    $('blBgAdd').onclick = function () { $('blBgFile').click() };
    $('blFgAdd').onclick = function () { $('blFgFile').click() };
    $('blBgFile').onchange = function () { upload('bg') };
    $('blFgFile').onchange = function () { upload('fg') };
    $('blBg').onchange = preview;
    $('blFg').onchange = preview;
    bindDrop('blBgDrop', 'blBgFile', 'bg');
    bindDrop('blFgDrop', 'blFgFile', 'fg');
    // ★ 前往抠图：直接切到抠图标签页
    $('blGoMatting').onclick = function () {
      if (window.AIWS && window.AIWS.activate) window.AIWS.activate('matting');
      else location.hash = '#matting';
    };
    // ★ 示例：一键载入示例素材 + 切到方案 B + 填好补充要求
    var demo = $('blDemoUse');
    if (demo) demo.onclick = function () {
      var wantBg = '示例-教室日落.png', wantFg = '示例-粉发少女透明底.png';
      function pick(sel, val) {
        for (var i = 0; i < sel.options.length; i++) {
          if (sel.options[i].value === val) { sel.value = val; return true }
        }
        return false;
      }
      var okB = pick($('blBg'), wantBg), okF = pick($('blFg'), wantFg);
      var st = $('blDemoStatus');
      if (!okB || !okF) {
        if (st) {
          st.textContent = '示例素材不在列表里 —— 点一下「刷新」；或者看 data\\溶图背景 与 ' +
            'data\\溶图主体 里有没有「示例-」开头的两个文件';
          st.className = 'status err';
        }
        return;
      }
      if ($('blEngine')) { $('blEngine').value = 'qwen'; applyEngine(); }
      var ex = $('blExtra');
      if (ex && !ex.value.trim()) ex.value = '人物全身入镜，站在教室里，保持背景不变';
      preview();
      if (st) {
        st.textContent = '✓ 示例素材已选好（引擎已切到方案 B、补充要求也填了）—— 现在点「开始溶图」就行';
        st.className = 'status ok';
      }
    };

    $('blFolder').onclick = async function () {
      var r = await api('/api/blend/openfolder', {});
      if (!r.ok) setStatus('打不开：' + (r.error || ''), 'err');
    };
    var pairs = [['blScale', 'blScaleV', '', 100], ['blX', 'blXV', '', 100],
                 ['blY', 'blYV', '', 100], ['blColorMatch', 'blCm', '', 1],
                 ['blFeather', 'blFv', ' px', 1], ['blShadow', 'blSv', '', 100],
                 ['blSat', 'blSatV', '', 1], ['blBri', 'blBriV', '', 1]];
    pairs.forEach(function (t) {
      var el = $(t[0]);
      el.oninput = function () {
        var v = parseFloat(this.value);
        $(t[1]).textContent = (t[3] === 1 ? v.toFixed(2) : Math.round(v * t[3])) + t[2];
      };
      el.oninput();
    });
    loadStatus();
  }

  document.addEventListener('aiws:tab', function (e) {
    if (e.detail && e.detail.tab === 'blend') init();
  });
  if (location.hash === '#blend') setTimeout(init, 300);
})();
