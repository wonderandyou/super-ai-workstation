/* ============================================================
   标签页：DeepSeek Harness（DSH）安装 / 配置 / 启动
   四步：① 拿 Key  ② 一键安装  ③ 填 Key  ④ 启动 + 检测在线
   ============================================================ */
(function () {
  'use strict';

  const $ = function (id) { return document.getElementById(id); };
  let st = null;
  let started = false;
  let progTimer = null;
  let onlineTimer = null;

  function setPill(el, kind, text) {
    if (!el) return;
    el.className = 'pill' + (kind ? ' ' + kind : '');
  }

  // ------------------------------------------------------------------
  //  状态
  // ------------------------------------------------------------------
  async function refresh(silent) {
    let r;
    try { r = await window.AIWS.api('/api/dsh/status'); }
    catch (e) {
      setPill($('dshPill'), 'down', '');
      $('dshPillText').textContent = '读取失败';
      return null;
    }
    st = r.status;

    // 顶部胶囊
    const map = {
      'online': ['up', 'DSH 服务在线 · 端口 ' + st.port],
      'ready': ['', '已就绪，等待启动'],
      'need-key': ['', '缺 API Key'],
      'need-dsh': ['', '缺 DSH 本体'],
      'need-node': ['down', '缺 Node.js']
    };
    const m = map[st.verdict] || ['', st.verdict];
    setPill($('dshPill'), m[0], '');
    $('dshPillText').textContent = m[1];

    // 右侧环境
    $('dshNode').textContent = st.node ? (st.nodeVersion || '已安装') : '未安装';
    $('dshNpm').textContent = st.npm ? (st.npmVersion || '已安装') : '未安装';
    $('dshBody').textContent = st.dshInstalled ? ('已安装 ' + (st.dshVersion || '')) : '未安装';
    $('dshLauncher').textContent = st.launcher ? '已就位' : (st.launcherInPkg ? '包内已有' : '没有');
    $('dshKeyState').textContent = st.apiKeySet ? st.apiKeyMask : '未设置';
    $('dshLnk').textContent = st.desktopLnk ? '已创建' : '没有';
    $('dshPort').textContent = st.port;
    $('dshPrefix').textContent = st.prefix || '（拿不到）';
    $('dshInstallerDir').textContent = st.installer ? st.installerDir : '★ 找不到捆绑的安装程序';
    const v = $('dshVerdict');
    v.textContent = st.reason || '';
    v.style.color = ({ online: '#1f7a4d', ready: '#1f7a4d' })[st.verdict] || '#9a6b00';

    $('dshKeyMask').textContent = st.apiKeySet ? st.apiKeyMask : '(未设置)';
    $('dshInstallBtn').disabled = !st.installer;

    // 第 4 步的在线状态
    renderOnline();
    return st;
  }

  function renderOnline() {
    if (!st) return;
    const pill = $('dshOnlinePill');
    const txt = $('dshOnlineText');
    if (st.online) {
      pill.className = 'pill up';
      txt.textContent = '✅ 后端服务在线';
    } else {
      pill.className = 'pill down';
      txt.textContent = '⛔ 后端服务不在线';
    }
    const rows = [
      ['服务地址', 'http://127.0.0.1:' + st.port],
      ['端口状态', st.online ? '监听中 · 有响应' : '无响应'],
      ['HTTP 状态', st.httpCode ? String(st.httpCode) + (st.httpCode === 401 || st.httpCode === 403
        ? '（正常：要授权令牌，说明服务活着）' : '  ✅ 已授权') : '—'],
      ['DSH 版本', st.dshVersion || '—'],
      ['API Key', st.apiKeySet ? st.apiKeyMask : '未设置'],
      ['桌面快捷方式', st.desktopLnk ? 'DSH Web.lnk 已就位' : '还没有']
    ];
    $('dshOnlineBox').innerHTML = rows.map(function (r) {
      return '<div class="kv"><span>' + r[0] + '</span><b>' + window.AIWS.esc(r[1]) + '</b></div>';
    }).join('');

    // 抓到授权链接就显示「打开 DSH 界面」
    const box = $('dshOpenBox');
    if (st.online && st.authUrl) {
      box.style.display = 'block';
      $('dshAuthUrl').textContent = st.authUrl;
    } else {
      box.style.display = 'none';
    }
  }

  // ------------------------------------------------------------------
  //  ③ 保存 API Key
  // ------------------------------------------------------------------
  async function saveKey() {
    const v = $('dshKeyInput').value.trim();
    const box = $('dshKeyStatus');
    if (!v) { box.textContent = '先粘贴 API Key'; box.className = 'status err'; return null; }
    if (v.indexOf('sk-') !== 0) {
      if (!confirm('这串不是 sk- 开头，确定要保存吗？')) return null;
    }
    try {
      const r = await window.AIWS.api('/api/dsh/apikey', { apikey: v });
      if (r.ok && r.saved && r.saved[0]) {
        box.textContent = '已保存到用户环境变量 DEEPSEEK_API_KEY ✓（重开 DSH 生效）';
        box.className = 'status ok';
        $('dshKeyInput').value = '';
        await refresh(true);
        return v;
      } else {
        box.textContent = '保存失败：' + ((r.saved && r.saved[1]) || '未知错误');
        box.className = 'status err';
      }
    } catch (e) {
      box.textContent = '保存失败：' + e.message;
      box.className = 'status err';
    }
    return null;
  }

  // ------------------------------------------------------------------
  //  ② 一键安装
  // ------------------------------------------------------------------
  async function doInstall() {
    if (!confirm('将运行捆绑的 DSH 安装程序：\n\n· 自动装 Node.js（便携版，不需要管理员）\n· 安装 DSH 本体\n· 创建桌面「DSH Web」快捷方式\n\n可能要几分钟，确定开始吗？')) return;
    const key = $('dshKeyInput').value.trim();
    $('dshInstallBtn').disabled = true;
    $('dshInstallProg').style.display = 'block';
    $('dshInstallStatus').textContent = '';
    $('dshInstallLog').textContent = '';
    try {
      const r = await window.AIWS.api('/api/dsh/install', { apikey: key });
      pollInstall(r.job);
    } catch (e) {
      $('dshInstallBtn').disabled = false;
      $('dshInstallStatus').textContent = '启动失败：' + e.message;
      $('dshInstallStatus').className = 'status err';
    }
  }

  function pollInstall(job) {
    if (progTimer) clearInterval(progTimer);
    progTimer = setInterval(async function () {
      let r;
      try { r = await window.AIWS.api('/api/dsh/progress?job=' + encodeURIComponent(job)); }
      catch (e) { return; }
      $('dshInstallBar').style.width = (r.percent || 0) + '%';
      $('dshInstallText').textContent = (r.stage || '') + '　·　已等 ' + r.elapsed + ' 秒';
      const lg = $('dshInstallLog');
      if (r.log) { lg.textContent = r.log.join('\n'); lg.scrollTop = lg.scrollHeight; }
      if (r.state === 'running') return;
      clearInterval(progTimer); progTimer = null;
      $('dshInstallBar').style.width = '100%';
      $('dshInstallBtn').disabled = false;
      const box = $('dshInstallStatus');
      if (r.state === 'done') {
        box.textContent = '安装完成 ✓ 现在可以到第 4 步「启动 DSH」了。';
        box.className = 'status ok';
      } else {
        box.textContent = '安装失败：' + (r.error || '未知错误') + '\n（详细输出见上方日志）';
        box.className = 'status err';
      }
      setTimeout(function () { $('dshInstallProg').style.display = 'none'; }, 1200);
      refresh(true);
    }, 1000);
  }

  // ------------------------------------------------------------------
  //  启动等待：转圈 + 计时器
  // ------------------------------------------------------------------
  let bootT0 = 0, bootTick = null, bootPoll = null;

  function fmtClock(s) {
    const m = Math.floor(s / 60), r = s % 60;
    return (m < 10 ? '0' : '') + m + ':' + (r < 10 ? '0' : '') + r;
  }

  function showBoot(title, hint) {
    const box = $('dshBootBox');
    box.style.display = 'flex';
    box.classList.remove('done');
    $('dshBootTitle').textContent = title || '正在启动 DSH…';
    if (hint) $('dshBootHint').innerHTML = hint;
    bootT0 = Date.now();
    if (bootTick) clearInterval(bootTick);
    bootTick = setInterval(function () {
      $('dshBootTimer').innerHTML = fmtClock(Math.floor((Date.now() - bootT0) / 1000)) +
        '<small>已等待</small>';
    }, 250);
  }

  function finishBoot(ok) {
    if (bootTick) { clearInterval(bootTick); bootTick = null; }
    if (bootPoll) { clearInterval(bootPoll); bootPoll = null; }
    const box = $('dshBootBox');
    if (!ok) { box.style.display = 'none'; return; }
    const secs = Math.floor((Date.now() - bootT0) / 1000);
    box.classList.add('done');
    $('dshBootTitle').textContent = 'DSH 已就绪！';
    $('dshBootTimer').innerHTML = fmtClock(secs) + '<small>总耗时</small>';
    $('dshBootHint').innerHTML = secs < 20
      ? '服务已在线。'
      : '服务已在线（DSH 首次启动较慢，之后就快了）。';
    setTimeout(function () { box.style.display = 'none'; }, 8000);
  }

  // ------------------------------------------------------------------
  //  ④ 启动 / 停止 + 在线检测
  // ------------------------------------------------------------------
  async function startDsh() {
    $('dshStartBtn').disabled = true;
    if (st && !st.apiKeySet) {
      if (!confirm('还没填 API Key，DSH 起来后也用不了模型。\n仍要启动吗？')) {
        $('dshStartBtn').disabled = false;
        return;
      }
    }
    showBoot('正在启动 DSH…（马上开始计时）');
    try {
      const r = await window.AIWS.api('/api/dsh/start', {});
      if (!r.ok) {
        finishBoot(false);
        $('dshStartBtn').disabled = false;
        alert(r.error || '启动失败');
        return;
      }
      if (r.already) {
        $('dshBootTitle').textContent = r.starting
          ? '检测到 DSH 正在启动中，继续等待…'
          : 'DSH 已经在运行，正在确认状态…';
      }
      // 最多等 10 分钟（DSH 首次要加载一堆插件）
      let n = 0;
      const MAX = 300;
      if (bootPoll) clearInterval(bootPoll);
      bootPoll = setInterval(async function () {
        n++;
        const s = await refresh(true);
        if (s && s.online) {
          $('dshStartBtn').disabled = false;
          finishBoot(true);
        } else if (n > MAX) {
          $('dshStartBtn').disabled = false;
          finishBoot(false);
          $('dshOnlineText').textContent = '启动超时（10 分钟）—— 看 data\\dsh-run.log';
        } else if (n === 40) {
          $('dshBootHint').innerHTML =
            '已经等了 80 秒。DSH 首次启动确实慢（要装/加载插件），<b>再等等</b>，别关。';
        } else if (n === 90) {
          $('dshBootHint').innerHTML =
            '已等 3 分钟。如果超过 5 分钟还没好，点「停止」再重试一次。';
        }
      }, 2000);
    } catch (e) {
      finishBoot(false);
      $('dshStartBtn').disabled = false;
      alert('启动失败：' + e.message);
    }
  }

  async function stopDsh() {
    if (!confirm('确定停止 DSH 服务吗？')) return;
    finishBoot(false);
    await window.AIWS.api('/api/dsh/stop', {});
    setTimeout(function () { refresh(true); }, 1500);
  }

  // ------------------------------------------------------------------
  //  初始化
  // ------------------------------------------------------------------
  function init() {
    if (started) return;
    started = true;

    $('dshInstallBtn').addEventListener('click', doInstall);
    $('dshKeySave').addEventListener('click', saveKey);
    $('dshKeyInput').addEventListener('keydown', function (e) { if (e.key === 'Enter') saveKey(); });
    $('dshStartBtn').addEventListener('click', startDsh);
    $('dshStopBtn').addEventListener('click', stopDsh);
    $('dshOpenBtn').addEventListener('click', async function () {
      try {
        const r = await window.AIWS.api('/api/dsh/open', {});
        if (!r.ok) { alert(r.error || '打不开'); return; }
        // ★ 后端已经用系统默认浏览器打开了（open_url），而且它不会被拦 ✗
        //   这里再 window.open 会：① 被浏览器当弹窗拦掉（看起来像"没反应"）
        //                      ② 侥幸没被拦时开两个标签页
        //   所以只在后端没开成功（opened === false）时才补一次。
        if (r.opened === false) {
          const w = window.open(r.url, '_blank');
          if (!w) {
            // 被拦了 —— 给用户一条能自己点的链接
            const box = document.getElementById('dshOnlineText');
            if (box) {
              box.innerHTML = '浏览器拦了弹窗，<a href="' + r.url +
                '" target="_blank"><b>点这里打开 DSH</b></a>';
            }
          }
        }
      } catch (e) { alert('打不开：' + e.message); }
    });
    $('dshRecheck').addEventListener('click', function () { refresh(false); });
    // 退路：装不上网页端时，去官网下桌面端（阉割版）
    if ($('dshDesktopBtn')) {
      $('dshDesktopBtn').addEventListener('click', function () {
        window.open('https://www.deepseek.com/download', '_blank');
      });
    }
    $('dshOpenPlatform').addEventListener('click', function () { window.open('https://platform.deepseek.com/', '_blank'); });
    $('dshOpenKeys').addEventListener('click', function () { window.open('https://platform.deepseek.com/api_keys', '_blank'); });
    $('dshOpenLog').addEventListener('click', async function () {
      // ★ 不能写死路径（别人电脑上盘符/目录都不同）——
      //   让后端用它自己的 data 目录
      const r = await window.AIWS.api('/api/dsh/openlog', {});
      if (!r.ok) alert(r.error || '打不开');
    });

    refresh();

    document.addEventListener('aiws:tab', function (e) {
      if (e.detail.tab === 'dsh') refresh(true);
    });
  }

  document.addEventListener('aiws:ready', init);
})();
