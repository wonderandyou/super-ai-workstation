/* AI 搜索引擎 —— 工作站的「🔍 AI 搜索引擎」标签页
   ------------------------------------------------------------------
   后端：/api/search/status | run | progress
   搜索走 **DeepSeek 官方 Anthropic 兼容接口的原生 web 搜索**
   （https://api.deepseek.com/anthropic/v1/messages + web_search_20250305 工具），
   和 DSH 自己联网用的是同一条通道；来源只从结构化搜索结果块里取。
*/
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var timer = null;

  function setStatus(m, k) {
    var e = $('srStatus');
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

  function md(t) {
    var h = esc(t);
    h = h.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
    h = h.replace(/`([^`]+)`/g, '<code>$1</code>');
    h = h.replace(/\[(\d+)\]/g, '<sup class="srRef">[$1]</sup>');
    return h.replace(/\n/g, '<br>');
  }

  async function loadStatus() {
    try {
      var s = await api('/api/search/status');
      var pill = $('srPill');
      if (!s.ok) {
        if (pill) { pill.textContent = '● 不可用'; pill.style.background = '#fdecec'; pill.style.color = '#a32020'; }
        setStatus(s.error || '后端没起来', 'err');
        return;
      }
      if (!s.hasKey) {
        if (pill) { pill.textContent = '● 缺 Key'; pill.style.background = '#fdf3e6'; pill.style.color = '#8a5a00'; }
        setStatus('还没填 DeepSeek API Key —— 去「⚙️ 设置」填一把就能搜。', 'err');
      } else {
        if (pill) { pill.textContent = '● 就绪'; pill.style.background = '#e8f7ee'; pill.style.color = '#177b3f'; }
        setStatus('Key ' + (s.keyMask || '') + '　·　模型 ' + s.model +
          '　·　每次最多搜 ' + s.maxUses + ' 轮', 'ok');
      }
    } catch (e) { setStatus('读取失败：' + e.message, 'err'); }
  }

  function renderResult(r) {
    $('srResultCard').style.display = 'block';
    $('srQ').textContent = r.query || '';
    var tc = r.tierCount || {};
    $('srMeta').textContent = '用时 ' + (r.seconds == null ? '?' : r.seconds) + ' 秒　·　模型 ' +
      (r.model || '?') + '　·　token 输入 ' + (((r.usage || {}).in) == null ? '?' : r.usage.in) +
      ' / 输出 ' + (((r.usage || {}).out) == null ? '?' : r.usage.out) +
      '　·　来源 ' + (r.sourceCount || 0) + ' 条（权威 ' + (tc.official || 0) +
      ' / 社区 ' + (tc.community || 0) + ' / 其他 ' + (tc.other || 0) + '，已按权威度排序）';

    var pts = (r.points && r.points.length) ? r.points : (r.answer ? [r.answer] : []);
    $('srPoints').innerHTML = pts.length ? pts.map(function (p, i) {
      return '<div class="srPoint"><span class="srIdx">' + (i + 1) + '</span>' +
        '<div class="srPTxt">' + md(p) + '</div></div>';
    }).join('') : '<p class="muted">（这次没拿到论述内容）</p>';

    var src = r.sources || [];
    $('srSrcBox').style.display = src.length ? 'block' : 'none';
    $('srSources').innerHTML = src.map(function (s) {
      var tag = s.tier === 0 ? '<span class="srTier t0">权威</span>'
              : (s.tier === 1 ? '<span class="srTier t1">社区</span>' : '');
      return '<div class="srSrc">' +
        '<div class="srSrcHead"><span class="srNo">' + s.n + '</span>' + tag +
        '<a href="' + esc(s.url) + '" target="_blank" rel="noopener">' + esc(s.title) + '</a></div>' +
        '<div class="srSrcMeta">' + esc(s.site) + (s.date ? '　·　' + esc(s.date) : '') + '</div>' +
        (s.snippet ? '<div class="srSrcSnip">' + esc(s.snippet) + '</div>' : '') +
        '</div>';
    }).join('');

    $('srRefBox').style.display = src.length ? 'block' : 'none';
    $('srRefs').innerHTML = src.map(function (s) {
      return '<li><span class="srNo">' + s.n + '</span>　<a href="' + esc(s.url) +
        '" target="_blank" rel="noopener">' + esc(s.url) + '</a></li>';
    }).join('');
  }

  async function go() {
    var q = ($('srInput').value || '').trim();
    if (!q) { setStatus('先说你想搜什么', 'err'); $('srInput').focus(); return; }
    $('srGo').disabled = true;
    $('srResultCard').style.display = 'none';
    $('srProg').style.display = 'block';
    $('srBar').style.width = '0%';
    $('srLog').textContent = '';
    setStatus('');
    try {
      var r = await api('/api/search/run', { query: q });
      if (!r.ok) {
        setStatus(r.error || '提交失败', 'err');
        $('srGo').disabled = false; $('srProg').style.display = 'none';
        return;
      }
      poll(r.job);
    } catch (e) {
      setStatus('失败：' + e.message, 'err');
      $('srGo').disabled = false;
    }
  }

  function poll(jid) {
    if (timer) clearInterval(timer);
    timer = setInterval(function () {
      api('/api/search/progress?job=' + jid).then(function (q) {
        if (!q.ok) return;
        $('srBar').style.width = (q.percent || 0) + '%';
        $('srProgText').textContent = (q.stage || '') + (q.detail ? '　·　' + q.detail : '') +
          '　·　已等 ' + q.elapsed + ' 秒';
        $('srLog').textContent = (q.log || []).join('\n');
        $('srLog').scrollTop = $('srLog').scrollHeight;
        if (q.state === 'running') return;
        clearInterval(timer);
        $('srGo').disabled = false;
        $('srProg').style.display = 'none';
        if (q.state === 'done') {
          renderResult(q.result || {});
          setStatus('✓ 搜完了：拿到 ' + ((q.result || {}).sourceCount || 0) + ' 个来源', 'ok');
        } else {
          setStatus('失败：' + (q.error || ''), 'err');
        }
      }).catch(function () {});
    }, 1500);
  }

  function init() {
    if (!document.body.contains($('srGo')) || $('srGo').dataset.wired) return;
    $('srGo').dataset.wired = '1';
    $('srGo').onclick = go;
    $('srInput').addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); go(); }
    });
    $('srClear').onclick = function () {
      $('srInput').value = '';
      $('srResultCard').style.display = 'none';
      setStatus('');
      $('srInput').focus();
    };
    $('srGoSetting').onclick = function () {
      if (window.AIWS && window.AIWS.activate) window.AIWS.activate('settings');
      else location.hash = '#settings';
    };
    loadStatus();
  }

  document.addEventListener('aiws:tab', function (e) {
    if (e.detail && e.detail.tab === 'search') init();
  });
  if (location.hash === '#search') setTimeout(init, 300);
})();
