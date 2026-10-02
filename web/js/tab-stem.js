/* AI 人声分离 —— 工作站的「🎚️ 人声分离」标签页
   ------------------------------------------------------------------
   后端：/api/stem/status | progress | file | upload | run | openfolder
   引擎：Demucs htdemucs（Meta 官方，MIT）—— 跑在 ComfyUI 那套 Python 里，
         所以是起子进程跑的；模型在本机有缓存，正常不联网。
*/
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var timer = null;

  function setStatus(m, k) {
    var e = $('stStatus');
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
  function esc(s) { return window.AIWS.esc(s == null ? '' : s); }

  async function loadStatus() {
    try {
      var s = await api('/api/stem/status');
      var pill = $('stPill');
      if (!s.ok) {
        if (pill) { pill.textContent = '● 不可用'; pill.style.background = '#fdecec'; pill.style.color = '#a32020'; }
        setStatus(s.error || '后端没起来', 'err');
        return;
      }
      if (pill) {
        var ready = !!s.ready;
        pill.textContent = ready ? '● 就绪' : '● 缺组件';
        pill.style.background = ready ? '#e8f7ee' : '#fdf3e6';
        pill.style.color = ready ? '#177b3f' : '#8a5a00';
      }

      // 素材下拉
      var sel = $('stSrc'), cur = sel.value;
      var list = s.sources || [];
      sel.innerHTML = list.length
        ? '<option value="">（选一个音频 / 视频）</option>' + list.map(function (v) {
            return '<option value="' + esc(v.name) + '">' + esc(v.name) +
              '　' + esc(v.sizeText) + '</option>';
          }).join('')
        : '<option value="">（还没有素材，点下面上传）</option>';
      if (cur) sel.value = cur;

      // 提示（模型缓存 / 内存）
      var tips = [];
      if (!s.modelCached) {
        tips.push('本地没找到 Demucs 权重缓存 —— 第一次跑会去<b>官方 huggingface.co</b> 下（约 84 MB）。' +
                  '按铁律不换镜像：下不动会直接报错，不会偷偷换个人镜像。');
      }
      if (s.mem && s.mem.commitFreeMB != null && s.mem.commitFreeMB < (s.needCommitMB || 3500)) {
        tips.push('内存偏紧：可用提交量 ' + s.mem.commitFreeMB + ' MB，建议 ' +
                  (s.needCommitMB || 3500) + ' MB 以上 —— 先关掉 ComfyUI 之类再跑。');
      }
      var tb = $('stTips');
      if (tb) {
        tb.innerHTML = tips.join('<br>');
        tb.style.display = tips.length ? 'block' : 'none';
      }
      $('stDir').textContent = s.outDir || '';

      // 出片列表
      var outs = s.outputs || [];
      $('stOuts').innerHTML = outs.length ? outs.map(function (f) {
        return '<div class="stRow"><span class="stName">' + esc(f.name) + '</span>' +
          '<span class="muted">' + esc(f.sizeText) + '　' + esc(f.time) + '</span>' +
          '<a class="mini" style="text-decoration:none" download href="/api/stem/file?kind=out&f=' +
          encodeURIComponent(f.name) + '">💾</a></div>';
      }).join('') : '<p class="muted">（还没出过片）</p>';
    } catch (e) { setStatus('读取失败：' + e.message, 'err') }
  }

  async function upload() {
    var inp = $('stFile');
    var f = (inp.files || [])[0];
    inp.value = '';
    if (!f) return;
    if (f.size > 120 * 1024 * 1024) {
      setStatus('超过 120 MB —— 这么大的文件请直接放进「分离素材」文件夹（界面上有路径）', 'err');
      return;
    }
    setStatus('正在上传…（' + (f.size / 1048576).toFixed(1) + ' MB）', '');
    var fr = new FileReader();
    fr.onload = async function () {
      try {
        var r = await api('/api/stem/upload', { name: f.name, data: fr.result });
        if (!r.ok) { setStatus(r.error, 'err'); return; }
        setStatus('已导入：' + r.name + '（' + r.sizeText + '）', 'ok');
        await loadStatus();
        $('stSrc').value = r.name;
      } catch (e) { setStatus('上传失败：' + e.message, 'err') }
    };
    fr.readAsDataURL(f);
  }

  async function go() {
    if (!$('stSrc').value) { setStatus('先选一个音频 / 视频', 'err'); return }
    $('stGo').disabled = true;
    $('stResultCard').style.display = 'none';
    $('stProg').style.display = 'block';
    $('stBar').style.width = '0%';
    $('stLog').textContent = '';
    setStatus('');
    try {
      var r = await api('/api/stem/run', {
        source: $('stSrc').value,
        mode: $('stMode').value,
        format: $('stFmt').value,
        name: ($('stName').value || '').trim()
      });
      if (!r.ok) {
        setStatus(r.error || '提交失败', 'err');
        $('stGo').disabled = false; $('stProg').style.display = 'none'; return;
      }
      poll(r.job);
    } catch (e) { setStatus('失败：' + e.message, 'err'); $('stGo').disabled = false }
  }

  function poll(jid) {
    if (timer) clearInterval(timer);
    timer = setInterval(function () {
      api('/api/stem/progress?job=' + jid).then(function (q) {
        if (!q.ok) return;
        $('stBar').style.width = (q.percent || 0) + '%';
        $('stProgText').textContent = (q.stage || '') + (q.detail ? '　·　' + q.detail : '') +
          '　·　已等 ' + q.elapsed + ' 秒';
        $('stLog').textContent = (q.log || []).join('\n');
        $('stLog').scrollTop = $('stLog').scrollHeight;
        if (q.state === 'running') return;
        clearInterval(timer);
        $('stGo').disabled = false;
        if (q.state === 'done') {
          var res = q.result || {};
          setStatus('✓ 分离完成：' + (res.count || 0) + ' 个文件　用时 ' + res.seconds + ' 秒', 'ok');
          renderResult(res);
          loadStatus();
        } else {
          setStatus('失败：' + (q.error || ''), 'err');
        }
      }).catch(function () {});
    }, 1000);
  }

  function renderResult(res) {
    $('stResultCard').style.display = 'block';
    $('stResMeta').textContent = '用时 ' + (res.seconds == null ? '?' : res.seconds) +
      ' 秒　·　' + (res.mode === 'four' ? '四轨（人声/鼓/贝斯/其他）' : '两轨（人声 + 伴奏）') +
      '　·　格式 ' + String(res.format || '').toUpperCase();
    $('stFiles').innerHTML = (res.files || []).map(function (f) {
      return '<div class="stRow"><span class="stTag">' + esc(f.stem || '') + '</span>' +
        '<span class="stName">' + esc(f.name) + '</span>' +
        '<span class="muted">' + esc(f.sizeText) + '</span>' +
        '<a class="mini" style="text-decoration:none" download href="/api/stem/file?kind=out&f=' +
        encodeURIComponent(f.name) + '">💾 下载</a></div>';
    }).join('');
    $('stOutHint').textContent = '文件也在：' + ($('stDir').textContent || '');
  }

  function init() {
    if (!document.body.contains($('stGo')) || $('stGo').dataset.wired) return;
    $('stGo').dataset.wired = '1';
    $('stGo').onclick = go;
    $('stAdd').onclick = function () { $('stFile').click() };
    $('stFile').onchange = upload;
    $('stRefresh').onclick = loadStatus;
    $('stFolder').onclick = async function () {
      var r = await api('/api/stem/openfolder', {});
      if (!r.ok) setStatus('打不开：' + (r.error || ''), 'err');
    };
    // 拖拽上传
    var dz = $('stDrop');
    if (dz) {
      ['dragenter', 'dragover'].forEach(function (ev) {
        dz.addEventListener(ev, function (e) { e.preventDefault(); e.stopPropagation(); dz.classList.add('dragover'); });
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
        if (!f) { setStatus('没识别到文件', 'err'); return }
        var inp = $('stFile');
        try {
          var dt = new DataTransfer();
          dt.items.add(f);
          inp.files = dt.files;
          inp.dispatchEvent(new Event('change', { bubbles: true }));
        } catch (err) { setStatus('这个浏览器不支持拖拽赋值，请点按钮选文件', 'err') }
      });
      dz.addEventListener('click', function () { $('stFile').click() });
    }
    loadStatus();
  }

  document.addEventListener('aiws:tab', function (e) {
    if (e.detail && e.detail.tab === 'stem') init();
  });
  if (location.hash === '#stem') setTimeout(init, 300);
})();
