/* 声音包朗读（F5-TTS）—— 工作站的「📖 声音包朗读」标签页
   ------------------------------------------------------------------
   后端：/api/tts/status | progress | audio | list | run | openfolder | refopen
   跑在 F5-TTS 自己的 venv 里（torch 2.11+cu128，和工作站那套不同），
   所以后端是起子进程、解析 stdout 来报进度的。
*/
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var ready = false;
  var timer = null;

  function setStatus(m, k) {
    var e = $('ttsStatus');
    if (!e) return;
    e.textContent = m || '';
    e.className = 'status' + (k ? ' ' + k : '');
  }

  async function api(path, body) {
    var opt = body ? {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    } : undefined;
    var r = await fetch(path, opt);
    return r.json();
  }

  /* ---------- 声音包 ---------- */
  async function loadVoices() {
    try {
      var s = await api('/api/tts/status');
      var pill = $('ttsPill');
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
      var sel = $('ttsVoice');
      var cur = sel.value;
      var vs = s.voices || [];
      sel.innerHTML = vs.length
        ? vs.map(function (v) {
            return '<option value="' + esc(v.name) + '">' + esc(v.name) + '　' + v.sizeText + '</option>';
          }).join('')
        : '<option value="">（还没有声音包）</option>';
      if (cur) sel.value = cur;
      if (!vs.length) {
        setStatus('还没有声音包 —— 去「🎵 AI 音乐工坊」的声音包制作器里做一个', 'err');
      }
      ready = !!s.ready;
    } catch (e) {
      setStatus('读取状态失败：' + e.message, 'err');
    }
  }

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  }

  /* ---------- 出片历史 ---------- */
  async function loadHistory() {
    try {
      var r = await api('/api/tts/list');
      var box = $('ttsHistory');
      if (!r.ok || !(r.items || []).length) {
        box.innerHTML = '<span class="muted">还没有生成过</span>';
        return;
      }
      box.innerHTML = r.items.map(function (it) {
        return '<div class="row" style="border-bottom:1px solid var(--line);padding:8px 0">' +
          '<div style="flex:1"><b>' + esc(it.name) + '</b>' +
          '<div class="muted" style="font-size:12.5px">' + it.sizeText + '　' + it.time + '</div></div>' +
          '<audio controls preload="none" style="height:34px;width:260px" src="/api/tts/audio?f=' +
          encodeURIComponent(it.name) + '"></audio>' +
          '</div>';
      }).join('');
    } catch (e) { /* 忽略 */ }
  }

  /* ---------- 开始朗读 ---------- */
  async function go() {
    var text = ($('ttsText').value || '').trim();
    if (!text) { setStatus('先输入要朗读的文字', 'err'); return }
    if (text.length > 600) { setStatus('文字超过 600 字，请分次', 'err'); return }
    var voice = $('ttsVoice').value;
    if (!voice) { setStatus('先选一个声音包', 'err'); return }

    $('ttsGo').disabled = true;
    $('ttsResultCard').style.display = 'none';
    $('ttsProg').style.display = 'block';
    $('ttsBar').style.width = '0%';
    $('ttsLog').textContent = '';
    setStatus('');

    try {
      var r = await api('/api/tts/run', {
        text: text,
        voice: voice,
        refText: ($('ttsRefText').value || '').trim(),
        name: ($('ttsName').value || '').trim()
      });
      if (!r.ok) {
        setStatus(r.error || '提交失败', 'err');
        $('ttsGo').disabled = false;
        $('ttsProg').style.display = 'none';
        return;
      }
      poll(r.job);
    } catch (e) {
      setStatus('失败：' + e.message, 'err');
      $('ttsGo').disabled = false;
    }
  }

  function poll(jid) {
    if (timer) clearInterval(timer);
    timer = setInterval(async function () {
      var q;
      try { q = await api('/api/tts/progress?job=' + jid) } catch (e) { return }
      if (!q.ok) return;
      $('ttsBar').style.width = (q.percent || 0) + '%';
      $('ttsProgText').textContent = (q.stage || '') +
        (q.detail ? '　·　' + q.detail : '') + '　·　已等 ' + q.elapsed + ' 秒';
      $('ttsLog').textContent = (q.log || []).join('\n');
      $('ttsLog').scrollTop = $('ttsLog').scrollHeight;
      if (q.state === 'running') return;
      clearInterval(timer);
      $('ttsGo').disabled = false;
      if (q.state === 'done') {
        var x = q.result || {};
        setStatus('✓ 完成：' + x.name + '（' + x.sizeText + '，' + x.seconds + ' 秒音频，用时 ' +
          x.cost + ' 秒）', 'ok');
        $('ttsResultCard').style.display = 'block';
        $('ttsResult').innerHTML =
          '<audio controls style="width:100%" src="/api/tts/audio?f=' +
          encodeURIComponent(x.name) + '"></audio>' +
          '<div class="row" style="margin-top:10px;flex-wrap:wrap">' +
          '<span class="muted" style="flex:1">' + x.sizeText + '　' + x.seconds + ' 秒</span>' +
          '<button class="mini" id="ttsResFolder">📂 打开出片目录</button>' +
          '<a class="mini" style="text-decoration:none" download href="/api/tts/audio?f=' +
          encodeURIComponent(x.name) + '">💾 下载</a></div>';
        var fb = document.getElementById('ttsResFolder');
        if (fb) fb.onclick = async function () {
          var r = await api('/api/tts/openfolder', {});
          if (!r.ok) setStatus('打不开：' + (r.error || ''), 'err');
        };
        loadHistory();
      } else {
        setStatus('失败：' + (q.error || ''), 'err');
      }
    }, 900);
  }

  /* ---------- 初始化 ---------- */
  function init() {
    if (!document.body.contains($('ttsGo')) || $('ttsGo').dataset.wired) return;
    $('ttsGo').dataset.wired = '1';
    $('ttsGo').onclick = go;
    $('ttsText').oninput = function () {
      var n = this.value.length;
      $('ttsCount').textContent = n + ' / 600 字';
    };
    $('ttsFolder').onclick = async function () {
      var r = await api('/api/tts/openfolder', {});
      if (!r.ok) setStatus('打不开：' + (r.error || ''), 'err');
    };
    $('ttsRefOpen').onclick = async function () {
      var r = await api('/api/tts/refopen', {});
      if (!r.ok) setStatus('打不开：' + (r.error || ''), 'err');
    };
    loadVoices();
    loadHistory();
  }

  // 切到这个标签页时才初始化（省得每次开页面都请求）
  document.addEventListener('aiws:tab', function (e) {
    if (e.detail && e.detail.tab === 'tts') init();
  });
  // 如果页面直接以 #tts 打开
  if (location.hash === '#tts') setTimeout(init, 300);
})();
