/* 开屏动画 —— 12 块等大三角毛玻璃拼成十二边形、转圈，再凝聚成「开场提示」
   ------------------------------------------------------------------
   流程（总长约 4.5~5 秒）：
     0.0s  12 块**一样大的三角形**从屏幕四周出发（每块随机晚 0~0.5 秒）
     3.5s  全部抵达 —— 拼成一个**正十二边形**（每个三角形占一条边）
     3.1s  拼合的同时开始**整体转圈**（一直转到面板出现）
     4.2s  凝聚成一块毛玻璃面板「开场提示」
     5.0s  撤掉三角形（省性能）

   面板里写三件必须提前知道的事：
     ① 哪些地方会花钱（三处）  ② 哪些需要实名认证  ③ 各功能需要的算力

   ★ 数据口径：按实测写（速率数据来自本机跑过的实测记录）；
     付费单价取自火山方舟价目表和 DeepSeek 官方价目表。
   ★ 不写"本机 / 某型号显卡"这类话 —— 老师的电脑配置不同，写死会误导 ✗

   控制：
     · 面板上勾「下次不再自动显示」→ 存 localStorage
     · URL 加 ?intro=1 可以强制再放一次（方便演示）
*/
(function () {
  'use strict';
  var KEY = 'aiws:intro:off';
  var force = location.search.indexOf('intro=1') >= 0;
  // ?nointro=1 直接跳过开屏（截图、调试、给老师演示时都用得上）
  if (location.search.indexOf('nointro=1') >= 0) return;

  try {
    if (localStorage.getItem(KEY) === '1' && !force) return;
  } catch (e) { /* 隐私模式读不了 localStorage —— 那就照放 */ }

  var N = 12;                     // 十二块，拼成正十二边形

  // 样式跟着组件走（自包含：页面只要引这一个文件，去掉也干净）
  var CSS = `
.intro-layer{position:fixed; inset:0; z-index:9998; display:flex; align-items:center;
  justify-content:center; padding:24px;
  background:radial-gradient(120% 130% at 50% 38%, rgba(14,50,92,.58) 0%, rgba(7,26,52,.82) 100%);
  backdrop-filter:blur(7px); -webkit-backdrop-filter:blur(7px);
  transition:opacity .45s ease;}
.intro-layer.bye{opacity:0; pointer-events:none}

/* 碎块容器：整体转圈就靠它（inset:0 所以旋转中心 = 屏幕中心）*/
.intro-shards{position:absolute; inset:0; pointer-events:none;
  transform-origin:50% 50%;}
.intro-shards.spin{animation:introSpin 3.4s linear infinite}
@keyframes introSpin{from{transform:rotate(0deg)} to{transform:rotate(360deg)}}

/* 三角形碎块：等大、毛玻璃质感、从四周飞到十二边形上各自的位置 */
.shard{position:absolute; left:50%; top:50%;
  width:var(--w); height:var(--h);
  margin-left:calc(var(--w) / -2); margin-top:calc(var(--h) / -2);
  background:linear-gradient(160deg, rgba(255,255,255,.54) 0%, rgba(208,233,255,.30) 55%, rgba(160,202,243,.38) 100%);
  clip-path:polygon(50% 0%, 100% 100%, 0% 100%);     /* 等腰三角形，尖朝上 */
  opacity:0;
  transform:translate3d(var(--x), var(--y), 0) rotate(var(--r)) scale(1.12);
  transition:transform 2.6s cubic-bezier(.22,.86,.24,1) var(--d), opacity .7s ease var(--d);}
.intro-layer.go .shard{opacity:var(--o); transform:translate3d(var(--tx), var(--ty), 0) rotate(var(--tr)) scale(1)}

.intro-panel{position:relative; z-index:2; width:min(780px,94vw); max-height:88vh;
  padding:26px 30px 22px; border-radius:26px;
  background:linear-gradient(180deg, rgba(255,255,255,.80) 0%, rgba(246,252,255,.68) 100%);
  backdrop-filter:blur(28px) saturate(1.6); -webkit-backdrop-filter:blur(28px) saturate(1.6);
  border:1.5px solid rgba(255,255,255,.75);
  box-shadow:0 34px 90px rgba(10,42,84,.42), inset 0 1px 0 rgba(255,255,255,.9);
  opacity:0; transform:scale(.9) translateY(14px);
  transition:opacity .5s ease, transform .6s cubic-bezier(.18,.9,.24,1);}
.intro-layer.show-panel .intro-panel{opacity:1; transform:none}
.intro-head{text-align:center; margin-bottom:14px; flex:0 0 auto}
.intro-badge{display:inline-block; padding:4px 14px; border-radius:999px; font-size:12px;
  font-weight:800; letter-spacing:2px; color:#fff;
  background:linear-gradient(115deg,#2f7ddb,#5aa9f0); box-shadow:0 6px 16px rgba(47,125,219,.35)}
.intro-head h2{margin:12px 0 0; font-size:26px; font-weight:900; color:#123a6b; letter-spacing:1px;
  display:flex; flex-direction:column; align-items:center; gap:4px}
.intro-head h2 span{font-size:14.5px; font-weight:700; color:#3f7cc0; letter-spacing:3px}
.intro-sub{margin:8px 0 0; font-size:13.5px; color:#4a6d94; font-weight:600}
.intro-grid{display:flex; flex-direction:column; gap:12px;
  overflow:auto; flex:1 1 auto; min-height:0; padding-right:6px; margin-right:-6px}
.intro-card{border-radius:16px; padding:14px 16px; background:rgba(255,255,255,.62);
  border:1px solid rgba(150,195,235,.55); box-shadow:inset 0 1px 0 rgba(255,255,255,.9)}
.intro-card h3{margin:0 0 8px; font-size:15px; font-weight:800; color:#17456f}
.intro-card ul{margin:0; padding-left:20px}
.intro-card li{font-size:13.5px; line-height:1.72; color:#2c4c6d}
.intro-card li span{opacity:.78}
.intro-card.pay{border-color:rgba(240,180,120,.75); background:rgba(255,250,244,.72)}
.intro-card.pay h3{color:#9a5a10}
.intro-card.real{border-color:rgba(160,180,240,.75); background:rgba(247,249,255,.72)}
.intro-card.real h3{color:#3c4a9a}
.intro-card.zhipu{border-color:rgba(130,215,255,.85); background:rgba(244,252,255,.76)}
.intro-card.zhipu h3{color:#0b6ba6}
.intro-note{margin:8px 0 0; font-size:12.8px; line-height:1.7; color:#3d6485}
.intro-foot{display:flex; align-items:center; justify-content:space-between; gap:14px;
  flex-wrap:wrap; margin-top:14px; padding-top:14px; border-top:1px solid rgba(150,195,235,.5);
  flex:0 0 auto}
.intro-chk{font-size:12.5px; color:#4a6d94; font-weight:600; cursor:pointer;
  display:flex; align-items:center; gap:7px}
.intro-chk code{background:rgba(47,125,219,.12); padding:1px 5px; border-radius:5px}
.intro-go{padding:11px 26px; border:0; border-radius:999px; cursor:pointer;
  font-family:inherit; font-size:15px; font-weight:800; color:#fff;
  background:linear-gradient(115deg,#2f7ddb 0%,#5aa9f0 60%,#7cc0ff 100%);
  box-shadow:0 10px 26px rgba(47,125,219,.4); transition:transform .16s ease, box-shadow .16s ease}
.intro-go:hover{transform:translateY(-1px); box-shadow:0 14px 32px rgba(47,125,219,.5)}
.intro-go:active{transform:translateY(0)}
/* 面板三层：标题常驻 + 中间滚动 + 按钮常驻（不然小屏上按钮会被顶出屏幕 ✗）*/
.intro-panel{display:flex; flex-direction:column; overflow:hidden}
@media (max-width:640px){
  .intro-panel{padding:20px 16px 16px; border-radius:20px}
  .intro-head h2{font-size:21px}
  .intro-card li{font-size:12.8px}
}
@media (prefers-reduced-motion:reduce){
  .shard{display:none}
  .intro-shards.spin{animation:none}
  .intro-layer .intro-panel{opacity:1; transform:none}
}`;

  function injectCSS() {
    if (document.getElementById('introStyle')) return;
    var s = document.createElement('style');
    s.id = 'introStyle';
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  var layer, shards, panel;

  function h(html) {                            // 极简建 DOM
    var d = document.createElement('div');
    d.innerHTML = html.trim();
    return d.firstChild;
  }

  var CONTENT = `
    <div class="intro-head">
      <div class="intro-badge">开 场 提 示</div>
      <h2>超级AI工作台<span>一切奇迹的起点</span></h2>
      <p class="intro-sub">开始之前，先把几件事说清楚 —— 都是实话，不藏</p>
    </div>

    <div class="intro-grid">
      <section class="intro-card zhipu">
        <h3>🎭 新功能 · Live2D 生成（把立绘变"活"）</h3>
        <ul>
          <li><b>是什么</b> —— 把一张平面动漫立绘，变成会<b>眨眼、呼吸、转头、头发摆动</b>的
              Live2D 模型（<code>.moc3</code>），能拿去当虚拟形象、桌面挂件</li>
          <li><b>怎么做</b> —— AI 先把立绘拆成头发、脸、眼睛等<b>分层</b>，再自动绑定骨架、
              加上动作，导出就能用</li>
          <li><b>两种方式</b> —— 「本地引擎」用你自己的显卡（不联网，约 20 分钟）；
              「在线官方演示」走作者云端（免费、要登录，约 10 分钟）</li>
        </ul>
        <p class="intro-note">成品在 <code>桌面\Live2D成品\</code>，里面的 <code>model.moc3</code> 就是模型本体 ✓</p>
      </section>

      <section class="intro-card">
        <h3>✨ 智谱 AI · AI 生视频（免费）</h3>
        <ul>
          <li><b>完全免费</b> —— 走智谱云端的 <b>CogVideoX-Flash</b>，<b>不花一分钱、不占你的显卡</b>
              <span>（约 60~90 秒出一条 5 秒 1080p）</span></li>
          <li><b>成片干净</b> —— 出片后本工作台会以<b>特殊技术手段去除 AI 水印</b>，
              尺寸不变、看不出痕迹，可以直接用 ✓</li>
          <li><b>怎么开始</b> —— 到 <code>bigmodel.cn</code> 注册并<b>实名认证</b>，
              在「API Keys」建一把 Key，填进「⚙️ 设置」即可
              <span>（注册即送 2000 万 Token 额度）</span></li>
          <li><b>想更清晰</b>（可选、要花钱）—— 付费档 <b>CogVideoX-3：1 元/次</b>，
              5 秒或 10 秒、最高 4K、可带音频、支持首尾帧
              <span>（代码不用改，在页面里选模型即可）</span></li>
        </ul>
        <p class="intro-note">
          那一页里还有 <b>💡 AI 帮我写提示词</b> —— 你写一句话，它替你扩写成
          <b>3 条</b>专业视频提示词，点一条就填进去 ✓
        </p>
      </section>

      <section class="intro-card pay">
        <h3>💰 会花钱的地方（只有这三处）</h3>
        <ul>
          <li><b>豆包 Seedream 生图（API）</b> —— 走火山方舟云端接口，<b>按张计费</b>
              <span>（2K 约 0.3~0.5 元/张）</span></li>
          <li><b>AI 搜索引擎（API）</b> —— 走 DeepSeek 接口，<b>按 token 计费</b>
              <span>（实测一次搜索约 0.04~0.1 元，看问题长短）</span></li>
          <li><b>高速工作流（API）</b> —— 同样走 DeepSeek 接口，<b>按 token 计费</b>
              <span>（用得越久、上下文越长花得越多；和 AI 搜索共用同一把 Key）</span></li>
        </ul>
        <p class="intro-note">其余全部功能都在<b>你自己的电脑上</b>跑：不花钱、不上传、断网也能用 ✓</p>
      </section>

      <section class="intro-card real">
        <h3>🪪 需要实名认证的地方（只有这三处）</h3>
        <ul>
          <li><b>火山方舟（API）</b> —— 豆包生图用的，要在火山引擎完成实名认证，才能开通模型、调用接口</li>
          <li><b>DeepSeek 开放平台（API）</b> —— AI 搜索与高速工作流共用这一把，要实名认证后才能充值和调用 API</li>
          <li><b>智谱开放平台（API）</b> —— AI 生视频用的，要在
              <code>bigmodel.cn</code> 完成实名认证后才能建 Key、调接口</li>
        </ul>
        <p class="intro-note">本地功能<b>不需要任何实名认证</b>，装好就能用 ✓</p>
      </section>

      <section class="intro-card spec">
        <h3>🖥 各功能需要的算力（按档位）</h3>
        <p class="intro-note" style="margin-bottom:8px">
          <b>耗时是参考值</b>（机器越好越快），不同电脑会有差异 ✓
        </p>
        <ul>
          <li>🟢 <b>只要能联网</b>：豆包生图（API）· AI 搜索（API）· 高速工作流（API）· <b>AI 生视频（API，智谱云端）</b> · Live2D 在线演示（API）　<span>（云端出结果，不吃显卡）</span></li>
          <li>🔵 <b>纯 CPU 就行</b>：AI 溶图 · 方案 A（本地）　<span>（0.5 秒）</span></li>
          <li>🟡 <b>核显够用</b>：AI 抠图（本地）　<span>（约 1 分 17 秒）</span></li>
          <li>🟠 <b>独显 ≥ 6 GB</b>：AI 人声分离（本地）<span>（9~18 秒）</span> · 声音包朗读（本地）<span>（约 30 秒）</span></li>
          <li>🔴 <b>独显 ≥ 8 GB</b>：本地千问生图（本地）<span>（约 1 分钟）</span> · 音乐工坊（本地）<span>（约 44 秒）</span>
              · 溶图方案 B（本地）<span>（87~93 秒）</span> · 翻唱（本地）<span>（60 秒的歌约 6 分钟）</span>
              · Live2D 本地引擎（本地）<span>（约 20 分钟）</span></li>
        </ul>
        <p class="intro-note">
          <b>内存建议</b>：<b>16 GB 以上</b>更稳。后端都是<b>按需启动</b>的 ——
          但别同时把所有后端都开着，容易挤爆内存。
        </p>
      </section>
    </div>

    <div class="intro-foot">
      <label class="intro-chk"><input type="checkbox" id="introNoShow"> 下次不再自动显示（想再看：网址后面加 <code>?intro=1</code>）</label>
      <button class="intro-go" id="introGo">进入工作台 →</button>
    </div>
  `;

  /* 12 块等大三角形：从四周飞到位后拼成正十二边形
     ─ 每个三角形 = 「中心 → 一条边」的那个等腰三角形（顶点在中心，底边就是十二边形的一条边）
     ─ 三角形自身是「尖朝上」的（clip-path: 50% 0 尖），所以旋转到尖朝中心即可
  */
  function buildShards() {
    shards = document.createElement('div');
    shards.className = 'intro-shards';

    var W = window.innerWidth, H = window.innerHeight;
    var R = Math.min(W, H) * 0.26;                     // 十二边形外接圆半径（大一些）
    R = Math.max(120, Math.min(R, 205));
    var DEG = Math.PI / 180;
    var side = 2 * R * Math.sin(15 * DEG) * 0.88;      // 底边（留一点缝，看得出是 12 块）
    var hgt = R * Math.cos(15 * DEG) * 0.88;           // 三角形高
    var startR = Math.max(W, H) * 0.72;                // 出发点：屏幕外

    for (var i = 0; i < N; i++) {
      var d = document.createElement('div');
      d.className = 'shard';
      d.style.setProperty('--w', side.toFixed(1) + 'px');
      d.style.setProperty('--h', hgt.toFixed(1) + 'px');

      // 目标：这条边的中点方向 θ = i*30 + 15 度，离中心 hgt/2
      var th = (i * 30 + 15) * DEG;
      var dist = hgt / 2;
      d.style.setProperty('--tx', Math.round(Math.cos(th) * dist) + 'px');
      d.style.setProperty('--ty', Math.round(Math.sin(th) * dist) + 'px');
      // 朝向：尖对准中心（尖原本朝上 = -90°，所以要转 θ + 270）
      d.style.setProperty('--tr', (i * 30 + 15 + 270) + 'deg');

      // 起点：从屏幕外同一方向飞进来（角度加一点随机，免得整齐得像机器）
      var a0 = th + (Math.random() - 0.5) * 0.5;
      d.style.setProperty('--x', Math.round(Math.cos(a0) * startR) + 'px');
      d.style.setProperty('--y', Math.round(Math.sin(a0) * startR * 0.85) + 'px');
      d.style.setProperty('--r', Math.round(Math.random() * 200 - 100) + 'deg');

      d.style.setProperty('--o', (0.72 + Math.random() * 0.28).toFixed(2));
      d.style.setProperty('--d', (Math.random() * 0.4).toFixed(2) + 's');
      shards.appendChild(d);
    }
    return shards;
  }

  function close() {
    var chk = document.getElementById('introNoShow');
    if (chk && chk.checked) {
      try { localStorage.setItem(KEY, '1'); } catch (e) { }
    }
    layer.classList.add('bye');
    setTimeout(function () {
      if (layer && layer.parentNode) layer.parentNode.removeChild(layer);
    }, 460);
  }

  function play() {
    injectCSS();
    // 开屏期间压住「首次提示」层 —— 两者会叠在一起，而且提示层在更上层会盖住开屏 ✗
    // 开屏层从 DOM 移除后自动停止压制，所以提示之后照常出现 ✓
    var tipsGuard = setInterval(function () {
      if (!document.querySelector('.intro-layer')) { clearInterval(tipsGuard); return; }
      var el = document.getElementById('tipsLayer');
      if (el && !el.hidden) el.hidden = true;
    }, 180);

    layer = h('<div class="intro-layer"></div>');
    layer.appendChild(buildShards());
    panel = h('<div class="intro-panel">' + CONTENT + '</div>');
    layer.appendChild(panel);
    document.body.appendChild(layer);

    document.getElementById('introGo').addEventListener('click', close);
    layer.addEventListener('click', function (e) { if (e.target === layer) close(); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && layer && layer.parentNode) close();
    });

    // 两帧之后再触发动画，保证 transition 真的跑起来
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        layer.classList.add('go');                                          // ① 三角块从四周飞向十二边形的位置
        setTimeout(function () { if (shards) shards.classList.add('spin'); }, 3100);   // ② 拼好之后再开始转圈
        setTimeout(function () { layer.classList.add('show-panel'); }, 4200);          // ③ 凝聚成面板
        setTimeout(function () { if (shards) shards.style.display = 'none'; }, 4900);  // ④ 撤掉三角块（省性能）
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', play);
  } else {
    play();
  }
})();
