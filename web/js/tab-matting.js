/* AI 抠图 —— 工作站的「✂️ AI 抠图」标签页
   ------------------------------------------------------------------
   后端：/api/matting/status | progress | file | list
         /api/matting/upload | run | free | openfolder
   后端是「桥接」：复用用户「文档」目录下的 抠图\抠图-AI.py
   （BRIA RMBG 系列 ONNX + onnxruntime，纯本地）。
*/
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var timer = null, cur = '';

  function setStatus(m, k) {
    var e = $('mtStatus');
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
  function url(p) { return p; }

  async function loadStatus() {
    try {
      var s = await api('/api/matting/status');
      var pill = $('mtPill');
      if (!s.ok) {
        if (pill) { pill.textContent = '● 不可用'; pill.style.background = '#fdecec'; pill.style.color = '#a32020' }
        setStatus(s.error || '后端没起来', 'err');
        return;
      }
      var ready = (s.models || []).filter(function (m) { return m.ready }).length;
      if (pill) {
        pill.textContent = ready ? '● 就绪（' + ready + ' 个模型）' : '● 没有模型';
        pill.style.background = ready ? '#e8f7ee' : '#fff7e6';
        pill.style.color = ready ? '#177b3f' : '#9a6a00';
      }
      $('mtInfo').textContent = 'onnxruntime ' + s.onnxruntime +
        (s.loadedSessions && s.loadedSessions.length
          ? '　·　已加载 ' + s.loadedSessions.join('/') + '（占内存）' : '　·　没有模型常驻内存');
      // 引擎说明存起来给 showEngineNote 用
      window.MT_ENGINES = {};
      (s.engines || []).forEach(function (e) { window.MT_ENGINES[e.id] = e });

      var es = $('mtEngine'), ec = es.value;
      es.innerHTML = (s.engines || []).map(function (e) {
        return '<option value="' + esc(e.id) + '"' + (e.ready ? '' : ' disabled') + '>' +
          esc(e.name) + (e.ready ? '' : '（不可用）') + '</option>';
      }).join('');
      if (ec) es.value = ec;
      showEngineNote();

      var ms = $('mtModel'), mc = ms.value;
      ms.innerHTML = (s.models || []).map(function (m) {
        return '<option value="' + esc(m.name) + '"' + (m.ready ? '' : ' disabled') + '>' +
          esc(m.name) + (m.ready ? '　' + m.sizeText : '　（缺文件）') + '</option>';
      }).join('');
      if (mc) ms.value = mc;
      showNote();

      var ss = $('mtSrc'), sc = ss.value;
      var srcs = s.sources || [];
      ss.innerHTML = srcs.length
        ? srcs.map(function (v) {
            return '<option value="' + esc(v.name) + '">' + esc(v.name) + '　' + v.sizeText + '</option>';
          }).join('')
        : '<option value="">（还没上传过图片）</option>';
      if (sc) ss.value = sc;
      preview();
    } catch (e) { setStatus('读取失败：' + e.message, 'err') }
  }

  function showEngineNote() {
    var es = $('mtEngine');
    var opt = es.options[es.selectedIndex];
    var isComfy = es.value === 'comfy';
    if (opt) {
      var s = (window.MT_ENGINES || {})[es.value] || {};
      $('mtEngineNote').textContent = s.note || '';
    }
    // comfy 引擎下模型是固定的，禁掉下拉免得误导
    $('mtModel').disabled = isComfy;
    if (isComfy) {
      $('mtModelNote').textContent = 'ComfyUI 内置 BiRefNet（模型固定，会按需自动启动 ComfyUI）';
    } else {
      showNote();
    }
  }

  function showNote() {
    var ms = $('mtModel');
    var opt = ms.options[ms.selectedIndex];
    $('mtModelNote').textContent = opt ? (opt.textContent.split('　').slice(1).join('　')) : '';
  }

  function preview() {
    var n = $('mtSrc').value;
    var box = $('mtPreviewBox');
    if (!n) { box.style.display = 'none'; return }
    box.style.display = 'block';
    $('mtPreview').src = '/api/matting/file?kind=src&f=' + encodeURIComponent(n);
  }

  async function upload() {
    var f = ($('mtFile').files || [])[0];
    $('mtFile').value = '';
    if (!f) return;
    await uploadOne(f);
  }

  /* 上传单张。返回上传成功后的文件名（失败返回 ''） */
  async function uploadOne(f) {
    if (!f.type || f.type.indexOf('image/') !== 0) {
      setStatus('「' + f.name + '」不是图片，跳过', 'err');
      return '';
    }
    if (f.size > 40 * 1024 * 1024) {
      setStatus('「' + f.name + '」超过 40 MB，跳过', 'err');
      return '';
    }
    return new Promise(function (resolve) {
      var fr = new FileReader();
      fr.onload = async function () {
        try {
          var r = await api('/api/matting/upload', { name: f.name, data: fr.result });
          if (!r.ok) { setStatus('「' + f.name + '」上传失败：' + (r.error || ''), 'err'); resolve(''); return }
          resolve(r.name);
        } catch (e) { setStatus('「' + f.name + '」上传失败：' + e.message, 'err'); resolve('') }
      };
      fr.onerror = function () { resolve('') };
      fr.readAsDataURL(f);
    });
  }

  /* 批量上传（拖进来的多张） */
  async function uploadMany(files) {
    var list = Array.prototype.slice.call(files || []);
    if (!list.length) return;
    var dz = $('mtDrop');
    dz.classList.add('busy');
    setStatus('正在上传 ' + list.length + ' 张…', '');
    var okList = [];
    for (var i = 0; i < list.length; i++) {
      setStatus('正在上传 ' + (i + 1) + '/' + list.length + '：' + list[i].name, '');
      var n = await uploadOne(list[i]);
      if (n) okList.push(n);
    }
    dz.classList.remove('busy');
    await loadStatus();
    if (okList.length) {
      $('mtSrc').value = okList[okList.length - 1];
      preview();
      setStatus('✓ 成功导入 ' + okList.length + ' 张' +
        (okList.length > 1 ? '，已选中最后一张：' + okList[okList.length - 1] : '：' + okList[0]), 'ok');
    }
  }

  /* 拖拽上传 */
  function initDrop() {
    var dz = $('mtDrop');
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
      var dt = e.dataTransfer;
      if (!dt) return;
      // 从桌面拖文件进来：优先 files
      if (dt.files && dt.files.length) { uploadMany(dt.files); return }
      // 从网页里拖图片进来：拿 URL 让后端下载
      var url = dt.getData('text/uri-list') || dt.getData('text/plain');
      if (url && /^https?:\/\//i.test(url)) {
        fetch(url).then(function (r) { return r.blob() }).then(function (b) {
          var name = decodeURIComponent((url.split('/').pop() || 'image').split('?')[0]) || 'image.png';
          if (!/\.(png|jpe?g|webp|bmp)$/i.test(name)) name += '.png';
          var file = new File([b], name, { type: b.type || 'image/png' });
          uploadMany([file]);
        }).catch(function () { setStatus('拖进来的链接取不到内容', 'err') });
        return;
      }
      setStatus('没识别出图片 —— 从文件夹里直接拖文件进来最稳', 'err');
    });
    // 点一下也能选文件
    dz.addEventListener('click', function () { $('mtFile').click() });
    // 页面其他地方的拖拽不要触发浏览器默认打开图片
    ['dragover', 'drop'].forEach(function (ev) {
      document.addEventListener(ev, function (e) {
        if (dz.contains(e.target)) return;
        e.preventDefault();
      });
    });
  }

  async function go() {
    if (!$('mtSrc').value) { setStatus('先选一张图', 'err'); return }
    $('mtGo').disabled = true;
    $('mtResultCard').style.display = 'none';
    $('mtProg').style.display = 'block';
    $('mtBar').style.width = '0%';
    $('mtLog').textContent = '';
    setStatus('');
    var hard = parseFloat($('mtHard').value);
    cur = $('mtSrc').value;
    try {
      var r = await api('/api/matting/run', {
        source: $('mtSrc').value,
        engine: $('mtEngine').value,
        model: $('mtModel').value,
        lo: parseFloat($('mtLo').value),
        hi: parseFloat($('mtHi').value),
        hard: hard > 0 ? hard : null,
        crop: $('mtCrop').checked,
        avatar: parseInt($('mtAvatar').value, 10) || 0,
        noDecontam: $('mtNoDecontam').checked,
        cpu: $('mtCpu').checked
      });
      if (!r.ok) {
        setStatus(r.error || '提交失败', 'err');
        $('mtGo').disabled = false; $('mtProg').style.display = 'none'; return;
      }
      poll(r.job);
    } catch (e) { setStatus('失败：' + e.message, 'err'); $('mtGo').disabled = false }
  }

  function poll(jid) {
    if (timer) clearInterval(timer);
    timer = setInterval(async function () {
      var q;
      try { q = await api('/api/matting/progress?job=' + jid) } catch (e) { return }
      if (!q.ok) return;
      $('mtBar').style.width = (q.percent || 0) + '%';
      $('mtProgText').textContent = (q.stage || '') + (q.detail ? '　·　' + q.detail : '') +
        '　·　已等 ' + q.elapsed + ' 秒';
      $('mtLog').textContent = (q.log || []).join('\n');
      $('mtLog').scrollTop = $('mtLog').scrollHeight;
      if (q.state === 'running') return;
      clearInterval(timer);
      $('mtGo').disabled = false;
      if (q.state === 'done') {
        var res = q.result || {};
        var files = res.files || [];
        setStatus('✓ 完成，产出 ' + files.length + ' 个文件，用时 ' + res.seconds + ' 秒', 'ok');
        $('mtResultCard').style.display = 'block';
        // 透明底的一定是 png；优先展示带「透明底」的那个
        var main = files.filter(function (f) { return /透明底/.test(f.name) && !/裁边|预览|头像/.test(f.name) })[0]
          || files.filter(function (f) { return /\.png$/i.test(f.name) })[0] || files[0];
        var rel = jid + '/' + main.name;
        $('mtResult').innerHTML =
          '<div style="text-align:center;background:repeating-conic-gradient(#e9edf2 0% 25%,#fff 0% 50%) 50%/20px 20px;border-radius:12px;padding:12px">' +
          '<img style="max-width:100%;max-height:420px" src="/api/matting/file?kind=out&p=' +
          encodeURIComponent(rel) + '"></div>' +
          '<div class="row" style="margin-top:12px;flex-wrap:wrap">' +
          '<button class="mini" id="mtResFolder">📂 打开出片目录</button>' +
          files.map(function (f) {
            var p = encodeURIComponent(jid + '/' + f.name);
            return '<a class="mini" style="text-decoration:none" download href="/api/matting/file?kind=out&p=' +
              p + '">' + esc(f.name) + '（' + f.sizeText + '）</a>';
          }).join('') + '</div>';
        var fb = document.getElementById('mtResFolder');
        if (fb) fb.onclick = async function () {
          var r = await api('/api/matting/openfolder', { kind: 'out' });
          if (!r.ok) setStatus('打不开：' + (r.error || ''), 'err');
        };
        loadStatus();
      } else {
        setStatus('失败：' + (q.error || ''), 'err');
      }
    }, 800);
  }

  function init() {
    if (!document.body.contains($('mtGo')) || $('mtGo').dataset.wired) return;
    $('mtGo').dataset.wired = '1';
    $('mtGo').onclick = go;
    $('mtPick').onclick = function () { $('mtFile').click() };
    $('mtFile').onchange = upload;
    initDrop();
    $('mtSrcFolder').onclick = async function () {
      var r = await api('/api/matting/openfolder', { kind: 'src' });
      if (!r.ok) setStatus('打不开：' + (r.error || ''), 'err');
      else setStatus('已打开素材目录：' + (r.dir || ''), 'ok');
    };
    $('mtSrc').onchange = preview;
    $('mtModel').onchange = showNote;
    $('mtEngine').onchange = showEngineNote;
    $('mtFolder').onclick = async function () {
      var r = await api('/api/matting/openfolder', { kind: 'out' });
      if (!r.ok) setStatus('打不开：' + (r.error || ''), 'err');
    };
    $('mtFree').onclick = async function () {
      var r = await api('/api/matting/free', {});
      setStatus(r.ok ? ('✓ 已释放 ' + r.freed + ' 个模型的内存') : ('失败：' + (r.error || '')),
        r.ok ? 'ok' : 'err');
      loadStatus();
    };
    [['mtLo', 'mtLoV', ''], ['mtHi', 'mtHiV', ''], ['mtAvatar', 'mtAvatarV', '']].forEach(function (t) {
      $(t[0]).oninput = function () { $(t[1]).textContent = this.value; };
    });
    $('mtHard').oninput = function () {
      $('mtHardV').textContent = (parseFloat(this.value) > 0 ? this.value : '关');
    };
    $('mtAvatar').oninput = function () {
      $('mtAvatarV').textContent = (parseInt(this.value, 10) > 0 ? this.value + 'px' : '不出');
    };
    loadStatus();
  }

  document.addEventListener('aiws:tab', function (e) {
    if (e.detail && e.detail.tab === 'matting') init();
  });
  if (location.hash === '#matting') setTimeout(init, 300);
})();
