/* ============================================================
   标签页：电脑工具百宝箱
     工具 1 · 校园网一键登录（深澜 Srun 认证）
     工具 2 · 内存释放（Windows 标准 API，前后对比实测）
     工具 3 · 多线程自定义下载器（HTTP Range 分块并发）
   ============================================================ */
(function () {
  'use strict';

  const $ = function (id) { return document.getElementById(id); };
  let started = false;
  let memTick = null, memT0 = 0;
  let dlTimer = null;
  let probed = null;
  let curJob = null;

  function human(n) {
    n = Number(n) || 0;
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n.toFixed(0) : n.toFixed(2)) + ' ' + u[i];
  }

  function fmtClock(s) {
    const m = Math.floor(s / 60), r = s % 60;
    return (m < 10 ? '0' : '') + m + ':' + (r < 10 ? '0' : '') + r;
  }

  // ------------------------------------------------------------------
  //  工具 1：校园网一键登录
  // ------------------------------------------------------------------
  let cpTick = null, cpT0 = 0;

  function cpClock(s) {
    const m = Math.floor(s / 60), r = s % 60;
    return (m < 10 ? '0' : '') + m + ':' + (r < 10 ? '0' : '') + r;
  }

  function cpBusy(on, title) {
    const box = $('cpBusy');
    if (!on) {
      if (cpTick) { clearInterval(cpTick); cpTick = null; }
      box.style.display = 'none';
      return;
    }
    box.style.display = 'flex';
    box.classList.remove('done');
    if (title) $('cpBusyTitle').textContent = title;
    cpT0 = Date.now();
    if (cpTick) clearInterval(cpTick);
    cpTick = setInterval(function () {
      $('cpTimer').innerHTML = cpClock(Math.floor((Date.now() - cpT0) / 1000)) + '<small>已用时</small>';
    }, 250);
  }

  async function cpLoad() {
    try {
      const r = await window.AIWS.api('/api/campus/status');
      if (!r.ok) { $('cpPillText').textContent = r.error || '读取失败'; return; }
      const s = r.status;
      $('cpIp').textContent = s.ip || '（取不到）';
      $('cpPortal').textContent = s.portal + '　ac_id=' + s.acId;
      $('cpUser').textContent = s.configured ? s.fullUser : '（还没设置）';
      $('cpPortalState').textContent = s.portalState || '—';
      $('cpInternet').innerHTML = s.internet
        ? '<span style="color:#1f7a4d">✅ 能上网</span>'
        : '<span style="color:#9c2f26">❌ 不通（需要认证）</span>';

      const pill = $('cpPill'), txt = $('cpPillText');
      if (!s.configured) { pill.className = 'pill down'; txt.textContent = '需要设置账号'; $('cpCfgBox').style.display = 'block'; }
      else if (s.internet) { pill.className = 'pill up'; txt.textContent = '在线'; }
      else { pill.className = 'pill'; txt.textContent = '未认证'; }

      // 填充设置表单
      if (s.domains && !$('cpDomainIn').childElementCount) {
        $('cpDomainIn').innerHTML = s.domains.map(function (d) {
          return '<option value="' + d.v + '">' + d.t + '（' + d.v + '）</option>';
        }).join('');
      }
      $('cpUserIn').value = $('cpUserIn').value || s.user || '';
      $('cpPortalIn').value = $('cpPortalIn').value || s.portal || '';
      if (s.domain) $('cpDomainIn').value = s.domain;
    } catch (e) {
      $('cpPillText').textContent = '读取失败';
    }
  }

  async function cpLogin(force) {
    $('cpBtn').disabled = true;
    $('cpForce').disabled = true;
    $('cpStatus').textContent = '';
    $('cpStatus').className = 'status';
    cpBusy(true, force ? '正在强制重新登录…' : '正在登录校园网…');
    try {
      const r = await window.AIWS.api('/api/campus/login', { force: !!force });
      const res = r.result || {};
      cpBusy(false);
      $('cpLog').textContent = (res.steps || []).join('\n');
      if (res.ok) {
        $('cpStatus').textContent = res.message + (res.internetBefore ? '（本来就在线）' : ' ✓');
        $('cpStatus').className = 'status ok';
      } else {
        $('cpStatus').textContent = res.message || '登录失败';
        $('cpStatus').className = 'status err';
      }
      await cpLoad();
    } catch (e) {
      cpBusy(false);
      $('cpStatus').textContent = '失败：' + e.message;
      $('cpStatus').className = 'status err';
    } finally {
      $('cpBtn').disabled = false;
      $('cpForce').disabled = false;
    }
  }

  // ------------------------------------------------------------------
  //  开机自启
  // ------------------------------------------------------------------
  let autoOn = false;

  async function cpAutoLoad() {
    try {
      const r = await window.AIWS.api('/api/campus/autostart', { action: 'status' });
      if (!r.ok) { $('cpAutoPillText').textContent = '读取失败'; return; }
      const s = r.status;
      autoOn = !!s.installed;
      const pill = $('cpAutoPill'), txt = $('cpAutoPillText');
      if (autoOn) {
        pill.className = 'pill up';
        txt.textContent = '已开启（' + (s.state || '') + '）';
        $('cpAutoBtn').textContent = '关闭';
        $('cpAutoBtn').className = 'mini';
        $('cpAutoDesc').innerHTML =
          '已设为<b>登录 Windows 后 30 秒</b>自动登录，之后<b>每 5 分钟巡查</b>断网自动重连。<br>' +
          '<span style="font-size:12px">执行：' + window.AIWS.esc(s.action || '') + '</span>';
      } else {
        pill.className = 'pill';
        txt.textContent = '未开启';
        $('cpAutoBtn').textContent = '开启';
        $('cpAutoBtn').className = 'primary small';
        $('cpAutoDesc').innerHTML =
          '登录 Windows 后 <b>30 秒</b>自动登录校园网，之后<b>每 5 分钟巡查一次</b>' +
          '（断网自动重连）。用计划任务实现，跑的是无窗口的 pythonw，<b>不会闪黑框</b>。';
      }
      $('cpOldTask').style.display = s.oldTaskExists ? 'block' : 'none';
      if (s.oldTaskExists) autoOnOld = true;
    } catch (e) {
      $('cpAutoPillText').textContent = '读取失败';
    }
  }

  let autoOnOld = false;

  async function cpAutoToggle() {
    const want = !autoOn;
    if (want && !confirm('开启开机自启？\n\n会创建一个计划任务：\n' +
        '· 登录 Windows 后 30 秒自动登录校园网\n' +
        '· 之后每 5 分钟巡查一次，断网自动重连\n' +
        '· 用 pythonw 运行，不弹窗口\n\n' +
        '（任务只在本机，随时可以关掉）')) return;
    $('cpAutoBtn').disabled = true;
    $('cpAutoStatus').textContent = want ? '正在创建计划任务…' : '正在删除计划任务…';
    $('cpAutoStatus').className = 'status';
    try {
      const r = await window.AIWS.api('/api/campus/autostart',
        { action: want ? 'on' : 'off' });
      if (r.ok) {
        $('cpAutoStatus').textContent = r.message + ' ✓';
        $('cpAutoStatus').className = 'status ok';
      } else {
        $('cpAutoStatus').textContent = '失败：' + (r.error || '未知错误') +
          '\n（如果提示拒绝访问，可能是权限问题，试试以管理员身份启动工作站）';
        $('cpAutoStatus').className = 'status err';
      }
      await cpAutoLoad();
    } catch (e) {
      $('cpAutoStatus').textContent = '失败：' + e.message;
      $('cpAutoStatus').className = 'status err';
    } finally {
      $('cpAutoBtn').disabled = false;
    }
  }

  async function cpOldDel() {
    if (!confirm('删除旧的「校园网自动登录-巡查」任务？\n\n（旧脚本文件不会被删，只是不再自动跑）')) return;
    $('cpOldDel').disabled = true;
    try {
      const r = await window.AIWS.api('/api/campus/autostart', { action: 'removeOld' });
      $('cpAutoStatus').textContent = (r.ok ? r.message : ('失败：' + r.error)) + (r.ok ? ' ✓' : '');
      $('cpAutoStatus').className = 'status ' + (r.ok ? 'ok' : 'err');
      await cpAutoLoad();
    } finally {
      $('cpOldDel').disabled = false;
    }
  }

  // ------------------------------------------------------------------
  //  内存
  // ------------------------------------------------------------------
  async function loadMemory() {
    try {
      const r = await window.AIWS.api('/api/toolbox/memory');
      if (!r.ok) { $('tbPillText').textContent = r.error || '读取失败'; return; }
      renderMemory(r.memory);
      $('tbIsAdmin').textContent = r.isAdmin ? '✓ 有（可深度清理）' : '✗ 没有（深度清理会跳过）';
      $('tbPill').className = 'pill up';
      $('tbPillText').textContent = '可用 ' + r.memory.availableText + ' / ' + r.memory.totalText;
    } catch (e) {
      $('tbPillText').textContent = '读取失败';
    }
  }

  function renderMemory(m) {
    const pct = m.total ? (100 * m.used / m.total) : 0;
    const bar = $('tbMemBar');
    bar.style.width = pct.toFixed(1) + '%';
    bar.className = 'memBar' + (pct >= 90 ? ' hot' : (pct >= 75 ? ' warn' : ''));
    $('tbMemTotal').textContent = m.totalText;
    $('tbMemUsed').textContent = m.usedText;
    $('tbMemAvail').textContent = m.availableText;
    $('tbMemLoad').textContent = m.loadPercent + '%';
  }

  async function releaseMemory() {
    $('tbMemBtn').disabled = true;
    $('tbMemResult').style.display = 'none';
    $('tbMemBusy').style.display = 'flex';
    memT0 = Date.now();
    if (memTick) clearInterval(memTick);
    memTick = setInterval(function () {
      $('tbMemTimer').innerHTML = fmtClock(Math.floor((Date.now() - memT0) / 1000)) + '<small>已用时</small>';
    }, 200);

    try {
      const r = await window.AIWS.api('/api/toolbox/memory/release',
        { deep: $('tbMemDeep').checked });
      const x = r.result;
      if (memTick) { clearInterval(memTick); memTick = null; }
      $('tbMemBusy').style.display = 'none';
      $('tbMemResult').style.display = 'block';

      // ★ 后端降级时会带 error —— 明确说出来，别装作成功 ✗
      if (x.error) {
        window.AIWS.toast && window.AIWS.toast('内存释放出错：' + x.error, 'err');
        $('tbMemResultBox').innerHTML =
          '<div style="grid-column:1/-1"><div class="muted" style="font-weight:700">本次释放失败</div>' +
          '<div style="color:#b3261e;font-weight:700;margin-top:4px">' +
          window.AIWS.esc(String(x.error)) + '</div>' +
          '<div class="muted" style="font-size:12.5px;margin-top:6px">' +
          '检查（可直接重试；深度清理需要管理员权限）：' +
          'toolbox.py 的 human() 必须在提权子进程入口之前定义 ✓</div></div>';
        $('tbMemSteps').innerHTML = '';
        return;
      }
      const freedHot = x.freed > 0;
      $('tbMemResultBox').innerHTML =
        '<div><div class="muted" style="font-size:12.5px;font-weight:700">释放前可用</div>' +
        '<div style="font-size:20px;font-weight:800">' + x.beforeText + '</div></div>' +
        '<div class="arrow">➜</div>' +
        '<div><div class="muted" style="font-size:12.5px;font-weight:700">释放后可用</div>' +
        '<div class="big">' + x.afterText + '</div></div>' +
        '<div class="arrow">＝</div>' +
        '<div><div class="muted" style="font-size:12.5px;font-weight:700">本次释放</div>' +
        '<div class="big" style="color:' + (freedHot ? '#146c3f' : '#9a6b00') + '">' +
        (freedHot ? '+' : '') + x.freedText + '</div></div>' +
        '<div class="muted" style="font-size:13px">用时 ' + x.elapsed + ' 秒　' +
        '占用率 ' + x.loadBefore + '% → ' + x.loadAfter + '%</div>';

      $('tbMemSteps').innerHTML = '<p class="muted" style="margin-bottom:8px">各步骤结果：</p>' +
        x.steps.map(function (s) {
          return '<div class="needItem ' + (s.ok ? 'ok' : 'no') + '">' +
            '<span class="ic">' + (s.ok ? '✓' : '✗') + '</span>' +
            '<span class="nm">' + window.AIWS.esc(s.name) +
            '<br><small class="muted">' + window.AIWS.esc(s.detail) +
            '　·　' + window.AIWS.esc(s.api) + '</small></span></div>';
        }).join('');

      await loadMemory();
    } catch (e) {
      if (memTick) { clearInterval(memTick); memTick = null; }
      $('tbMemBusy').style.display = 'none';
      alert('释放失败：' + e.message);
    } finally {
      $('tbMemBtn').disabled = false;
    }
  }

  // ------------------------------------------------------------------
  //  下载器
  // ------------------------------------------------------------------
  async function probe() {
    const url = $('tbUrl').value.trim();
    if (!url) { setDlStatus('请先填下载地址'); return; }
    setDlStatus('正在探测…', '');
    $('tbProbe').disabled = true;
    try {
      const r = await window.AIWS.api('/api/toolbox/dl/probe', { url: url });
      if (!r.ok) { setDlStatus(r.error || '探测失败'); $('tbProbeBox').style.display = 'none'; return; }
      probed = r;
      $('tbProbeBox').style.display = 'block';
      $('tbProbeInfo').innerHTML =
        '<div class="kv"><span>文件大小</span><b>' + window.AIWS.esc(r.sizeText || '未知') + '</b></div>' +
        '<div class="kv"><span>文件名</span><b>' + window.AIWS.esc(r.name || '—') + '</b></div>' +
        '<div class="kv"><span>支持分块并发</span><b style="color:' + (r.ranges ? '#1f7a4d' : '#9c2f26') + '">' +
        (r.ranges ? '✓ 支持（可多线程提速）' : '✗ 不支持（只能单线程）') + '</b></div>';
      if (r.name && !$('tbName').value.trim()) $('tbName').value = r.name;
      $('tbThreads').disabled = !r.ranges;
      if (!r.ranges) { $('tbThreads').value = 1; $('tbThreadVal').textContent = '1'; }
      setDlStatus('', '');
    } catch (e) {
      setDlStatus('探测失败：' + e.message);
    } finally {
      $('tbProbe').disabled = false;
    }
  }

  async function startDownload() {
    const url = $('tbUrl').value.trim();
    if (!url) { setDlStatus('请先填下载地址'); return; }
    if (!$('tbFolder').value.trim()) { setDlStatus('请先选保存目录'); return; }
    $('tbDlBtn').disabled = true;
    $('tbDlStatus').textContent = '';
    $('tbDlStatus').className = 'status';
    try {
      const r = await window.AIWS.api('/api/toolbox/dl/start', {
        url: url, folder: $('tbFolder').value.trim(),
        name: $('tbName').value.trim(),
        threads: parseInt($('tbThreads').value, 10)
      });
      if (!r.ok) { setDlStatus(r.error || '启动失败'); $('tbDlBtn').disabled = false; return; }
      curJob = r.job;
      $('tbDlProg').style.display = 'block';
      $('tbDlStats').style.display = 'flex';
      $('tbSegBox').style.display = 'block';
      $('tbDlCancel').style.display = '';
      $('tbDlBar').style.width = '0%';
      pollDl(r.job);
    } catch (e) {
      setDlStatus('启动失败：' + e.message);
      $('tbDlBtn').disabled = false;
    }
  }

  function pollDl(job) {
    if (dlTimer) clearInterval(dlTimer);
    dlTimer = setInterval(async function () {
      let r;
      try { r = await window.AIWS.api('/api/toolbox/dl/progress?job=' + encodeURIComponent(job)); }
      catch (e) { return; }

      $('tbDlBar').style.width = (r.percent || 0) + '%';
      $('tbDlText').textContent = (r.percent || 0) + '%　' + r.doneText + ' / ' + r.totalText +
        (r.state === 'running' ? '' : '　·　' + r.state);
      $('tbDlDone').textContent = r.doneText;
      $('tbDlSpeed').textContent = r.speedText;
      $('tbDlEta').textContent = r.etaText;
      $('tbDlThreads').textContent = r.threads + ' 条';

      $('tbSegs').innerHTML = (r.segs || []).map(function (s) {
        const cls = s.state === '完成' ? 'ok' : (s.state === '失败' ? 'err' : '');
        return '<div class="segItem ' + cls + '">' +
          '<span>线程 ' + (s.i + 1) + '　<b>' + s.percent + '%</b></span>' +
          '<span class="segBarWrap"><span class="segBar" style="width:' + s.percent + '%"></span></span>' +
          '<span>' + window.AIWS.esc(s.state) + '</span></div>';
      }).join('');

      if (r.state === 'running') return;
      clearInterval(dlTimer); dlTimer = null;
      $('tbDlBtn').disabled = false;
      $('tbDlCancel').style.display = 'none';
      if (r.state === 'done') {
        setDlStatus('下载完成 ✓　' + r.result.sizeText + '　用时 ' + r.result.seconds + ' 秒\n' +
          r.result.path, 'ok');
      } else if (r.state === 'error') {
        setDlStatus('下载失败：' + r.error, 'err');
      } else {
        setDlStatus('已取消（已下载的分块保留，重新开始可续传）');
      }
    }, 700);
  }

  async function cancelDl() {
    if (!confirm('确定取消下载吗？\n\n（当前分块会停下，已下载的部分保留在 .partN 文件里）')) return;
    if (curJob) await window.AIWS.api('/api/toolbox/dl/cancel', { job: curJob });
    if (dlTimer) { clearInterval(dlTimer); dlTimer = null; }
    $('tbDlBtn').disabled = false;
    $('tbDlCancel').style.display = 'none';
    setDlStatus('已取消');
  }

  function setDlStatus(m, kind) {
    const el = $('tbDlStatus');
    el.textContent = m || '';
    el.className = 'status' + (kind ? ' ' + kind : '');
  }

  // ------------------------------------------------------------------
  //  会话备份（DSH 上下文落文本）
  // ------------------------------------------------------------------
  function setBkStatus(m, kind) {
    const el = $('tbBkStatus');
    if (!el) return;
    el.textContent = m || '';
    el.className = 'status' + (kind ? ' ' + kind : '');
  }

  function bkRow(left, mid, right) {
    return '<div class="segRow"><span class="nm">' + left + '</span>' +
      '<span>' + mid + '</span><span class="muted">' + right + '</span></div>';
  }

  function renderBackup(s) {
    const esc = window.AIWS.esc;
    const t = s.task || {};
    const pill = $('tbBkPill');
    if (pill) {
      pill.textContent = t.exists ? '● 自动备份已开' : '● 计划任务没找到';
      pill.style.background = t.exists ? '#e8f7ee' : '#fdf3e6';
      pill.style.color = t.exists ? '#177b3f' : '#8a5a00';
    }
    const top = $('tbBkTop');
    if (top) {
      top.innerHTML =
        '<div class="kv"><span>备份目录</span><b>' + esc(s.dir) + '</b></div>' +
        '<div class="kv"><span>已有备份</span><b>' + s.fileCount + ' 份　合计 ' +
          esc(s.totalText) + '</b></div>' +
        '<div class="kv"><span>自动备份</span><b>' +
          (t.exists ? '每 ' + (t.intervalMin || 10) + ' 分钟一次（' + esc(t.status || '就绪') + '）'
                    : '没找到计划任务 —— 手动点「立刻备份一次」也能用') + '</b></div>' +
        '<div class="kv"><span>下次自动跑</span><b>' + esc(t.next || '—') + '</b></div>';
    }
    const sb = $('tbBkSessions');
    if (sb) {
      const list = s.sessions || [];
      sb.innerHTML = list.length ? list.map(function (x) {
        return bkRow(esc(x.name), esc(x.nowText) + '　已备份 ' + esc(x.backedText) +
          '（' + x.count + ' 次）', esc(x.lastTime || '还没备份'));
      }).join('') : '<p class="muted">（还没有记录 —— 点「立刻备份一次」）</p>';
    }
    const fb = $('tbBkFiles');
    if (fb) {
      const list = s.files || [];
      fb.innerHTML = list.length ? list.map(function (f) {
        return bkRow(esc(f.name), esc(f.sizeText), esc(f.time));
      }).join('') : '<p class="muted">（还没有备份文件）</p>';
    }
    const lg = $('tbBkLog');
    if (lg) lg.textContent = (s.log || []).join('\n') || '（日志还是空的）';
  }

  async function loadBackup() {
    try {
      const r = await window.AIWS.api('/api/toolbox/backup');
      if (!r.ok) { setBkStatus('读取失败：' + (r.error || ''), 'err'); return; }
      renderBackup(r);
    } catch (e) { setBkStatus('读取失败：' + e.message, 'err'); }
  }

  async function runBackup() {
    const btn = $('tbBkRun');
    if (btn) btn.disabled = true;
    setBkStatus('正在查一轮…（大会话要几秒）', '');
    try {
      const r = await window.AIWS.api('/api/toolbox/backup/run', {});
      if (!r.ok) { setBkStatus('失败：' + (r.error || ''), 'err'); return; }
      setBkStatus(r.made ? ('✓ 备份了 ' + r.made + ' 个，用时 ' + r.seconds + ' 秒')
                         : ('✓ 查完了，没有需要新备份的（用时 ' + r.seconds + ' 秒）'), 'ok');
      await loadBackup();
    } catch (e) {
      setBkStatus('失败：' + e.message, 'err');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // ------------------------------------------------------------------
  //  初始化
  // ------------------------------------------------------------------
  function init() {
    if (started) return;
    started = true;

    // 校园网
    $('cpBtn').addEventListener('click', function () { cpLogin(false); });
    $('cpForce').addEventListener('click', function () { cpLogin(true); });
    $('cpRefresh').addEventListener('click', function () { cpLoad(); });
    $('cpCfgBtn').addEventListener('click', function () {
      const c = $('cpCfgBox');
      c.style.display = (c.style.display === 'none' || !c.style.display) ? 'block' : 'none';
    });
    $('cpSave').addEventListener('click', async function () {
      const patch = {
        campusUser: $('cpUserIn').value.trim(),
        campusDomain: $('cpDomainIn').value,
        campusPortal: $('cpPortalIn').value.trim() || 'http://10.30.4.3'
      };
      if ($('cpPwdIn').value.trim()) patch.campusPassword = $('cpPwdIn').value.trim();
      try {
        await window.AIWS.api('/api/campus/save', patch);
        $('cpPwdIn').value = '';
        $('cpStatus').textContent = '账号设置已保存 ✓';
        $('cpStatus').className = 'status ok';
        await cpLoad();
      } catch (e) {
        $('cpStatus').textContent = '保存失败：' + e.message;
        $('cpStatus').className = 'status err';
      }
    });

    cpLoad();
    cpAutoLoad();
    $('cpAutoBtn').addEventListener('click', cpAutoToggle);
    $('cpOldDel').addEventListener('click', cpOldDel);

    $('tbMemBtn').addEventListener('click', releaseMemory);
    $('tbMemRefresh').addEventListener('click', loadMemory);
    $('tbProbe').addEventListener('click', probe);
    $('tbDlBtn').addEventListener('click', startDownload);
    $('tbDlCancel').addEventListener('click', cancelDl);
    $('tbThreads').addEventListener('input', function () {
      $('tbThreadVal').textContent = this.value;
    });
    $('tbPickFolder').addEventListener('click', async function () {
      const r = await window.AIWS.api('/api/openfolder', {});
      if (!r.ok) { alert('打开输出目录失败：' + (r.error || '')); return; }
      const d = prompt('把保存目录粘进来（可先用「打开输出目录」找位置）：', $('tbFolder').value || '');
      if (d && d.trim()) $('tbFolder').value = d.trim();
    });
    loadMemory();

    // 会话备份
    $('tbBkRun').addEventListener('click', runBackup);
    $('tbBkRefresh').addEventListener('click', loadBackup);
    $('tbBkOpen').addEventListener('click', async function () {
      const r = await window.AIWS.api('/api/toolbox/backup/open', {});
      if (!r.ok) setBkStatus('打不开：' + (r.error || ''), 'err');
    });
    loadBackup();

    document.addEventListener('aiws:tab', function (e) {
      if (e.detail.tab === 'toolbox') { loadMemory(); cpLoad(); loadBackup(); }
    });
  }

  document.addEventListener('aiws:ready', init);
})();
