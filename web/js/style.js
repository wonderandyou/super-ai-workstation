// 参考图风格提取（豆包生图 / 本地千问生图 共用）
//   上传图 → 调 DeepSeek 视觉（实测 deepseek-chat 支持图片 ✓）→ 8 个模块关键词
//   → 可一键填入提示词框 / 存进本地关键词库 ✓
(function () {
  // ★ 别缓存 AIWS！app.js 用 const 声明、到文件末尾才 window.AIWS = AIWS，
  //   在这里缓存会永久绑到一个空壳上 → (window.AIWS || {}).api is not a function ✗
  //   所以下面一律用 (window.AIWS || {}) 实时取 ✓
  window.AIWS = window.AIWS || {};

  var MODULES = [
    ['style', '画风'], ['subject', '主体'], ['palette', '配色'], ['light', '光影'],
    ['compo', '构图'], ['mood', '氛围'], ['texture', '质感'], ['detail', '细节']
  ];

  function $(id) { return document.getElementById(id); }

  // 每个面板一个实例：P=前缀, promptId=目标提示词框
  function makePanel(P, promptId) {
    var files = [];        // 选中的图片（File 对象）
    var last = null;       // 最近一次识别结果
    var jobId = null;
    var pollTries = 0;

    function setStatus(msg, cls) {
      var el = $(P + 'StyleStatus');
      if (!el) return;
      el.textContent = msg || '';
      el.className = 'status' + (cls ? ' ' + cls : '');
    }

    function renderMods(mods) {
      var box = $(P + 'StyleMods');
      if (!box) return;
      if (!mods) { box.innerHTML = ''; return; }
      box.innerHTML = MODULES.map(function (m) {
        var v = (mods[m[0]] && mods[m[0]].text) || '';
        return '<label class="f" style="margin-top:8px">' + m[1] +
          '<input type="text" data-m="' + m[0] + '" value="' + (window.AIWS || {}).esc(v) + '"></label>';
      }).join('');
    }

    function collectMods() {
      var box = $(P + 'StyleMods');
      if (!box) return null;
      var out = {};
      var any = false;
      box.querySelectorAll('input[data-m]').forEach(function (inp) {
        var t = (inp.value || '').trim();
        if (t) any = true;
        out[inp.dataset.m] = { label: '', text: t };
      });
      return any ? out : null;
    }

    function promptText(mods) {
      var parts = [];
      MODULES.forEach(function (m) {
        var o = (mods || {})[m[0]];
        var v = (o && (o.text || o)) || '';
        if (String(v).trim()) parts.push(m[1] + '：' + String(v).trim());
      });
      return parts.join('，');
    }

    function refreshLib() {
      // ★ 初始化那一刻 (window.AIWS || {}).api 可能还没就绪（脚本时机），
      //   直接调会同步抛 TypeError，把整个 makePanel 打断 ✗ → 先挡住 ✓
      if (!(window.AIWS || {}).api) { setTimeout(refreshLib, 300); return; }
      (window.AIWS || {}).api('/api/style/lib').then(function (r) {
        var box = $(P + 'StyleLib');
        if (!box) return;
        var items = (r && r.items) || [];
        if (!items.length) {
          box.innerHTML = '<div class="muted" style="font-size:12px">（还没有存过关键词）</div>';
          return;
        }
        box.innerHTML = items.map(function (it, i) {
          return '<div class="mdRow" style="padding:6px 0"><div class="mdHead">' +
            '<b style="font-size:12px">' + (window.AIWS || {}).esc(it.title || ('关键词 ' + (i + 1))) + '</b>' +
            '<span class="mdTag">' + (window.AIWS || {}).esc(it.time || '') + '</span>' +
            '<button class="mini" data-use="' + i + '">填入</button>' +
            '<button class="mini" data-del="' + i + '">删</button></div>' +
            '<div class="mdNote" style="font-size:11px">' + (window.AIWS || {}).esc(it.prompt || '').slice(0, 200) + '</div></div>';
        }).join('');
        box.querySelectorAll('button[data-use]').forEach(function (b) {
          b.onclick = function () {
            var it = items[+b.dataset.use];
            var box2 = $(promptId);
            if (box2 && it) { box2.value = it.prompt || ''; setStatus('已填入提示词 ✓', 'ok'); }
          };
        });
        box.querySelectorAll('button[data-del]').forEach(function (b) {
          b.onclick = function () {
            (window.AIWS || {}).api('/api/style/delete', { index: +b.dataset.del }).then(function () {
              refreshLib(); setStatus('已删除', '');
            });
          };
        });
      }).catch(function () {});
    }

    function poll() {
      if (!jobId) return;
      pollTries++;
      if (pollTries > 150) {            // ≈5 分钟还没完 → 明确报错，别无限转 ✓
        jobId = null;
        if ($(P + 'StyleProg')) $(P + 'StyleProg').style.display = 'none';
        setStatus('等太久了（约 5 分钟）—— 检查「设置」里的 DeepSeek API Key 是否有效', 'err');
        return;
      }
      (window.AIWS || {}).api('/api/style/progress?job=' + encodeURIComponent(jobId)).then(function (r) {
        // ★ 接口万一包了一层 data 也能取到（踩过：jobId 取不到 → 一直「准备中」✗）
        var j = (r && (r.job || r)) || {};
        var bar = $(P + 'StyleBar'), txt = $(P + 'StyleText');
        if (bar) bar.style.width = (j.percent || 0) + '%';
        if (txt) txt.textContent = (j.stage || '排队中…') + (j.detail ? '　' + j.detail : '') +
          '　（' + (j.elapsed || 0) + ' 秒）';
        if (j.state === 'done') {
          jobId = null;
          if ($(P + 'StyleProg')) $(P + 'StyleProg').style.display = 'none';
          last = j.result || null;
          if (last && last.modules) {
            renderMods(last.modules);
            var f = $(P + 'StyleFill'), s = $(P + 'StyleSave');
            if (f) f.disabled = false;
            if (s) s.disabled = false;
            // ★ 识别完自动填进上方提示词框 ✓（主人要求）
            var filled = doFill(true);
            setStatus(filled
              ? '识别完成 ✓ 关键词已自动填入上方提示词框，可以随手改，或存进关键词库'
              : '识别完成 ✓ 可以改词、点「填入提示词」，或存进关键词库', 'ok');
          } else {
            setStatus('完成了但没拿到结果（看看日志）', 'err');
          }
          return;
        }
        if (j.state === 'failed') {
          jobId = null;
          if ($(P + 'StyleProg')) $(P + 'StyleProg').style.display = 'none';
          setStatus('识别失败：' + (j.detail || ''), 'err');
          return;
        }
        setTimeout(poll, 2000);
      }).catch(function () { setTimeout(poll, 3000); });
    }

    var pick = $(P + 'StylePick'), file = $(P + 'StyleFile');

    // 选中之后：更新预览缩略图 + 允许开始识别 ✓
    function onPicked() {
      files = Array.prototype.slice.call(file.files || []);
      var th = $(P + 'StyleThumb');
      if (th) th.textContent = files.length ? ('已选 ' + files.length + ' 张：' +
        files.map(function (f) { return f.name; }).join('、').slice(0, 46)) : '';
      var box = $(P + 'StylePrev');
      if (box) {
        box.innerHTML = '';
        files.forEach(function (f) {
          try {
            var img = document.createElement('img');
            img.src = URL.createObjectURL(f);
            img.title = f.name;
            img.style.cssText = 'width:86px;height:86px;object-fit:cover;border-radius:9px;' +
              'border:1px solid rgba(127,127,127,.35);box-shadow:0 2px 8px rgba(0,0,0,.12)';
            box.appendChild(img);
          } catch (err) {}
        });
      }
      var run = $(P + 'StyleRun');
      if (run) run.disabled = !files.length;
      setStatus(files.length ? ('已选 ' + files.length + ' 张，点「开始识别」') : '');
    }

    if (pick && file) {
      pick.onclick = function () { file.click(); };
      file.onchange = onPicked;
    }

    // ★ 拖入区：和溶图一模一样的做法（拖拽高亮 + 放下取文件 + 点击选图）✓
    var dz = $(P + 'StyleDrop');
    if (dz && file && !dz.dataset.wired) {
      dz.dataset.wired = '1';
      dz.addEventListener('click', function (e) { e.preventDefault(); file.click(); });
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
        var fs = (e.dataTransfer && e.dataTransfer.files) || [];
        if (!fs.length) { setStatus('没识别到文件 —— 从文件夹直接拖进来最稳', 'err'); return; }
        var good = [];
        for (var i = 0; i < fs.length; i++) {
          if ((fs[i].type || '').indexOf('image/') !== 0) {
            setStatus('「' + fs[i].name + '」不是图片', 'err'); return;
          }
          good.push(fs[i]);
        }
        try {
          var dt = new DataTransfer();
          good.slice(0, 8).forEach(function (f) { dt.items.add(f); });
          file.files = dt.files;
        } catch (err) {
          setStatus('这个浏览器不支持拖拽赋值，请点一下拖入区选文件', 'err');
          return;
        }
        onPicked();
      });
    }

    var run = $(P + 'StyleRun');
    if (run) run.onclick = function () {
      if (!files.length) { setStatus('先选图片', 'err'); return; }
      setStatus('正在读图片…');
      var imgs = [];
      var n = files.length;
      files.forEach(function (f, i) {
        var fr = new FileReader();
        fr.onload = function () {
          imgs[i] = fr.result;
          if (imgs.filter(Boolean).length === n) {
            if ($(P + 'StyleProg')) $(P + 'StyleProg').style.display = 'block';
            (window.AIWS || {}).api('/api/style/analyze', { images: imgs }).then(function (r) {
              if (!r.ok) { setStatus('启动失败：' + (r.error || ''), 'err'); return; }
              // ★ 宽容取任务号：拿不到就说清楚，别让进度条干等 ✗
              jobId = r.job || (r.data && r.data.job) || '';
              if (!jobId) {
                if ($(P + 'StyleProg')) $(P + 'StyleProg').style.display = 'none';
                setStatus('没拿到任务号，接口返回：' + JSON.stringify(r).slice(0, 180), 'err');
                return;
              }
              pollTries = 0;
              setStatus('识别中…（DeepSeek 视觉，一般 10~30 秒）', 'warn');
              poll();
            }).catch(function (e) { setStatus('启动失败：' + e.message, 'err'); });
          }
        };
        fr.readAsDataURL(f);
      });
    };

    // ★ 填入提示词：抽成函数，识别完成后会自动调一次（silent=不覆盖状态栏）✓
    function doFill(silent) {
      var mods = collectMods();
      if (!mods) { if (!silent) setStatus('还没有关键词', 'err'); return false; }
      var t = promptText(mods);
      var box = $(promptId);
      if (!box) return false;
      if (box.value && box.value.indexOf(t.slice(0, 12)) >= 0) return true;   // 填过别重复 ✗
      box.value = box.value ? (box.value.replace(/\s*$/, '') + '，' + t) : t;
      try { box.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
      if (!silent) setStatus('已填入提示词 ✓（在页面上方）', 'ok');
      return true;
    }
    var fill = $(P + 'StyleFill');
    if (fill) fill.onclick = function () { doFill(false); };

    var save = $(P + 'StyleSave');
    if (save) save.onclick = async function () {
      var mods = collectMods();
      if (!mods) { setStatus('还没有关键词', 'err'); return; }
      var t = promptText(mods);
      // ★ 原来这里用原生 window.prompt ✗（顶着「127.0.0.1:8200 显示」很丑 ✓）
      //   换成应用自绘弹窗 ✓（2026-10-03 主人："把所有类似的通知转变为弹窗" ✓）
      var title = (await AIWS.input('给这套风格起个名字（可留空）', '')) || '';
      (window.AIWS || {}).api('/api/style/save', { modules: mods, prompt: t, title: title }).then(function () {
        setStatus('已存进关键词库 ✓（展开下面能点「填入」复用）', 'ok');
        refreshLib();
      });
    };

    var card = $(P + 'StyleCard');
    if (card || pick) refreshLib();
    return { refreshLib: refreshLib };
  }

  function initPanels() {
    // ★ 各自包 try：一个面板出问题，另一个照样能用 ✓
    //   （踩过：db 的 refreshLib 抛错 → qw 永远初始化不到 → 只有豆包页能点 ✗）
    try { makePanel('db', 'prompt'); } catch (err) {}
    try { makePanel('qw', 'qPrompt'); } catch (err) {}
  }

  // ★★ 真凶（2026-10-01，诊断页实测确认）：
  //   style.js 在 app.js **之前**加载 → 先把方法挂到自建的 `window.AIWS = {}` 上；
  //   随后 app.js 末尾执行 `window.AIWS = AIWS`（换成一个**新对象**）✗
  //   → 那个属性连同旧对象一起被丢弃 → `AIWS.initStylePanels` 变 undefined ✓
  //   → 之后每一次 initStylePanels() 都 TypeError，被 try 吞掉 → 拖入区永远绑不上
  //     （实测 dropWired=null，而按钮因为用了内联 onclick 所以还在 ✓）
  //   所以改成 attach()：谁拿到 window.AIWS，就把方法**重新挂一遍** ✓
  function attach(runNow) {
    var A = window.AIWS || (window.AIWS = {});
    A.initStylePanels = initPanels;
    if (runNow) { try { initPanels(); } catch (e) {} }
    return A;
  }
  attach(true);

  function boot() { attach(true); }

  document.addEventListener('aiws:tab', function (e) {
    var t = e.detail && e.detail.tab;
    if (t === 'doubao' || t === 'qwen') boot();
  });

  // ★ 兜底初始化（2026-10-01 修「选图片按钮没效果」）
  //   原来只监听 aiws:tab，而那个事件**只在"切换标签"时**才发 ✗
  //   如果打开页面时默认就停在豆包/千问页，事件不发 → 按钮没绑 onclick
  //   → 表现就是「点选图片没反应」✓ 所以这里补三条兜底路径：
  //     ① 页面 DOM 就绪        ② app.js 发 aiws:ready        ③ hash 直接命中
  document.addEventListener('aiws:ready', function () { setTimeout(boot, 150); });
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { setTimeout(boot, 150); });
  } else {
    setTimeout(boot, 150);
  }
  if (location.hash === '#doubao' || location.hash === '#qwen') setTimeout(boot, 400);
})();
