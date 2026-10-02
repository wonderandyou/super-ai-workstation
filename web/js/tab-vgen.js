/* ==========================================================================
   AI 生视频（智谱 CogVideoX-Flash）— 2026-10-01
   ========================================================================== */
(function () {
  var AIWS = window.AIWS = window.AIWS || {};
  var $ = function (id) { return document.getElementById(id); };
  var jobId = null, pollTries = 0, initB64 = '';

  function setStatus(t, cls) {
    var el = $('vgenStatus');
    if (el) { el.textContent = t || ''; el.className = 'status' + (cls ? ' ' + cls : ''); }
  }

  function loadModels() {
    var sel = $('vgenModel');
    if (!sel) return;
    (AIWS.api ? AIWS.api('/api/vgen/status') : Promise.resolve({})).then(function (r) {
      var st = r.status || {}, models = r.models || [];
      if (sel.options.length === 0 && models.length) {
        sel.innerHTML = models.map(function (m) {
          return '<option value="' + m.id + '">' + m.name + '</option>';
        }).join('');
        sel.onchange = function () { showNote(models); };
        showNote(models);
      }
      if ($('vgenOutDir')) $('vgenOutDir').textContent = r.outDir || '—';
      var ok = !!st.ok;
      var pill = $('vgenPill'), txt = $('vgenPillText');
      if (pill) pill.className = 'pill' + (ok ? ' up' : ' down');
      if (txt) txt.textContent = ok ? 'Key 可用 ✓' : '还没配 Key';
      if ($('vgenKeyState')) {
        $('vgenKeyState').textContent = ok ? '已配置且可用 ✓' : (st.error || '未配置');
        $('vgenKeyState').style.color = ok ? '#146c3f' : '#9a6b00';
      }
      if (!r.hasKey && !ok) setStatus('先去「设置」填智谱 API Key，或在下面填临时的', 'warn');
    }).catch(function () {});
  }

  function showNote(models) {
    var id = $('vgenModel').value;
    for (var i = 0; i < models.length; i++) {
      if (models[i].id === id) { $('vgenModelNote').textContent = models[i].note || ''; return; }
    }
  }

  // ---------- 参考图（图生视频） ----------
  function bindInit() {
    var file = $('vgenInitFile'), dz = $('vgenInitDrop');
    if (!file) return;
    function picked() {
      var f = (file.files || [])[0];
      if (!f) return;
      var fr = new FileReader();
      fr.onload = function () {
        initB64 = fr.result;
        var th = $('vgenInitThumb');
        if (th) { th.src = initB64; th.style.display = 'block'; }
        if ($('vgenInitClear')) $('vgenInitClear').style.display = '';
        if ($('vgenInitName')) $('vgenInitName').textContent = f.name;
      };
      fr.readAsDataURL(f);
    }
    file.onchange = picked;
    var cl = $('vgenInitClear');
    if (cl) cl.onclick = function () {
      initB64 = ''; file.value = '';
      if ($('vgenInitThumb')) { $('vgenInitThumb').style.display = 'none'; }
      cl.style.display = 'none';
      if ($('vgenInitName')) $('vgenInitName').textContent = '';
    };
    if (dz && !dz.dataset.wired) {
      dz.dataset.wired = '1';
      ['dragenter', 'dragover'].forEach(function (ev) {
        dz.addEventListener(ev, function (e) {
          e.preventDefault(); e.stopPropagation(); dz.classList.add('dragover');
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
        var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
        if (!f) return;
        try { var dt = new DataTransfer(); dt.items.add(f); file.files = dt.files; } catch (err) { return; }
        picked();
      });
    }
  }

  // ---------- 生成 ----------
  function poll() {
    if (!jobId) return;
    pollTries++;
    if (pollTries > 200) {
      jobId = null;
      if ($('vgenProg')) $('vgenProg').style.display = 'none';
      setStatus('等太久了，看看日志', 'err');
      return;
    }
    (AIWS.api ? AIWS.api('/api/vgen/progress?job=' + encodeURIComponent(jobId))
              : Promise.reject()).then(function (r) {
      var j = (r && r.job) || {};
      if ($('vgenBar')) $('vgenBar').style.width = (j.percent || 0) + '%';
      if ($('vgenText')) {
        $('vgenText').textContent = (j.stage || '排队中…') +
          (j.detail ? '　' + j.detail : '') + '　（' + (j.elapsed || 0) + ' 秒）';
      }
      if (j.logText && $('vgenLog')) $('vgenLog').textContent = j.logText;
      if (j.state === 'done') {
        jobId = null;
        if ($('vgenProg')) $('vgenProg').style.display = 'none';
        var res = (j.result || [])[0] || {};
        if ($('vgenResultCard')) $('vgenResultCard').style.display = 'block';
        var box = $('vgenResultBox');
        if (box) {
          box.innerHTML =
            '<div style="margin-bottom:8px"><b>✓ 出片了</b>　' +
            '<span class="muted">' + AIWS.esc(res.name || '') + '　' +
            (res.meta && res.meta.text ? AIWS.esc(res.meta.text) + '　' : '') +
            (res.seconds ? '用时 ' + res.seconds + ' 秒　' : '') +
            '<b style="color:#146c3f">花费 0 元</b></span></div>' +
            '<video src="/api/video/file?path=' + encodeURIComponent(res.file || '') +
            '" controls style="max-width:100%;border-radius:10px"></video>' +
            '<div class="row" style="margin-top:10px">' +
            '<button class="mini" onclick="window.AIWS.api(\'/api/openfile\',{file:' +
            JSON.stringify(res.file || '') + '})">▶ 用系统播放器打开</button>' +
            '<button class="mini" onclick="window.AIWS.api(\'/api/openfolder\',{dir:' +
            JSON.stringify((res.file || '').replace(/[\\/][^\\/]*$/, '')) + '})">📁 打开所在目录</button>' +
            '</div>';
        }
        setStatus('生成完成 ✓', 'ok');
        return;
      }
      if (j.state === 'error') {
        jobId = null;
        if ($('vgenProg')) $('vgenProg').style.display = 'none';
        setStatus('失败：' + (j.error || ''), 'err');
        return;
      }
      setTimeout(poll, 2500);
    }).catch(function () { setTimeout(poll, 3500); });
  }

  function go() {
    var prompt = ($('vgenPrompt') || {}).value || '';
    var tmpKey = ($('vgenKey') || {}).value || '';
    if (!prompt.trim() && !initB64) { setStatus('写点画面描述，或给一张图', 'err'); return; }
    if ($('vgenGo')) $('vgenGo').disabled = true;
    if ($('vgenResultCard')) $('vgenResultCard').style.display = 'none';
    if ($('vgenProg')) $('vgenProg').style.display = 'block';
    if ($('vgenBar')) $('vgenBar').style.width = '0%';
    setStatus('提交中…');
    AIWS.api('/api/vgen/generate', {
      prompt: prompt,
      model: ($('vgenModel') || {}).value || 'cogvideox-flash',
      size: ($('vgenSize') || {}).value || '',
      fps: ($('vgenFps') || {}).value || '',
      apiKey: tmpKey.trim(),
      imageUrl: initB64 || '',
      stripMark: !!(($('vgenStrip') || {}).checked),
      stripMode: 'crop-scale'
    }).then(function (r) {
      if (!r.ok) { setStatus('提交失败：' + (r.error || ''), 'err'); $('vgenGo').disabled = false; return; }
      jobId = r.job; pollTries = 0;
      setStatus('生成中…（免费模型约 60~90 秒）', 'warn');
      poll();
      setTimeout(function () { if ($('vgenGo')) $('vgenGo').disabled = false; }, 3000);
    }).catch(function (e) {
      setStatus('提交失败：' + e.message, 'err');
      if ($('vgenGo')) $('vgenGo').disabled = false;
      if ($('vgenProg')) $('vgenProg').style.display = 'none';
    });
  }

  function init() {
    bindInit();
    bindMaker();
    loadModels();
    if ($('vgenGo') && !$('vgenGo').dataset.wired) {
      $('vgenGo').dataset.wired = '1';
      $('vgenGo').onclick = go;
    }
    if ($('vgenRefresh')) $('vgenRefresh').onclick = loadModels;
    if ($('vgenOpenDir')) $('vgenOpenDir').onclick = function () {
      AIWS.api('/api/vgen/status').then(function (r) {
        AIWS.api('/api/openfolder', { dir: r.outDir });
      });
    };
  }

  AIWS.initVgenPanel = init;
  document.addEventListener('aiws:tab', function (e) {
    if (e.detail && e.detail.tab === 'vgen') { try { init(); } catch (err) {} }
  });
  document.addEventListener('aiws:ready', function () { setTimeout(function () { try { init(); } catch (e) {} }, 200); });
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { setTimeout(function () { try { init(); } catch (e) {} }, 200); });
  } else { setTimeout(function () { try { init(); } catch (e) {} }, 200); }

  // ---------- AI 生成提示词 ----------
  function ideaStatus(t, cls) {
    var el = $('vgenIdeaStatus');
    if (el) { el.textContent = t || ''; el.className = 'status' + (cls ? ' ' + cls : ''); }
  }

  function bindMaker() {
    var btn = $('vgenMake');
    if (!btn || btn.dataset.wired) return;
    btn.dataset.wired = '1';
    btn.onclick = function () {
      var idea = (($('vgenIdea') || {}).value || '').trim();
      if (!idea) { ideaStatus('先写一句你的想法，例：一只猫在窗台晒太阳', 'err'); return; }
      btn.disabled = true;
      ideaStatus('正在让 AI 写…（免费模型，几秒钟）', 'warn');
      var box = $('vgenIdeas');
      if (box) box.innerHTML = '';
      AIWS.api('/api/vgen/make-prompt', {
        idea: idea,
        style: ($('vgenStyle') || {}).value || '',
        seconds: '5',
        apiKey: (($('vgenKey') || {}).value || '').trim()
      }).then(function (r) {
        btn.disabled = false;
        if (!r.ok) { ideaStatus('失败：' + (r.error || ''), 'err'); return; }
        ideaStatus('好了 ✓ 点下面任意一条填进提示词框', 'ok');
        if (!box) return;
        box.innerHTML = (r.items || []).map(function (t, i) {
          return '<div class="needItem ok" data-t="' + i +
            '" style="cursor:pointer;margin-bottom:8px">' +
            '<span class="ic">' + (i + 1) + '</span>' +
            '<span class="nm" style="font-weight:600;line-height:1.55">' +
            AIWS.esc(t) + '</span></div>';
        }).join('');
        Array.prototype.forEach.call(box.querySelectorAll('[data-t]'), function (el) {
          el.onclick = function () {
            var t = (r.items || [])[parseInt(el.getAttribute('data-t'), 10)] || '';
            var p = $('vgenPrompt');
            if (p) { p.value = t; ideaStatus('已填入提示词 ✓ 可以再改', 'ok'); }
          };
        });
      }).catch(function (e) {
        btn.disabled = false;
        ideaStatus('失败：' + (e.message || e), 'err');
      });
    };
  }

})();