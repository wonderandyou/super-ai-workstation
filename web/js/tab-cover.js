/* AI 翻唱 —— 工作站的「🎤 AI 翻唱」标签页
   ------------------------------------------------------------------
   后端：/api/cover/status | progress | audio | src | list | upload | run | openfolder
   流程跨两个 Python 环境（Demucs 在 ComfyUI 那套、Seed-VC 在自己 venv），
   后端用子进程编排，这里只负责界面和进度。
*/
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var timer = null;

  function setStatus(m, k) {
    var e = $('cvStatus');
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
      var s = await api('/api/cover/status');
      var pill = $('cvPill');
      if (!s.ok) {
        if (pill) { pill.textContent = '● 后端未就绪'; pill.style.background = '#fdecec'; pill.style.color = '#a32020'; }
        setStatus(s.error || '后端没起来', 'err');
        return;
      }
      if (pill) {
        pill.textContent = s.ready ? '● 就绪' : '● 环境不全';
        pill.style.background = s.ready ? '#e8f7ee' : '#fff7e6';
        pill.style.color = s.ready ? '#177b3f' : '#9a6a00';
      }
      var sel = $('cvVoice');
      var cur = sel.value;
      sel.innerHTML = (s.voices || []).length
        ? s.voices.map(function (v) {
            return '<option value="' + esc(v.name) + '">' + esc(v.name) + '</option>';
          }).join('')
        : '<option value="">（还没有声音包）</option>';
      if (cur) sel.value = cur;
      if (s.mem) {
        $('cvSrcInfo').textContent = '可用物理 ' + s.mem.physFreeMB +
          ' MB ／ 可用提交量 ' + s.mem.commitFreeMB + ' MB（至少需要 ' + s.needCommitMB + ' MB）';
      }
    } catch (e) { setStatus('读取状态失败：' + e.message, 'err') }
  }

  async function loadHistory() {
    try {
      var r = await api('/api/cover/list');
      var box = $('cvHistory');
      if (!r.ok || !(r.items || []).length) {
        box.innerHTML = '<span class="muted">还没有翻唱过</span>';
        return;
      }
      box.innerHTML = r.items.map(function (it) {
        return '<div class="row" style="border-bottom:1px solid var(--line);padding:8px 0">' +
          '<div style="flex:1"><b>' + esc(it.name) + '</b>' +
          '<div class="muted" style="font-size:12.5px">' + it.sizeText + '　' + it.time + '</div></div>' +
          '<audio controls preload="none" style="height:34px;width:280px" src="/api/cover/audio?f=' +
          encodeURIComponent(it.name) + '"></audio></div>';
      }).join('');
    } catch (e) { /* 忽略 */ }
  }

  async function upload() {
    var f = ($('cvFile').files || [])[0];
    $('cvFile').value = '';
    if (!f) return;
    if (f.size > 300 * 1024 * 1024) { setStatus('文件超过 300 MB', 'err'); return }
    setStatus('正在导入歌曲…', '');
    var fr = new FileReader();
    fr.onload = async function () {
      try {
        var r = await api('/api/cover/upload', { name: f.name, data: fr.result });
        if (!r.ok) { setStatus(r.error, 'err'); return }
        $('cvSrc').value = r.name;
        setStatus('已导入：' + r.name + '（' + r.sizeText + '）', 'ok');
      } catch (e) { setStatus('导入失败：' + e.message, 'err') }
    };
    fr.readAsDataURL(f);
  }

  function playSrc() {
    var n = $('cvSrc').value;
    if (!n) { setStatus('先选一首歌', 'err'); return }
    var a = $('cvAudio');
    a.style.display = 'block';
    a.src = '/api/cover/src?f=' + encodeURIComponent(n);
    a.play().catch(function () {});
  }

  async function go() {
    if (!$('cvSrc').value) { setStatus('先选一首歌', 'err'); return }
    if (!$('cvVoice').value) { setStatus('先选一个声音包', 'err'); return }
    $('cvGo').disabled = true;
    $('cvResultCard').style.display = 'none';
    $('cvProg').style.display = 'block';
    $('cvBar').style.width = '0%';
    $('cvLog').textContent = '';
    setStatus('');
    try {
      var r = await api('/api/cover/run', {
        source: $('cvSrc').value,
        voice: $('cvVoice').value,
        steps: parseInt($('cvSteps').value, 10),
        cfgRate: parseFloat($('cvCfg').value),
        polish: parseFloat(($('cvPolish') || {}).value || 0.6),
        vocalGain: parseFloat($('cvVg').value),
        instGain: parseFloat($('cvIg').value),
        semiTone: parseInt($('cvSemi').value, 10) || 0,
        f0: $('cvF0').checked,
        name: ($('cvName').value || '').trim()
      });
      if (!r.ok) {
        setStatus(r.error || '提交失败', 'err');
        $('cvGo').disabled = false; $('cvProg').style.display = 'none';
        return;
      }
      poll(r.job);
    } catch (e) {
      setStatus('失败：' + e.message, 'err');
      $('cvGo').disabled = false;
    }
  }

  function poll(jid) {
    if (timer) clearInterval(timer);
    timer = setInterval(async function () {
      var q;
      try { q = await api('/api/cover/progress?job=' + jid) } catch (e) { return }
      if (!q.ok) return;
      $('cvBar').style.width = (q.percent || 0) + '%';
      $('cvProgText').textContent = (q.stage || '') + (q.detail ? '　·　' + q.detail : '') +
        '　·　已等 ' + q.elapsed + ' 秒';
      $('cvLog').textContent = (q.log || []).join('\n');
      $('cvLog').scrollTop = $('cvLog').scrollHeight;
      if (q.state === 'running') return;
      clearInterval(timer);
      $('cvGo').disabled = false;
      if (q.state === 'done') {
        var x = q.result || {};
        setStatus('✓ 翻唱完成：' + x.name + '（' + x.sizeText + '，用时 ' + x.cost + ' 秒）', 'ok');
        $('cvResultCard').style.display = 'block';
        $('cvResult').innerHTML =
          '<audio controls style="width:100%" src="/api/cover/audio?f=' +
          encodeURIComponent(x.name) + '"></audio>' +
          '<div class="row" style="margin-top:10px;flex-wrap:wrap"><span class="muted" style="flex:1">' + x.sizeText + '</span>' +
          '<button class="mini" id="cvResFolder">📂 打开出片目录</button>' +
          '<a class="mini" style="text-decoration:none" download href="/api/cover/audio?f=' +
          encodeURIComponent(x.name) + '">💾 下载</a></div>';
        var fb = document.getElementById('cvResFolder');
        if (fb) fb.onclick = async function () {
          var r = await api('/api/cover/openfolder', {});
          if (!r.ok) setStatus('打不开：' + (r.error || ''), 'err');
        };
        loadHistory();
      } else {
        setStatus('失败：' + (q.error || ''), 'err');
      }
    }, 1200);
  }

  function init() {
    if (!document.body.contains($('cvGo')) || $('cvGo').dataset.wired) return;
    $('cvGo').dataset.wired = '1';
    $('cvGo').onclick = go;
    $('cvPick').onclick = function () { $('cvFile').click() };
    $('cvFile').onchange = upload;
    $('cvSrcPlay').onclick = playSrc;
    $('cvFolder').onclick = async function () {
      var r = await api('/api/cover/openfolder', {});
      if (!r.ok) setStatus('打不开：' + (r.error || ''), 'err');
    };
    [['cvSteps', 'cvStepsV', ''], ['cvCfg', 'cvCfgV', ''],
     ['cvPolish', 'cvPolishV', ''],          // ★ 之前漏了它 → 拖了数字不变，看着像没反应 ✗
     ['cvVg', 'cvVgV', ''], ['cvIg', 'cvIgV', '']].forEach(function (t) {
      var el = $(t[0]);
      if (!el) return;
      el.oninput = function () { $(t[1]).textContent = this.value; };
    });
    loadStatus();
    loadHistory();
  }

  document.addEventListener('aiws:tab', function (e) {
    if (e.detail && e.detail.tab === 'cover') init();
  });
  if (location.hash === '#cover') setTimeout(init, 300);
})();
