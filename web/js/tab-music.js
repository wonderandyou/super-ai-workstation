/* AI 音乐工坊（原生）—— 工作站的「🎵 AI 音乐工坊」标签页
   ------------------------------------------------------------------
   后端：/api/music/info | progress | audio | lyrics | list
         /api/music/generate | aiwrite | conf | engine | openfolder
         /api/music/refvoice/upload | voicepack/upload | voicepack/make
   后端是「桥接」：直接复用桌面 AI 音乐工坊工程的代码（见 music_ai.py 注释）。
   ComfyUI 是音乐生成的必需引擎，但最吃内存，所以做成手动启停。
*/
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var tGen = null, tVp = null;

  function setStatus(id, m, k) {
    var e = $(id);
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

  function pill(alive, modelOk) {
    var p = $('msPill');
    if (!p) return;
    if (!modelOk) { p.textContent = '● 模型缺失'; p.style.background = '#fdecec'; p.style.color = '#a32020'; return }
    if (alive) { p.textContent = '● 引擎就绪'; p.style.background = '#e8f7ee'; p.style.color = '#177b3f' }
    else { p.textContent = '● 引擎未启动'; p.style.background = '#fff7e6'; p.style.color = '#9a6a00' }
  }

  async function loadInfo() {
    try {
      var s = await api('/api/music/info');
      if (!s.ok) { pill(false, false); setStatus('msStatus', s.error || '读不到音乐工坊', 'err'); return }
      pill(s.comfyAlive, s.modelOk);
      $('msInfo').textContent = 'ACE-Step 模型 ' + s.modelSizeText + '　·　声音包 ' +
        (s.voices || []).length + ' 个　·　出片 ' + (s.outputs || []).length + ' 首' +
        (s.gpu && s.gpu.name ? '　·　' + s.gpu.name : '');
      window.__msVoices = s.voices || [];   // 给下面的生成前检查用 ✓
      var sel = $('msVoice'), cur = sel.value;
      sel.innerHTML = '<option value="">（不用，随机音色）</option>' +
        (s.voices || []).map(function (v) {
          return '<option value="' + esc(v.name) + '">' + esc(v.name) + '　' + v.sizeText + '</option>';
        }).join('');
      if (cur) sel.value = cur;
      var L = $('msLang');
      if (s.languages && !L.dataset.done) {
        // 语言表是**成对的**：[['zh','中文'], ['en','英语'], …]
        // 以前只认字符串和 {v,t} 对象，拿到数组就成了 undefined → 下拉全空白 ✗（踩过）
        L.innerHTML = s.languages.map(function (x) {
          var v, t;
          if (typeof x === 'string') { v = x; t = x }
          else if (Array.isArray(x)) { v = x[0]; t = x[1] || x[0] }
          else { v = x.v || x.code || x.id; t = x.t || x.name || x.label || v }
          return '<option value="' + esc(v) + '">' + esc(t) + '</option>';
        }).join('');
        L.dataset.done = '1';
      }
      var K = $('msKey');
      if (s.keyscales && !K.dataset.done) {
        K.innerHTML = s.keyscales.map(function (x) { return '<option value="' + esc(x) + '">' + esc(x) + '</option>' }).join('');
        K.dataset.done = '1';
      }
      loadHistory();
    } catch (e) { setStatus('msStatus', '读取失败：' + e.message, 'err') }
  }

  async function loadHistory() {
    try {
      var r = await api('/api/music/list');
      var box = $('msHistory');
      if (!r.ok || !(r.items || []).length) { box.innerHTML = '<span class="muted">还没有作品</span>'; return }
      box.innerHTML = r.items.map(function (it) {
        return '<div style="border-bottom:1px solid var(--line);padding:9px 0">' +
          '<div class="row"><div style="flex:1"><b>' + esc(it.name) + '</b>' +
          '<div class="muted" style="font-size:12.5px">' + it.sizeText + '　' + it.time + '</div></div>' +
          '<button class="mini" data-lrc="' + esc(it.name) + '">看歌词</button></div>' +
          '<audio controls preload="none" style="width:100%;height:34px;margin-top:6px" src="/api/music/audio?f=' +
          encodeURIComponent(it.name) + '"></audio></div>';
      }).join('');
      box.querySelectorAll('[data-lrc]').forEach(function (b) {
        b.onclick = async function () {
          var r2 = await api('/api/music/lyrics?f=' + encodeURIComponent(b.dataset.lrc));
          alert(r2.has ? r2.lrc : '这首歌没有歌词文件');
        };
      });
    } catch (e) { /* 忽略 */ }
  }

  /* ---------- 写歌 ---------- */
  async function go() {
    var tags = ($('msTags').value || '').trim();
    var lyrics = ($('msLyrics').value || '').trim();
    // ★ 实测：人声的男女音色**主要看声音包**（带女声参考 193Hz vs 无参考 161Hz）
    //   所以「有声音包却没选」= 大概率会唱出你不想要的性别 → 提醒一下 ✓
    if (!$('msVoice').value && (window.__msVoices || []).length) {
      if (await AIWS.no('你还没有选「声音包」。\n\n' +
          '实测：人声的男女音色主要取决于声音包（选了女声包才会唱女声），\n' +
          '风格标签里写 female / male 的影响很小。\n\n' +
          '要继续（不指定音色）吗？')) { return }
    }
    if (!lyrics && !tags) { setStatus('msStatus', '至少填风格标签或歌词', 'err'); return }
    $('msGo').disabled = true;
    $('msResultCard').style.display = 'none';
    $('msProg').style.display = 'block';
    $('msBar').style.width = '0%';
    $('msLog').textContent = '';
    setStatus('msStatus', '');
    try {
      var r = await api('/api/music/generate', {
        title: ($('msTitle').value || '').trim(),
        tags: tags,
        lyrics: lyrics || '[inst]',
        duration: parseFloat($('msDur').value),
        bpm: parseInt($('msBpm').value, 10),
        language: $('msLang').value || 'zh',
        keyscale: $('msKey').value || 'C major',
        timesignature: '4',
        refVoice: $('msVoice').value ? ('声音包/' + $('msVoice').value) : '',
        seed: 0
      });
      if (!r.ok) {
        setStatus('msStatus', r.error || '提交失败', 'err');
        $('msGo').disabled = false; $('msProg').style.display = 'none'; return;
      }
      pollGen(r.job);
    } catch (e) { setStatus('msStatus', '失败：' + e.message, 'err'); $('msGo').disabled = false }
  }

  function pollGen(jid) {
    if (tGen) clearInterval(tGen);
    tGen = setInterval(async function () {
      var q;
      try { q = await api('/api/music/progress?job=' + jid) } catch (e) { return }
      if (!q.ok) return;
      $('msBar').style.width = (q.percent || 0) + '%';
      $('msProgText').textContent = (q.stage || '') + (q.detail ? '　·　' + q.detail : '') +
        '　·　已等 ' + q.elapsed + ' 秒';
      $('msLog').textContent = (q.log || []).join('\n');
      $('msLog').scrollTop = $('msLog').scrollHeight;
      if (q.state === 'running') { if (q.stage && /引擎|ComfyUI/.test(q.stage)) pill(false, true); return }
      clearInterval(tGen);
      $('msGo').disabled = false;
      if (q.state === 'done') {
        var res = q.result || {};
        var items = res.items || res.saved || (Array.isArray(res) ? res : []);
        setStatus('msStatus', '✓ 生成完成', 'ok');
        $('msResultCard').style.display = 'block';
        $('msResult').innerHTML = (items || []).map(function (it) {
          return '<div style="margin-bottom:14px"><b>' + esc(it.name) + '</b>' +
            '<div class="muted" style="font-size:12.5px">' + (it.sizeText || '') +
            (it.segments ? '　分段 ' + it.segments : '') +
            (it.lyricsEmbedded ? '　歌词已内嵌' : '') +
            (it.seconds ? '　用时 ' + it.seconds + ' 秒' : '') + '</div>' +
            '<audio controls style="width:100%;height:36px;margin-top:6px" src="/api/music/audio?f=' +
            encodeURIComponent(it.name) + '"></audio></div>';
        }).join('') || '<span class="muted">完成，但没拿到文件名 —— 去出片目录看看</span>';
        loadInfo();
      } else {
        setStatus('msStatus', '失败：' + (q.error || ''), 'err');
      }
    }, 1000);
  }

  /* ---------- 声音包制作器 ---------- */
  async function vpUpload() {
    var f = ($('msVpFile').files || [])[0];
    $('msVpFile').value = '';
    if (!f) return;
    if (f.size > 500 * 1024 * 1024) { setStatus('msVpStatus', '文件超过 500 MB', 'err'); return }
    setStatus('msVpStatus', '正在导入素材…', '');
    var fr = new FileReader();
    fr.onload = async function () {
      try {
        var r = await api('/api/music/voicepack/upload', { name: f.name, data: fr.result });
        if (!r.ok) { setStatus('msVpStatus', r.error, 'err'); return }
        $('msVpSrc').value = r.name;
        var i = r.info || {};
        $('msVpInfo').textContent = i.duration
          ? ('素材：' + Math.round(i.duration) + ' 秒　' + (i.rate || '') + ' Hz　' + (i.channels || '') + ' 声道')
          : '';
        if (i.duration) {
          $('msVpStart').max = Math.max(1, Math.floor(i.duration) - 5);
          $('msVpStart').value = Math.min(30, Math.max(0, Math.floor(i.duration / 4)));
          $('msVpStartV').textContent = $('msVpStart').value;
        }
        setStatus('msVpStatus', '素材就位：' + r.name + '（' + r.sizeText + '）', 'ok');
      } catch (e) { setStatus('msVpStatus', '导入失败：' + e.message, 'err') }
    };
    fr.readAsDataURL(f);
  }

  async function vpGo() {
    if (!$('msVpSrc').value) { setStatus('msVpStatus', '先选一个素材', 'err'); return }
    $('msVpGo').disabled = true;
    $('msVpProg').style.display = 'block';
    $('msVpBar').style.width = '0%';
    $('msVpLog').textContent = '';
    setStatus('msVpStatus', '');
    try {
      var r = await api('/api/music/voicepack/make', {
        src: $('msVpSrc').value,
        start: parseFloat($('msVpStart').value),
        dur: parseFloat($('msVpDur').value),
        separate: true,
        name: ($('msVpName').value || '').trim()
      });
      if (!r.ok) { setStatus('msVpStatus', r.error, 'err'); $('msVpGo').disabled = false; return }
      pollVp(r.job);
    } catch (e) { setStatus('msVpStatus', '失败：' + e.message, 'err'); $('msVpGo').disabled = false }
  }

  function pollVp(jid) {
    if (tVp) clearInterval(tVp);
    tVp = setInterval(async function () {
      var q;
      try { q = await api('/api/music/progress?job=' + jid) } catch (e) { return }
      if (!q.ok) return;
      $('msVpBar').style.width = (q.percent || 0) + '%';
      $('msVpProgText').textContent = (q.stage || '') + '　·　已等 ' + q.elapsed + ' 秒';
      $('msVpLog').textContent = (q.log || []).join('\n');
      $('msVpLog').scrollTop = $('msVpLog').scrollHeight;
      if (q.state === 'running') return;
      clearInterval(tVp);
      $('msVpGo').disabled = false;
      if (q.state === 'done') {
        var x = q.result || {};
        setStatus('msVpStatus', '✓ 声音包已生成：' + (x.name || '') + '　已加入列表', 'ok');
        loadInfo();
      } else { setStatus('msVpStatus', '失败：' + (q.error || ''), 'err') }
    }, 1000);
  }

  /* ---------- 初始化 ---------- */
  function init() {
    if (!document.body.contains($('msGo')) || $('msGo').dataset.wired) return;
    $('msGo').dataset.wired = '1';
    $('msGo').onclick = go;
    // 一键设定人声性别 —— 音色主要靠风格标签，声音包只是轻微影响 ✓
    function setGender(kind) {
      var t = ($('msTags').value || '').trim();
      // 先清掉已有的性别描述，避免叠加成一堆
      t = t.replace(/\b(female|male|woman|man|girl|boy)\s+(lead\s+|backing\s+)?(vocal|vocals|voice|voices)\b/gi, '')
           .replace(/\s*,\s*,/g, ',').replace(/^\s*,|,\s*$/g, '').trim();
      var add = (kind === 'f') ? 'female lead vocal' : 'male lead vocal';
      $('msTags').value = t ? (add + ', ' + t) : add;
      setStatus('msStatus', (kind === 'f' ? '✓ 已写上 female vocal' : '✓ 已写上 male vocal')
        + ' —— 注意：实测这个对音色影响很小，真正管用的是上面「声音包」里选一个', 'ok');
    }
    $('msFemale').onclick = function () { setGender('f') };
    $('msMale').onclick = function () { setGender('m') };

    $('msAI').onclick = async function () {
      var p = await AIWS.input('想让 AI 写一首什么样的歌？\n（描述主题、情绪、风格、语言）',
        '写一首关于' + ($('msTitle').value || '故乡') + '的中文歌，' + ($('msTags').value || '流行'));
      if (!p) return;
      setStatus('msStatus', 'AI 正在写…', '');
      var r = await api('/api/music/aiwrite', { prompt: p });
      if (!r.ok) { setStatus('msStatus', r.error, 'err'); return }
      var d = r.data || r || {};   // 后端是平铺返回的，兼容两种写法 ✓
      if (d.title) $('msTitle').value = d.title;
      if (d.tags) $('msTags').value = d.tags;
      if (d.lyrics) $('msLyrics').value = d.lyrics;
      if (d.bpm) { $('msBpm').value = d.bpm; $('msBpmV').textContent = d.bpm }
      if (d.keyscale) { try { $('msKey').value = d.keyscale } catch (e) {} }
      setStatus('msStatus', '✓ AI 写好了：' + (d.title || ''), 'ok');
    };
    $('msVoiceAdd').onclick = function () { $('msVoiceFile').click() };
    $('msVoiceFile').onchange = async function () {
      var f = (this.files || [])[0]; this.value = '';
      if (!f) return;
      var fr = new FileReader();
      fr.onload = async function () {
        var r = await api('/api/music/refvoice/upload', { name: f.name, data: fr.result });
        if (!r.ok) { setStatus('msStatus', r.error, 'err'); return }
        setStatus('msStatus', '已导入声音包：' + r.name, 'ok');
        await loadInfo();
        $('msVoice').value = r.name;
      };
      fr.readAsDataURL(f);
    };
    $('msVoicePlay').onclick = function () {
      var n = $('msVoice').value;
      if (!n) { setStatus('msStatus', '先选一个声音包', 'err'); return }
      var a = $('msVoiceAudio');
      a.style.display = 'block';
      a.src = '/api/music/audio?f=__ref__&n=' + encodeURIComponent(n);
      a.src = '/api/music/refplay?f=' + encodeURIComponent(n);
      a.play().catch(function () {});
    };
    $('msVpPick').onclick = function () { $('msVpFile').click() };
    $('msVpFile').onchange = vpUpload;
    $('msVpGo').onclick = vpGo;
    $('msFolder').onclick = async function () {
      var r = await api('/api/music/openfolder', {});
      if (!r.ok) setStatus('msStatus', '打不开：' + (r.error || ''), 'err');
    };
    $('msEngineStart').onclick = async function () {
      setStatus('msStatus', '正在启动 ComfyUI（约 20~60 秒）…', '');
      var r = await api('/api/music/engine', { action: 'start' });
      if (!r.ok) { setStatus('msStatus', r.error, 'err'); return }
      setStatus('msStatus', '✓ 引擎就绪', 'ok');
      loadInfo();
    };
    $('msEngineStop').onclick = async function () {
      if (await AIWS.no('停止 ComfyUI？\n\n它是音乐生成的必需引擎，但最占内存（几个 GB）。\n下次生成时会自动重新启动。')) return;
      var r = await api('/api/music/engine', { action: 'stop' });
      setStatus('msStatus', r.ok ? '✓ 引擎已停止，内存已释放' : ('停止失败：' + (r.error || '')),
        r.ok ? 'ok' : 'err');
      loadInfo();
    };
    [['msDur', 'msDurV', ''], ['msBpm', 'msBpmV', ''],
     ['msVpStart', 'msVpStartV', ''], ['msVpDur', 'msVpDurV', ' 秒']].forEach(function (t) {
      var el = $(t[0]);
      if (el) el.oninput = function () { $(t[1]).textContent = this.value + t[2] };
    });
    loadInfo();
  }

  document.addEventListener('aiws:tab', function (e) {
    if (e.detail && e.detail.tab === 'music') init();
  });
  if (location.hash === '#music') setTimeout(init, 300);
})();
