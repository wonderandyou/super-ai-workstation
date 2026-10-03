/* ============================================================
   大型 AI 工作站 —— 框架层
   标签页系统 / 首次提示 / 配置 / 与服务端通信
   ============================================================ */
(function () {
  'use strict';

  const LS_TIPS = 'aiws.tips.';        // 每个标签页一条：aiws.tips.doubao = '0' | '1'

  // ------------------------------------------------------------------
  //  标签页定义（以后加功能就在这儿加一条）
  // ------------------------------------------------------------------
  const TABS = {
    vgen: {
      icon: '🎬',
      title: 'AI 生视频',
      tipsTitle: 'AI 生视频 · 使用说明',
      tipsBody: `
        <p>用<b>智谱（GLM）的 CogVideoX-Flash</b> 在云端生成视频，<b>完全免费</b>，不占你的显卡 ✓</p>
        <p><b>第一次用先做一件事：</b>去「⚙️ 设置」填一把 <b>智谱 API Key</b>
           （到 <a href="https://bigmodel.cn/usercenter/proj-mgmt/apikeys" target="_blank">bigmodel.cn → API Keys</a> 建一个，
            注册送 2000 万 Token 额度）。</p>
        <p><b>怎么用：</b>写一段画面描述（主体 + 动作 + 镜头 + 光线）→ 需要的话拖一张图当首帧（图生视频）
           → 选画幅 → 点「开始生成」。<b>约 60~90 秒</b>出一条 5 秒片。</p>
        <p><b>关于水印：</b>为让成片可直接使用，本工作台采用
           <b>特殊技术手段去除 AI 水印</b> —— 面板上该选项<b>默认已开启</b>，
           它会先裁掉带标的那一角、再无损缩回原尺寸，<b>尺寸不变、看不出痕迹</b> ✓
           如不需要，在面板上取消勾选即可。</p>`
    },
    live2d: {
      icon: '🎭',
      title: 'Live2D 制作',
      tipsTitle: 'Live2D 制作 · 使用说明',
      tipsBody: `
        <p>把一张<b>立绘</b>变成真正的 Live2D 模型：AI 先把它拆成带深度的<b>分层 PSD</b>，
           再交给本机的 <b>PSD2Live</b> 自动分层 / 网格 / 绑定 / 物理，
           最后导出 <b>moc3 + cmo3 + 动作 + 贴图</b>。</p>
        <p><b>拆层两种方式，二选一：</b></p>
        <ul>
          <li><b>本地引擎</b> —— 用你自己显卡跑：不联网、不排队、想跑多少跑多少，
              8 GB 显存能跑，一张大约 3~8 分钟。</li>
          <li><b>在线官方演示</b> —— 论文作者在 ModelScope 上的免费演示（不占你的显存），
              但那是共享队列，通常要等 <b>5~15 分钟</b>。</li>
        </ul>
        <p><b>立绘要求（直接决定成品好坏）：</b>单人、全身正面站姿、四肢别交叉、背景干净。</p>
        <p><b>产物在哪：</b><code>桌面\\Live2D成品\\&lt;作品名&gt;\\</code> ——
           <code>model.moc3</code> 是模型本体（可直接给 Live2D 宿主用），
           <code>model.cmo3</code> 可以拿去 Cubism Editor 接着精修。</p>
        <p><b>一个注意：</b>导入 PSD 要求 PSD2Live 里是空工程，工作台会自动重开它的引擎清空
           （界面上的勾选项，<b>未保存的改动会丢</b>）。</p>`
    },
    search: {
      icon: '🔍',
      title: 'AI 搜索引擎',
      tipsTitle: 'AI 搜索引擎 · 使用说明',
      tipsBody: `
        <p>一个输入框：把问题写进去，它<b>联网搜一遍</b>，给你<b>分点论述的答案</b> + <b>来源</b> + <b>参考网址</b>。</p>
        <p><b>要先做一件事：</b>去「⚙️ 设置」填一把 <b>DeepSeek API Key</b>（没有就到
           <a href="https://platform.deepseek.com/api_keys" target="_blank">platform.deepseek.com → API 密钥</a> 建一个）。
           搜索走 DeepSeek 官方 Anthropic 兼容接口的<b>原生联网搜索</b>，和 DSH 自己联网是同一条通道。</p>
        <p><b>要知道的成本和脾气：</b></p>
        <ul>
          <li>一次搜索 = 一个完整模型轮次，通常 <b>20~60 秒</b>，按 token 计费（联网搜索本身不另外收费）。</li>
          <li>它<b>只依据搜到的资料</b>回答，资料不足会直说"资料不足"，不会硬编。</li>
          <li>已经要求它<b>优先采信正规来源</b>（官方文档 / 政府与高校 / 权威媒体 / 厂商官网），
              不采用内容农场；每条来源都列在下面，可以点开自己核对。</li>
          <li>搜索历史存在 <code>data\\搜索历史\\</code>，只留最近 60 次。</li>
        </ul>`
    },
    doubao: {
      icon: '🎨',
      title: '豆包 Seedream 生图',
      tipsTitle: '豆包 Seedream 生图 · 使用说明',
      tipsBody: `
        <p>云端出图，用的是<b>火山方舟（豆包 Seedream）</b>。第一次用请先做三件事：</p>
        <ol>
          <li><b>填 API Key</b> —— 去「⚙️ 设置」标签页粘贴。没有的话到
              <a href="https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey" target="_blank">方舟控制台 → API Key</a> 建一个。</li>
          <li><b>开通模型</b> —— 控制台「开通管理」里把 <code>doubao-seedream-5-0-pro</code> 之类勾上（免费开通，按张计费）。</li>
          <li><b>确认输出目录</b> —— 「⚙️ 设置」里可以改，默认在桌面 <code>豆包出图</code>。</li>
        </ol>
        <p><b>怎么用：</b>写一段画面描述 → 需要的话从下面的<b>提示词库</b>点一条填进去 → 选模型和尺寸 → 点「开始生成」。</p>
        <p><b>小提示：</b></p>
        <ul>
          <li>2K 尺寸通常要 <b>1~2 分钟</b>，别关窗口。</li>
          <li>有参考图时，尺寸和参考图张数会影响价格，右上角会实时显示<b>预计费用</b>。</li>
          <li>图会直接存进你的输出目录，命名带时间戳和提示词摘要，方便翻找。</li>
        </ul>`
    },
    qwen: {
      icon: '🐋',
      title: '本地千问生图',
      tipsTitle: '本地千问生图 · 使用说明',
      tipsBody: `
        <p>这一页用的是<b>你电脑自己的显卡</b>出图（ComfyUI + Qwen-Image-2.1），
           <b>完全离线、不花钱、图片不出本机</b>。</p>
        <p><b>和豆包生图的区别，一句话：</b></p>
        <ul>
          <li><b>豆包</b> = 租别人的服务器，画质最强、要联网、按张收费、图要上传</li>
          <li><b>本地千问</b> = 用自己的显卡，免费、离线、图不外传，画质够用</li>
        </ul>
        <p>页面里有一张<b>完整对比表</b>，可以直接给老师看。</p>
        <p><b>怎么用（三步）：</b></p>
        <ol>
          <li>第一次用：去 <b>「⚙️ 设置」页 → 本地模型</b> 点下载（约 15 GB，支持断点续传，中途关掉也没事）★ 下载入口只在这里 ✓</li>
          <li>点<b>右上角「⏻ 启动后端」</b>（首次加载模型要 1~3 分钟，之后就快了）</li>
          <li>写描述 → 选比例 → <b>开始生成</b>（1024×1024 约 1 分钟一张）</li>
        </ol>
        <p><b>硬件要求：</b>需要 NVIDIA 显卡，显存 ≥ 6 GB。页面会自动检测并给出结论。</p>`
    },
    dsh: {
      icon: '⚡',
      title: '高速工作流',
      tipsTitle: '高速工作流（DeepSeek Harness）· 四步装好',
      tipsBody: `
        <p>这一页用来<b>装 DSH 并启动它</b>。从上到下四步，按顺序来就行：</p>
        <ol>
          <li><b>先拿到 API Key</b> —— 去 <a href="https://platform.deepseek.com/" target="_blank">platform.deepseek.com</a>
              充值 → 「API keys」→ 创建 → <b>立刻复制</b> <code>sk-</code> 开头那串</li>
          <li><b>一键启动 DSH 安装程序</b> —— 自动装 Node.js（便携版，不要管理员）+ DSH 本体 + 桌面快捷方式</li>
          <li><b>填写 API</b> —— 把 Key 粘进来保存，存到用户环境变量 <code>DEEPSEEK_API_KEY</code>，DSH 认这个名字</li>
          <li><b>启动 DSH</b> —— 会<b>自动检测后端服务是否在线</b>，并自动开浏览器（带授权令牌）</li>
        </ol>
        <p><b>注意：</b>DSH 的首页地址<b>不能手输</b>，必须让它自己开浏览器 —— 那个链接里带着授权令牌。</p>
        <p>装好后桌面会出现 <b>「DSH Web」</b>快捷方式，以后双击它就行，不用回这个页面。</p>`
    },
    toolbox: {
      icon: '🧰',
      title: '电脑工具百宝箱',
      tipsTitle: '电脑工具百宝箱',
      tipsBody: `
        <p>三个常用小工具，都是本机跑、不联网：</p>
        <p><b>🔓 校园网一键登录</b> —— <b>深澜 Srun 认证</b>。
           点一下自动完成「拿挑战码 → 加密账号密码 → 提交认证」。
           第一次用点「账号设置」填学号密码 + 选认证方式（电信 / 移动 / 教师），
           填完可以开<b>开机自启</b>，以后开机自动登录、断网自动重连。</p>
        <p><b>💾 内存释放</b> —— 逐个进程回收工作集 + 收缩系统文件缓存 + 清空待机列表，
           跑完给你释放前 / 释放后的<b>实测对比</b>（不是估算）。</p>
        <p><b>⬇️ 多线程自定义下载器</b> —— 填个直链，按 HTTP Range 切块并发下载，
           显示每条线程的独立进度、实时速度和剩余时间。</p>`
    },
    gallery: {
      icon: '🖼️',
      title: '作品库',
      tipsTitle: '作品库 · 使用说明',
      tipsBody: `
        <p>这里按时间倒序列出你出过的图。</p>
        <ul>
          <li>点图片可以<b>放大预览</b></li>
          <li>悬停有<b>打开文件位置</b>和<b>删除</b></li>
          <li>记录保存在 <code>data/history.json</code>，图片本体在你的输出目录</li>
        </ul>`
    },
    settings: {
      icon: '⚙️',
      title: '设置',
      tipsTitle: '设置 · 使用说明',
      tipsBody: `
        <p>这里管三样东西：</p>
        <ul>
          <li><b>API Key</b> —— 存在本机 <code>data/config.json</code>，不上传任何地方</li>
          <li><b>输出目录</b> —— 出图直接落到这里</li>
          <li><b>首次提示</b> —— 想再看一遍各标签页的说明，点「重置」</li>
        </ul>`
    },
    matting: {
      icon: '✂️',
      title: 'AI 抠图',
      tipsTitle: 'AI 抠图 · 使用说明',
      tipsBody: `
        <p>把白底/实底的图抠成<b>透明底 PNG</b>，纯本地跑，不联网、不花钱。</p>
        <p><b>两个引擎可选：</b></p>
        <ul>
          <li><b>本地工具（RMBG ONNX）</b> —— 推荐。带去色溢，白蕾丝、发丝这类边缘不容易留白边；
              三个模型：<code>rmbg-1.4</code>（快）、<code>rmbg-2.0</code>（质量最好，默认）</li>
          <li><b>ComfyUI 内置 BiRefNet</b> —— 用 ComfyUI 的节点跑，会自动启动 ComfyUI</li>
        </ul>
        <p><b>参数怎么调：</b>电平上下限决定透明过渡范围；阈值收边让平涂图边缘更脆；
          裁掉透明边切掉四周空白；去色溢默认开（关掉浅色边缘可能带原背景颜色）。</p>
        <p><b>抠完想合成？</b>去「🎨 AI 溶图」，把风景照和抠好的图放一起。</p>`
    },
    blend: {
      icon: '🎨',
      title: 'AI 溶图',
      tipsTitle: 'AI 溶图 · 使用说明',
      tipsBody: `
        <p>把<b>抠好的主体</b>自然地放进<b>风景照</b>里，解决「一眼假」的问题。</p>
        <p><b>怎么用：</b></p>
        <ol>
          <li><b>导入背景</b> —— 一张风景照 / 场景图</li>
          <li><b>导入主体</b> —— <b>抠好图的透明底 PNG</b>（没有就点「前往抠图」先抠）</li>
          <li>调整<b>位置、大小、水平翻转</b>，摆到合适的地方</li>
          <li>点「开始溶图」</li>
        </ol>
        <p><b>为什么必须抠图：</b>溶图工具要知道哪部分是主体。透明底 PNG 自带蒙版，
          最省事也最准；直接给白底图，机器分不清主体和背景。</p>`
    },
    music: {
      icon: '🎵',
      title: 'AI 音乐工坊',
      tipsTitle: 'AI 音乐工坊 · 使用说明',
      tipsBody: `
        <p>这一页把桌面的 <b>AI 音乐工坊</b>整个嵌进来了，能力全在：</p>
        <ul>
          <li><b>写歌</b> —— 给风格标签 + 歌词，ACE-Step 1.5 生成完整歌曲（自动内嵌歌词到 FLAC）</li>
          <li><b>🎤 声音包</b> —— 传一段干净人声，让 AI 用<b>那个音色</b>来唱（走 ComfyUI 原生
              <code>ReferenceTimbreAudio</code>，免训练，而且出片快 3~4 倍）</li>
          <li><b>🎬 声音包制作器</b> —— 给一首歌或一段视频，用 <b>Demucs</b>（Meta 官方 · MIT）
              剥出干净人声，自动截取 + 响度归一化，导出成能直接用的声音包</li>
        </ul>
        <p><b>它其实是个独立服务</b>（端口 7870），这里只是内嵌显示。工作站启动时会自动把它拉起来。</p>
        <p><b>如果显示「引擎未启动」</b>：那是 ComfyUI 还没就绪的状态灯，不是报错。等几秒点「重新加载」。</p>`
    },
    stem: {
      icon: '🎚️',
      title: 'AI 人声分离',
      tipsTitle: 'AI 人声分离 · 使用说明',
      tipsBody: `
        <p>把一首歌拆开：<b>人声</b> + <b>伴奏</b>（也可以拆成四轨：人声 / 鼓 / 贝斯 / 其他）。</p>
        <p><b>引擎</b>是 <b>Demucs htdemucs</b>（Meta 官方，MIT 许可），跑在你自己显卡上 ——
           <b>不上传、不花钱、不需要联网</b>（模型本机已有缓存）。</p>
        <p><b>用法：</b>拖一首歌进来 → 选「两轨」还是「四轨」→ 选格式（WAV 无损 / FLAC 小一半 / MP3 最小）
           → 点「开始分离」。一首歌大约 <b>1~3 分钟</b>。</p>
        <p><b>几个实在话：</b></p>
        <ul>
          <li>分离质量取决于原曲：<b>人声和伴奏越分明，分得越干净</b>；现场版、合唱、混响很重的会留一点串音。</li>
          <li>两轨模式的「伴奏」= 鼓 + 贝斯 + 其他三轨相加，不会再抠细。</li>
          <li>跑之前最好<b>关掉 ComfyUI</b> 之类吃显存的后端（这台机器 8G 显存 + 16G 内存，挤一起容易失败）。</li>
          <li>超过 120 MB 的文件别用上传，直接丢进「分离素材」文件夹，列表里刷新一下就出现。</li>
        </ul>`
    },
    cover: {
      icon: '🎤',
      title: 'AI 翻唱',
      tipsTitle: 'AI 翻唱 · 使用说明',
      tipsBody: `
        <p>把一首歌里的人声换成<b>声音包</b>的音色，再和原伴奏混回去。四步：</p>
        <ol>
          <li><b>上传歌曲</b> —— 任意 mp3 / wav / flac / mp4</li>
          <li><b>Demucs 分离</b> —— Meta 官方 MIT，本地跑，剥出干净人声 + 伴奏</li>
          <li><b>Seed-VC 换音色</b> —— 用你选的声音包，零样本转换，<b>不用训练</b></li>
          <li><b>混音导出</b> —— 目标人声 + 伴奏合成成品</li>
        </ol>
        <p><b>环境已就绪</b>：<code>D:\\SeedVC</code>，torch 2.14+cu130，4 个模型全部通过 SHA256 校验。</p>`
    },
    tts: {
      icon: '📖',
      title: '声音包朗读',
      tipsTitle: '声音包朗读 · 使用说明',
      tipsBody: `
        <p>输入任意文字，选一个<b>声音包</b>，它就用那个音色念出来。</p>
        <p><b>底层是 F5-TTS</b>（官方仓库 · MIT 许可 · 零样本克隆，不用训练）：</p>
        <ul>
          <li>给它一段参考音频（就是你的<b>声音包</b>）+ 要念的文字，直接出音频</li>
          <li>生成时<b>跳过 Whisper 转写</b> —— 参考音频念的是什么，页面里填一下就行，还更快</li>
        </ul>
        <p><b>环境已就绪</b>：<code>D:\\F5TTS</code>，torch 2.11+cu128，实测 11 秒音频约 28 秒出片。</p>`
    }
  };

  const AIWS = {
    TABS,

    /* ---------- 🌐 全局拖拽上传 ----------
       把文件丢到页面任何地方，自动转交给「当前标签页」的第一个 file input。
       好处：一处改动，全站（抠图/溶图/音乐工坊/翻唱/生图参考图…）都有拖拽上传。

       为什么不用逐个绑：上传点散在 6 个文件里，逐个改既啰嗦又容易漏；
       而且每个 input 自己的 onchange 已经写好了上传逻辑 —— 我们只要
       把文件「塞进」那个 input 再触发 change，原有逻辑原样跑。

       注意：抠图页有自己的拖拽区（支持多张 + 网页图片拖入），
       它会 stopPropagation，所以这里的全局处理不会跟它打架。
    */
    initGlobalDrop() {
      if (document.getElementById('globalDropHint')) return;
      var hint = document.createElement('div');
      hint.id = 'globalDropHint';
      hint.innerHTML =
        '<div class="box">' +
        '<div class="big">📥</div>' +
        '<div class="t1" id="gdhTitle">松手就上传</div>' +
        '<div class="t2" id="gdhDesc"></div>' +
        '</div>';
      document.body.appendChild(hint);

      var depth = 0;

      function activeInputs() {
        var pane = document.querySelector('.pane.active');
        if (!pane) return [];
        return Array.prototype.slice.call(
          pane.querySelectorAll('input[type=file]'));
      }

      function show(e) {
        var inputs = activeInputs();
        var t = document.getElementById('gdhTitle');
        var d = document.getElementById('gdhDesc');
        var n = (e && e.dataTransfer && e.dataTransfer.items)
          ? e.dataTransfer.items.length : 0;
        if (!inputs.length) {
          hint.classList.add('no');
          t.textContent = '这一页没有上传的地方';
          d.textContent = '切到「AI 抠图」「AI 溶图」「AI 音乐工坊」这些页面再拖';
        } else {
          hint.classList.remove('no');
          t.textContent = n > 1 ? ('松手就上传这 ' + n + ' 个文件') : '松手就上传';
          var tab = document.querySelector('.tab.active');
          d.textContent = '会传到当前页面「' +
            (tab ? tab.textContent.trim() : '') + '」';
        }
        hint.classList.add('on');
      }

      function hide() { depth = 0; hint.classList.remove('on'); }

      document.addEventListener('dragenter', function (e) {
        if (!e.dataTransfer) return;
        // 只认文件拖拽（拖文字/链接不打扰）
        var hasFile = Array.prototype.some.call(
          e.dataTransfer.types || [], function (t) { return t === 'Files' });
        if (!hasFile) return;
        e.preventDefault();
        depth++;
        show(e);
      });

      document.addEventListener('dragover', function (e) {
        if (!e.dataTransfer) return;
        var hasFile = Array.prototype.some.call(
          e.dataTransfer.types || [], function (t) { return t === 'Files' });
        if (!hasFile) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
      });

      document.addEventListener('dragleave', function (e) {
        if (!e.dataTransfer) return;
        depth--;
        if (depth <= 0) hide();
      });

      document.addEventListener('drop', function (e) {
        if (!e.dataTransfer || !e.dataTransfer.files || !e.dataTransfer.files.length) {
          hide();
          return;
        }
        e.preventDefault();
        var files = e.dataTransfer.files;
        var dropX = e.clientX, dropY = e.clientY;
        hide();

        var inputs = activeInputs();
        if (!inputs.length) {
          AIWS.toast && AIWS.toast('这一页没有可以上传的地方');
          return;
        }
        // ★ 先看鼠标落在哪个卡片里 —— 溶图页有「背景」「主体」两个上传口，
        //   不这么判就永远只会塞给第一个。
        var scope = inputs;
        try {
          var el = document.elementFromPoint(dropX, dropY);
          var card = el && el.closest ? el.closest('.card') : null;
          if (card) {
            var inside = Array.prototype.slice.call(
              card.querySelectorAll('input[type=file]'));
            if (inside.length) scope = inside;
          }
        } catch (err) { }
        // 挑第一个能接受这些文件的 input（按 accept 判断）
        var target = null;
        for (var i = 0; i < scope.length; i++) {
          var acc = (scope[i].getAttribute('accept') || '').trim();
          if (!acc) { target = scope[i]; break }
          var okAll = true;
          for (var j = 0; j < files.length; j++) {
            var t = (files[j].type || '').toLowerCase();
            var name = (files[j].name || '').toLowerCase();
            var good = acc.split(',').some(function (a) {
              a = a.trim().toLowerCase();
              if (!a) return false;
              if (a === 'image/*') return t.indexOf('image/') === 0;
              if (a === 'audio/*') return t.indexOf('audio/') === 0;
              if (a === 'video/*') return t.indexOf('video/') === 0;
              if (a.charAt(0) === '.') return name.slice(a.length * -1) === a;
              return t === a;
            });
            if (!good) { okAll = false; break }
          }
          if (okAll) { target = scope[i]; break }
        }
        // 没有完全匹配的就退而求其次用范围内的第一个
        if (!target) target = scope[0];

        try {
          var dt = new DataTransfer();
          for (var k = 0; k < files.length; k++) dt.items.add(files[k]);
          target.files = dt.files;
          target.dispatchEvent(new Event('change', { bubbles: true }));
          AIWS.toast && AIWS.toast('已收到 ' + files.length + ' 个文件');
        } catch (err) {
          AIWS.toast && AIWS.toast('这个浏览器不支持拖拽赋值，请点按钮选文件');
        }
      });
    },

        loadMusic(startIfDown) {
      var f = document.getElementById('msFrame');
      var pill = document.getElementById('msPill');
      if (!f) return;

      var rb = document.getElementById('msReload');
      if (rb && !rb.dataset.wired) {
        rb.dataset.wired = '1';
        rb.onclick = function () { f.removeAttribute('data-loaded'); AIWS.loadMusic(true); };
      }
      var ob = document.getElementById('msOpen');
      if (ob && !ob.dataset.wired) {
        ob.dataset.wired = '1';
        ob.onclick = function () { window.open('http://127.0.0.1:7870', '_blank'); };
      }
      var sb = document.getElementById('msStop');
      if (sb && !sb.dataset.wired) {
        sb.dataset.wired = '1';
        sb.onclick = async function () {
          if (await AIWS.no('停止音乐工坊？\n\n界面会断开，内存会被释放。下次打开这一页会重新启动。')) return;
          sb.disabled = true;
          fetch('/api/music/stop', { method: 'POST',
            headers: { 'Content-Type': 'application/json' }, body: '{}' })
            .then(function (r) { return r.json(); })
            .then(function (x) {
              sb.disabled = false;
              f.removeAttribute('data-loaded');
              f.src = 'about:blank';
              if (pill) { pill.textContent = '● 已停止'; pill.style.background = '#f2f4f7'; pill.style.color = '#555'; }
            }).catch(function () { sb.disabled = false; });
        };
      }
      fetch('/api/music/status').then(function (r) { return r.json(); }).then(function (s) {
        var up = s && s.alive;
        if (pill) {
          pill.textContent = up ? '● 已就绪' : '● 启动中…';
          pill.style.background = up ? '#e8f7ee' : '#fff7e6';
          pill.style.color = up ? '#177b3f' : '#9a6a00';
        }
        // 就绪了才填 src，避免白屏报错
        if (up && f.getAttribute('data-loaded') !== '1') {
          f.src = 'http://127.0.0.1:' + (s.port || 7870);
          f.setAttribute('data-loaded', '1');
        }
      }).catch(async function () {
        if (pill) { pill.textContent = '● 启动中…'; }
      });
    },
    config: null,
    port: location.port || '8200',

    // ---------------- 与服务端通信 ----------------
    async api(path, body, method) {
      const opt = { method: method || (body ? 'POST' : 'GET'), headers: {} };
      if (body) {
        opt.headers['Content-Type'] = 'application/json';
        opt.body = JSON.stringify(body);
      }
      const r = await fetch(path + (path.includes('?') ? '&' : '') + '', opt);
      const txt = await r.text();
      let data;
      try { data = JSON.parse(txt); } catch (e) { throw new Error('服务端返回异常：' + txt.slice(0, 200)); }
      if (!r.ok && data && data.error) throw new Error(data.error);
      return data;
    },

    esc(s) {
      return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    },

    money(n) {
      n = Number(n) || 0;
      return n.toFixed(2);
    },

    // ---------------- 提示层 ----------------
    // ★ 打开网页时弹的「开始提示」（和各个标签页的说明分开，2026-10-01 主人要求）
    WELCOME: {
      icon: '🎬',
      title: '欢迎使用 超级AI工作台 βv1.2',
      body: `
        <p style="padding:10px 13px;border-radius:10px;background:rgba(90,190,255,.12);
                  border:1px solid rgba(90,190,255,.35);margin-bottom:12px">
          ✨ <b>本工作台已接入智谱 AI（GLM）</b> —— 「🎬 AI 生视频」用的是
          <b>CogVideoX-Flash：云端生成、完全免费</b>，不占你的显卡 ✓
          出片后由本工作台以<b>特殊技术手段去除 AI 水印</b>，成片干净、可直接使用 ✓
        </p>
        <p><b>用之前先做一件事：</b>去「⚙️ 设置」填一把 <b>智谱 API Key</b> ——
           到 <a href="https://bigmodel.cn/usercenter/proj-mgmt/apikeys" target="_blank">bigmodel.cn → API Keys</a>
           申请（注册送 <b>2000 万 Token</b>，需先实名认证）✓</p>
        <p class="muted" style="margin-top:8px">
          这一页的说明只讲这一个模块；各标签页第一次打开时还会各自弹一份「怎么用」✓
          想重看：到「⚙️ 设置」点<b>重置提示</b> ✓
        </p>`
    },

    showTips(tab, force) {
      const def = TABS[tab];
      if (!def || !def.tipsBody) return;
      // 调试/截图用：加 ?tips=0 可强制不弹；?tips=1 强制弹
      const q = (location.search || '');
      if (q.indexOf('tips=0') >= 0) return;
      if (q.indexOf('tips=1') < 0) {
        if (!force && localStorage.getItem(LS_TIPS + tab) === '0') return;
      }
      document.getElementById('tipsIcon').textContent = def.icon || '💡';
      document.getElementById('tipsTitle').textContent = def.tipsTitle || def.title;
      document.getElementById('tipsBody').innerHTML = def.tipsBody;
      document.getElementById('tipsSkip').checked = false;
      const layer = document.getElementById('tipsLayer');
      layer.dataset.tab = tab;
      layer.hidden = false;
    },

    initTips() {
      document.getElementById('tipsOk').addEventListener('click', function () {
        const layer = document.getElementById('tipsLayer');
        const tab = layer.dataset.tab;
        const skip = document.getElementById('tipsSkip').checked;
        // 勾了「不再显示」→ 记 0；没勾 → 记 1（下次打开还弹，跟原 HTA 行为一致）
        try { localStorage.setItem(LS_TIPS + tab, skip ? '0' : '1'); } catch (e) {}
        layer.hidden = true;
      });
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !document.getElementById('tipsLayer').hidden) {
          document.getElementById('tipsOk').click();
        }
      });
    },

    // ★ 开屏欢迎提示：不依赖当前是哪个标签页
    showWelcome() {
      const q = (location.search || '');
      if (q.indexOf('tips=0') >= 0) return;                    // 截图模式不弹
      if (q.indexOf('tips=1') < 0) {
        if (localStorage.getItem(LS_TIPS + '_welcome') === '0') return;
      }
      // ★★ 关键：initTabs 会排一个 560ms 后才弹「当前标签说明」的定时器，
      //    如果不清掉，欢迎提示马上就会被它盖掉 ✗（实测过）
      clearTimeout(AIWS._tipsTimer);
      AIWS._tipsTimer = null;
      const w = AIWS.WELCOME || {};
      document.getElementById('tipsIcon').textContent = w.icon || '👋';
      document.getElementById('tipsTitle').textContent = w.title || '欢迎';
      document.getElementById('tipsBody').innerHTML = w.body || '';
      document.getElementById('tipsSkip').checked = false;
      const layer = document.getElementById('tipsLayer');
      layer.dataset.tab = '_welcome';        // ★ 独立 key，跟标签页说明互不影响
      layer.hidden = false;
    },

    resetTips() {
      Object.keys(TABS).forEach(function (k) {
        try { localStorage.removeItem(LS_TIPS + k); } catch (e) {}
      });
      try { localStorage.removeItem(LS_TIPS + '_welcome'); } catch (e) {}
    },

    // ---------------- 标签页切换 ----------------
    activate(tab, opts) {
      if (!TABS[tab]) tab = 'doubao';
      document.querySelectorAll('.tab').forEach(function (b) {
        b.classList.toggle('active', b.dataset.tab === tab);
      });
      document.querySelectorAll('.pane').forEach(function (p) {
        p.classList.toggle('active', p.dataset.pane === tab);
      });
      try { history.replaceState(null, '', '#' + tab); } catch (e) {}
      if (tab === 'gallery') AIWS.loadGallery();
      if (tab === 'settings') AIWS.loadModels();
      AIWS.tabBadge(tab);
      AIWS.checkTabModels(tab);
    // 音乐工坊已改成原生实现，逻辑在 tab-music.js（它自己监听 aiws:tab 事件）
      // ★ 提示框要等标签页的进入动画走完再弹（不然两段动画叠在一起，很乱 ✗）
      //   定时器顺便做防抖：连点几个标签时，只有最后停下的那个会弹 ✓
      if (!opts || !opts.silent) {
        clearTimeout(AIWS._tipsTimer);
        AIWS._tipsTimer = setTimeout(function () {
          AIWS.showTips(tab, opts && opts.force);
        }, 560);
      }
      document.dispatchEvent(new CustomEvent('aiws:tab', { detail: { tab: tab } }));
    },

    /* 轻提示：右下角冒一条，2 秒后自己消失。没有就用 console。 */
    toast(msg) {
      try {
        var t = document.getElementById('aiwsToast');
        if (!t) {
          t = document.createElement('div');
          t.id = 'aiwsToast';
          t.style.cssText = 'position:fixed;right:22px;bottom:22px;z-index:10000;' +
            'background:rgba(24,32,45,.94);color:#fff;padding:11px 18px;border-radius:12px;' +
            'font-size:13.5px;font-weight:700;box-shadow:0 10px 30px rgba(0,0,0,.28);' +
            'opacity:0;transform:translateY(10px);transition:all .18s ease;pointer-events:none;' +
            'max-width:60vw';
          document.body.appendChild(t);
        }
        t.textContent = msg;
        t.style.opacity = '1';
        t.style.transform = 'translateY(0)';
        clearTimeout(t._timer);
        t._timer = setTimeout(function () {
          t.style.opacity = '0';
          t.style.transform = 'translateY(10px)';
        }, 2000);
      } catch (e) { }
    },

    initTabs() {
      document.querySelectorAll('.tab').forEach(function (b) {
        b.addEventListener('click', function () { AIWS.activate(b.dataset.tab); });
      });
      // ★ 标题区那行「下载模型去设置页」也能点着跳过去 ✓
      const _hd = document.getElementById('heroDlHint');
      if (_hd) _hd.addEventListener('click', function () { AIWS.activate('settings'); });

      // ★★ 左侧导航的分类可以**折叠**（2026-10-03 主人要求：做个小箭头能把分类隐藏 ✓）
      //   箭头是这里动态加上去的 ✓ 所以 index.html 不用改 ✓
      //   折叠状态记在 localStorage ✓ 下次打开还是收着的 ✓
      document.querySelectorAll('.sideGrp').forEach(function (gp, gi) {
        const t = gp.querySelector('.sideTitle');
        if (!t || t.dataset.fold === '1') return;
        t.dataset.fold = '1';
        const arrow = document.createElement('i');
        arrow.className = 'sideArrow';
        arrow.textContent = '▾';
        t.appendChild(arrow);
        const key = 'aiws.fold.' + gi;
        if (localStorage.getItem(key) === '1') gp.classList.add('collapsed');
        const toggle = async function (e) {
          e.preventDefault();
          e.stopPropagation();
          gp.classList.toggle('collapsed');
          const on = gp.classList.contains('collapsed');
          localStorage.setItem(key, on ? '1' : '0');
          t.title = on ? '点一下展开这一类' : '点一下收起这一类';
        };
        arrow.addEventListener('click', toggle);
        t.addEventListener('click', toggle);
        t.title = '点一下收起这一类';
      });
      const hash = (location.hash || '').replace('#', '');
      AIWS.activate(TABS[hash] ? hash : 'doubao');
    },

    // ---------------- 配置 ----------------
    async loadConfig() {
      const r = await AIWS.api('/api/config');
      AIWS.config = r.config;
      AIWS.applyConfig();
      return r.config;
    },

    applyConfig() {
      const c = AIWS.config || {};
      if (c.siteTitle) document.getElementById('siteTitle').innerHTML =
        AIWS.esc(c.siteTitle) + '<span class="sub">AI Workstation</span>';
      var sl1 = document.getElementById('siteSlogan');
      if (sl1) sl1.textContent = c.siteSlogan || '一切奇迹的起点';
      var sl2 = document.getElementById('siteSlogan2');
      if (sl2) sl2.textContent = c.siteSlogan2 || '与你相遇，便是奇迹';
      if (c.siteAuthor) document.getElementById('siteAuthor').textContent = c.siteAuthor;
      const ct = document.getElementById('siteContact');
      if (ct) ct.textContent = c.contact || '有问题加Q:3153180025';
      document.getElementById('akMask').textContent = c.apikeyMask || '(未设置)';
      const dsMask = document.getElementById('dsKeyMask');
      if (dsMask) dsMask.textContent = c.dsKeyMask || '(未设置)';
      const dsM = document.getElementById('dsModelInput');
      if (dsM && document.activeElement !== dsM) dsM.value = c.dsSearchModel || '';
      const dsB = document.getElementById('dsBaseInput');
      if (dsB && document.activeElement !== dsB) dsB.value = c.dsSearchBase || '';
      document.getElementById('dirInput').value = c.outputDir || '';
      document.getElementById('svcUrl').textContent = location.origin;
      const rd = document.getElementById('rootDir');
      if (rd) rd.textContent = c.root || '—';
      document.title = (c.siteTitle || '超级AI工作台') + ' · ' + (c.siteAuthor || '');
    },

    async saveConfig(patch, msgEl) {
      try {
        const r = await AIWS.api('/api/config', patch);
        AIWS.config = r.config;
        AIWS.applyConfig();
        if (msgEl) { msgEl.textContent = '已保存'; msgEl.className = 'status ok'; }
        return r.config;
      } catch (e) {
        if (msgEl) { msgEl.textContent = '保存失败：' + e.message; msgEl.className = 'status err'; }
        throw e;
      }
    },

    // ---------------- 作品库（汇总各标签页的产出，按来源分类）----------------
    galCat: 'all',

    async loadGallery(cat) {
      const box = document.getElementById('gallery');
      const empty = document.getElementById('galleryEmpty');
      const catsBox = document.getElementById('galCats');
      const pill = document.getElementById('galPill');
      const info = document.getElementById('galInfo');
      if (cat) AIWS.galCat = cat;
      const cur = AIWS.galCat || 'all';
      try {
        const r = await AIWS.api('/api/gallery?cat=' + encodeURIComponent(cur));
        if (!r.ok) {
          if (pill) { pill.textContent = '● 不可用'; pill.style.background = '#fdecec'; pill.style.color = '#a32020'; }
          box.innerHTML = '';
          empty.style.display = 'block';
          empty.textContent = r.error || '作品库读不到';
          return;
        }
        // 分类按钮
        if (catsBox) {
          catsBox.innerHTML = (r.cats || []).map(function (c) {
            const on = (c.key === cur) ? ' on' : '';
            const n = c.count ? ' <em>' + c.count + '</em>' : '';
            return '<button class="galCat' + on + '" data-cat="' + AIWS.esc(c.key) + '">' +
              c.icon + ' ' + AIWS.esc(c.name) + n + '</button>';
          }).join('');
          catsBox.querySelectorAll('.galCat').forEach(function (b) {
            b.addEventListener('click', function () { AIWS.loadGallery(b.dataset.cat); });
          });
        }
        if (pill) {
          pill.textContent = '● ' + (r.total || 0) + ' 件';
          pill.style.background = '#e8f7ee'; pill.style.color = '#177b3f';
        }
        if (info) {
          info.textContent = '图 ' + (r.imgCount || 0) + ' 张 · 音频 ' + (r.audCount || 0) + ' 个' +
            ((r.total > r.shown) ? '（只显示最近 ' + r.shown + ' 件）' : '');
        }
        // 刷新 / 打开目录（每轮重绑，幂等）
        const rf = document.getElementById('galRefresh');
        if (rf) rf.onclick = function () { AIWS.loadGallery(); };
        const op = document.getElementById('galOpen');
        if (op) op.onclick = function () { AIWS.galleryOpenFolder(r); };

        const items = r.items || [];
        if (!items.length) {
          box.innerHTML = '';
          empty.style.display = 'block';
          empty.textContent = '这一类还没有作品。';
          return;
        }
        empty.style.display = 'none';
        box.innerHTML = items.map(function (it) {
          const url = '/api/image?path=' + encodeURIComponent(it.file);
          if (it.kind === 'audio' || it.kind === 'other') {
            const ic = (it.kind === 'audio') ? '🎵' : '📄';
            return '<div class="galItem galFlat" data-file="' + AIWS.esc(it.file) +
              '" title="' + AIWS.esc(it.name) + '">' +
              '<div class="galIco">' + ic + '</div>' +
              '<div class="cap">' + AIWS.esc(it.name) + '<br>' +
              '<span class="muted">' + AIWS.esc(it.sizeText) + ' · ' + AIWS.esc(it.time) + '</span>' +
              '</div></div>';
          }
          return '<div class="galItem" data-file="' + AIWS.esc(it.file) + '" title="' +
            AIWS.esc(it.catName + (it.sub ? ' · ' + it.sub : '') + ' · ' + it.name) + '">' +
            '<img loading="lazy" src="' + url + '" alt="">' +
            '<div class="cap">' +
            (it.sub
              ? AIWS.esc(it.name.length > 20 ? it.name.slice(0, 20) + '…' : it.name)
              : it.icon + ' ' + AIWS.esc(it.catName)) +
            ' · ' + AIWS.esc(it.time) + '</div></div>';
        }).join('');
        box.querySelectorAll('.galItem').forEach(function (el) {
          el.addEventListener('click', function () {
            window.open('/api/image?path=' + encodeURIComponent(el.dataset.file), '_blank');
          });
        });
      } catch (e) {
        box.innerHTML = '';
        empty.style.display = 'block';
        empty.textContent = '读取失败：' + e.message;
      }
    },

    /* 打开「当前分类里第一件作品」所在的目录 */
    galleryOpenFolder(loaded) {
      const go = function (r) {
        const it = ((r || {}).items || [])[0];
        if (!it) { alert('这一类还没有作品'); return; }
        // 优先用后端给的「这一类的主目录」——抠图那种按任务号分子目录的，
        // 用文件的父目录会开到某一个任务文件夹里 ✗
        const dir = it.catDir || it.file.replace(/[\\/][^\\/]+$/, '');
        AIWS.api('/api/openfolder', { dir: dir })
          .then(function (x) { if (!x.ok) alert(x.error || '打不开'); })
          .catch(function (e) { alert('打不开：' + e.message); });
      };
      if (loaded) { go(loaded); return; }
      AIWS.api('/api/gallery?cat=' + encodeURIComponent(AIWS.galCat || 'all'))
        .then(go).catch(function (e) { alert('打不开：' + e.message); });
    },

    // ---------------- 本地模型（一键下载 + 官方哈希校验）----------------
    mdJob: '',
    mdJobs: {},          // ★ 每个组一个任务：{ jid: {group, jid} } —— 可同时下多个 ✓

    async loadModels() {
      const box = document.getElementById('mdList');
      const st = document.getElementById('mdStatus');
      if (!box) return;
      const rf = document.getElementById('mdRefresh');
      if (rf) rf.onclick = async function () { AIWS.loadModels(); };
      AIWS.loadEngines();          // 顺手把运行环境状态也拉一次 ✓
      try {
        const r = await AIWS.api('/api/models/status');
        if (!r.ok) {
          box.innerHTML = '';
          st.textContent = r.error || '读不到模型清单';
          st.className = 'status err';
          return;
        }
        box.innerHTML = (r.groups || []).map(function (g) {
          const items = (g.items || []).map(function (it) {
            return '<div class="mdItem"><span class="mdDot' + (it.installed ? ' on' : '') + '"></span>' +
              '<span class="mdFile" title="' + AIWS.esc(it.dest) + '">' + AIWS.esc(it.name) + '</span>' +
              '<span class="mdSize">' + AIWS.esc(it.installed ? it.sizeText : '未装') + '</span></div>';
          }).join('');
          let btn, tag;
          if (g.blocked) {
            btn = '<button class="mini" disabled>暂不可下</button>';
            tag = '<span class="mdTag warn">来源待查证</span>';
          } else if (g.count > 0 && g.have >= g.count) {
            btn = '<button class="mini" disabled>已装齐</button>';
            tag = '<span class="mdTag ok">✓ 齐全</span>';
          } else {
            btn = '<button class="mini primary" data-g="' + AIWS.esc(g.key) + '">⬇ 一键下载</button>';
            tag = '<span class="mdTag">' + g.have + '/' + g.count + '</span>';
          }
          return '<div class="mdRow" data-mdg="' + AIWS.esc(g.key) + '">' +
            '<div class="mdHead"><b>' + g.icon + ' ' + AIWS.esc(g.name) + '</b>' + tag + btn + '</div>' +
            '<div class="mdNote">' + AIWS.esc(g.note || '') +
            (g.blocked ? '<br><span class="mdWhy">' + AIWS.esc(g.blocked) + '</span>' : '') + '</div>' +
            (items ? '<div class="mdItems">' + items + '</div>' : '') +
            '<div class="mdJob"></div>' +          // ★ 点下载后，进度条 + 日志长在这里 ✓
            '</div>';
        }).join('');
        box.querySelectorAll('button[data-g]').forEach(function (b) {
          b.onclick = function () { AIWS.startModelInstall(b.dataset.g); };
        });
        st.textContent = '';
        st.className = 'status';
      } catch (e) {
        st.textContent = '读取失败：' + e.message;
        st.className = 'status err';
      }
    },

async loadEngines() {
      const box = document.getElementById('egList');
      const st = document.getElementById('egStatus');
      const rf = document.getElementById('egRefresh');
      if (rf) rf.onclick = async function () { AIWS.loadEngines(); };
      try {
        const r = await AIWS.api('/api/engine/status');
        if (!r.ok) { if (st) { st.textContent = r.error || '读不到运行环境状态'; st.className = 'status err'; } return; }
        const py = r.python || {};
        let html = '<div class="mdRow"><div class="mdHead"><b>🐍 Python 运行环境</b>' +
          (py.installed ? '<span class="mdTag ok">已就绪 ✓</span>' : '<span class="mdTag">未安装</span>') +
          '</div><div class="mdNote">' + (py.installed
            ? AIWS.esc(py.path) + '（' + AIWS.esc(py.version) + '）'
            : '一键装的时候会自动下载 python.org 官方安装包，并验签后才安装 ✓') +
          '</div></div>';
        (r.engines || []).forEach(function (e) {
          const dep = e.deps || {};
          const depTxt = Object.keys(dep).length
            ? Object.keys(dep).map(function (k) { return k + (dep[k] ? '✓' : '✗'); }).join(' ')
            : '（还没建虚拟环境）';
          html += '<div class="mdRow"><div class="mdHead"><b>' + AIWS.esc(e.name) + '</b>' +
            (e.ready ? '<span class="mdTag ok">✓ 就绪</span>'
                     : '<button class="mini primary" data-e="' + AIWS.esc(e.key) + '">⬇ 一键安装</button>') +
            '</div><div class="mdNote">虚拟环境：' + (e.venv ? '有 ✓' : '无 ✗') +
            '　官方源码：' + (e.src ? '有 ✓' : '无 ✗') +
            '<br>依赖：' + AIWS.esc(depTxt) + '</div></div>';
        });
        box.innerHTML = html;
        box.querySelectorAll('button[data-e]').forEach(function (b) {
          b.onclick = async function () { AIWS.startEngineInstall(b.dataset.e); };
        });
        if (st) { st.textContent = r.ready ? '两个引擎都就绪了 ✓' : ''; st.className = 'status'; }
      } catch (e) {
        if (st) { st.textContent = '读取失败：' + e.message; st.className = 'status err'; }
      }
    },

    async startEngineInstall(engine) {
      const st = document.getElementById('egStatus');
      const prog = document.getElementById('egProg');
      const logBox = document.getElementById('egLog');
      try {
        const r = await AIWS.api('/api/engine/install', { engine: engine });
        if (!r.ok) { if (st) { st.textContent = r.error || '启动失败'; st.className = 'status err'; } return; }
        AIWS.egJob = r.job;
        if (st) {
          st.textContent = '已开始安装 —— 第一步下载官方安装包，然后会验证官方签名；' +
            'torch 那步最久（2~3 GB），可以先去干别的，别关窗口 ✓';
          st.className = 'status warn';
        }
        if (prog) prog.style.display = 'block';
        if (logBox) logBox.textContent = '';
        AIWS.pollEngine();
      } catch (e) {
        if (st) { st.textContent = '启动失败：' + e.message; st.className = 'status err'; }
      }
    },

    pollEngine() {
      if (!AIWS.egJob) return;
      AIWS.api('/api/engine/progress?job=' + encodeURIComponent(AIWS.egJob)).then(function (r) {
        const j = r.job || {};
        const bar = document.getElementById('egBar');
        const txt = document.getElementById('egText');
        const st = document.getElementById('egStatus');
        const logBox = document.getElementById('egLog');
        if (bar) bar.style.width = (j.percent || 0) + '%';
        if (txt) txt.textContent = (j.stage || '') + (j.detail ? '　' + j.detail : '') +
          '　（' + (j.elapsed || 0) + ' 秒）';
        if (logBox && j.logText) logBox.textContent = j.logText;
        if (j.state === 'done' || j.state === 'failed') {
          if (st) {
            st.textContent = (j.state === 'done' ? '完成：' : '失败：') + (j.detail || '');
            st.className = 'status ' + (j.state === 'done' ? 'ok' : 'err');
          }
          AIWS.egJob = null;
          AIWS.loadEngines();
          return;
        }
        setTimeout(function () { AIWS.pollEngine(); }, 2500);
      }).catch(function () { setTimeout(async function () { AIWS.pollEngine(); }, 4000); });
    },

    async startModelInstall(group) {
      const st = document.getElementById('mdStatus');
      const row = document.querySelector('.mdRow[data-mdg="' + group + '"]');
      if (row && row.dataset.busy === '1') return;          // 同一组别重复点 ✓
      try {
        const r = await AIWS.api('/api/models/install', { group: group });
        if (!r.ok) {
          if (st) { st.textContent = r.error || '启动失败'; st.className = 'status err'; }
          return;
        }
        // ★★ 点完：**按钮消失** ✓ 本组下面**长出进度条 + 日志** ✓
        //   各组用各自的 job / 各自的进度条 → **可以同时下多个** ✓ 互不干扰 ✓
        if (row) {
          row.dataset.busy = '1';
          const btn = row.querySelector('button[data-g]');
          if (btn) btn.remove();                             // 按钮消失 ✓
          const slot = row.querySelector('.mdJob');
          if (slot) {
            slot.innerHTML =
              '<div class="progress" style="margin-top:10px"><div class="bar">' +
              '<span class="jbar" style="width:0%"></span></div>' +
              '<div class="progText jtxt">已开始下载…</div></div>' +
              '<div class="status warn jst">大模型要一会儿 —— ' +
              '下完会自动跟官方哈希逐字节比对，对不上就删掉 ✗</div>' +
              '<details style="margin-top:8px"><summary class="muted" style="cursor:pointer">' +
              '下载日志</summary><pre class="logBox jlog">（刚开始）</pre></details>';
          }
          AIWS.mdJobs[r.job] = { group: group };
          AIWS.pollMdJob(r.job, row);
          if (st) {
            st.textContent = '有 ' + Object.keys(AIWS.mdJobs).length + ' 组正在下载 —— 可以同时下别的组 ✓';
            st.className = 'status warn';
          }
        }
      } catch (e) {
        if (st) { st.textContent = '启动失败：' + e.message; st.className = 'status err'; }
      }
    },

    // ★ 只更新**本组那一块** ✓（所以多组能同时下 ✓）
    pollMdJob(jid, row) {
      const tick = async function () {
        if (!document.body.contains(row)) return;            // 列表被重画了 → 停 ✓
        let j;
        try {
          j = await AIWS.api('/api/models/progress?job=' + encodeURIComponent(jid));
        } catch (e) { setTimeout(tick, 2500); return; }
        const bar = row.querySelector('.jbar');
        const txt = row.querySelector('.jtxt');
        const st = row.querySelector('.jst');
        const lg = row.querySelector('.jlog');
        if (bar) bar.style.width = (j.percent || 0) + '%';
        if (txt) txt.textContent = (j.stage || '') + (j.detail ? '　·　' + j.detail : '') +
          '　·　' + (j.percent || 0) + '%';
        if (lg && j.log) lg.textContent = j.log.slice(-60).join('\n');
        if (j.state === 'running') { setTimeout(tick, 1200); return; }
        if (bar) bar.style.width = '100%';
        if (st) {
          st.textContent = (j.state === 'done')
            ? '✓ 装好了（全部通过官方哈希校验）'
            : '✗ 没成功：' + (j.error || '看下面日志');
          st.className = 'status ' + (j.state === 'done' ? 'ok' : 'err');
        }
        delete AIWS.mdJobs[jid];
        // ★★ 只在**成功**时刷新清单 ✓
        //   失败时**坚决不刷** ✗ —— 一刷就把错误和日志冲没了，主人根本看不到为什么失败 ✗
        //   （2026-10-03 踩过：虚拟机上下载失败后，进度条变回"一键下载"，日志全丢 ✗）
        if (j.state === 'done') {
          if (st) st.textContent += '　（正在刷新清单…）';
          if (!Object.keys(AIWS.mdJobs).length) {
            setTimeout(function () { AIWS.loadModels(); }, 1500);
          }
        } else if (st) {
          // 失败：留一个「重试」按钮 ✓ 点它重新下 —— **会接着下** ✓ 已下的不白费 ✓
          var again = document.createElement('button');
          again.className = 'mini';
          again.style.marginLeft = '10px';
          again.textContent = '↻ 重试（接着下 ✓ 已下的不白费）';
          again.onclick = function () {
            st.textContent = '重新开始…';
            AIWS.startModelInstall(row.dataset.mdg || row.getAttribute('data-mdg'));
          };
          st.appendChild(again);
          var tip = document.createElement('div');
          tip.className = 'uiHint';
          tip.style.marginTop = '6px';
          tip.textContent = '① 日志就在上面，展开看看卡在哪 ✓　② 直接点重试也能接着下 ✓';
          st.parentNode.insertBefore(tip, st.nextSibling);
        }
      };
      tick();
    },

    pollModels() {
      const jid = AIWS.mdJob;
      if (!jid) return;
      const bar = document.getElementById('mdBar');
      const txt = document.getElementById('mdText');
      const st = document.getElementById('mdStatus');
      const logBox = document.getElementById('mdLog');
      const tick = async function () {
        if (AIWS.mdJob !== jid) return;
        try {
          const j = await AIWS.api('/api/models/progress?job=' + encodeURIComponent(jid));
          if (bar) bar.style.width = (j.percent || 0) + '%';
          if (txt) txt.textContent = (j.stage || '') + '　' + (j.detail || '');
          if (logBox && j.log) logBox.textContent = j.log.slice(-40).join('\n');
          if (j.state === 'done') {
            st.textContent = '✓ 装好了（全部通过官方哈希校验）';
            st.className = 'status ok';
            AIWS.loadModels();
            return;
          }
          if (j.state === 'failed') {
            st.textContent = '✗ 没成功 —— 看下面的日志找原因';
            st.className = 'status err';
            AIWS.loadModels();
            return;
          }
          setTimeout(tick, 1500);
        } catch (e) {
          setTimeout(tick, 2500);
        }
      };
      tick();
    },

    /* 每个需要模型的标签页：切过去时检查一下，缺就顶上冒一条**提示** ✓
       （★ 2026-10-03 起：这里**不带下载按钮** ✗，只提示 + 跳到「设置」页 ✓） */
    /* 每个标签页是「本地跑」还是「调 API」——这张表说了算 ✓ */
    TAB_KIND: {
      dsh: ['API', '走 DeepSeek 云端，按字数计费'],
      search: ['API', '走 DeepSeek 云端，按字数计费'],
      doubao: ['API', '走火山方舟云端，按张计费'],
      qwen: ['本地', '用你自己电脑的显卡跑，不联网、不花钱'],
      matting: ['本地', '纯本地跑，核显就够'],
      blend: ['本地', '方案 A 纯 CPU；方案 B 用显卡'],
      music: ['本地', '纯本地跑，需要独立显卡'],
      stem: ['本地', '纯本地跑，需要独立显卡'],
      cover: ['本地', '纯本地跑，需要独立显卡'],
      tts: ['本地', '纯本地跑，需要独立显卡'],
      toolbox: ['本地', '系统小工具，不联网'],
      vgen: ['API', '走智谱云端，免费视频生成'],
      live2d: ['本地', '拆层可选本地引擎或在线官方演示；建模导出全部在本机'],
      gallery: ['本地', '看本机的出片文件'],
      settings: ['本地', '工作站设置'],
    },

    /* 在标签页顶部插一行小字，标出来源（本地 / API）*/
    tabBadge(tab) {
      const pane = document.querySelector('.pane[data-pane="' + tab + '"]');
      if (!pane) return;
      const k = AIWS.TAB_KIND[tab];
      let bar = pane.querySelector('.kindBar');
      if (!k) { if (bar) bar.remove(); return; }
      if (!bar) {
        bar = document.createElement('div');
        bar.className = 'kindBar';
        pane.insertBefore(bar, pane.firstChild);
      }
      bar.innerHTML = '<span class="kindTag' + (k[0] === 'API' ? ' api' : '') + '">' +
        k[0] + '</span><span class="kindTxt">' + AIWS.esc(k[1]) + '</span>';
    },

    async checkTabModels(tab) {
      const pane = document.querySelector('.pane[data-pane="' + tab + '"]');
      if (!pane) return;
      try {
        const r = await AIWS.api('/api/models/status');
        if (!r.ok) return;
        const g = (r.groups || []).find(function (x) { return x.key === tab; });
        let bar = pane.querySelector('.mdBar');
        // 本机齐了就不冒条 —— 有没有下载源跟「缺不缺」无关 ✓
        //（踩过：写成 !g.blocked && 齐全，结果本机明明有模型还一直冒提示 ✗）
        if (!g || (g.count > 0 && g.have >= g.count)) {
          if (bar) bar.remove();
          return;
        }
        if (!bar) {
          bar = document.createElement('div');
          bar.className = 'mdBar';
          pane.insertBefore(bar, pane.firstChild);
        }
        if (g.blocked) {
          bar.className = 'mdBar warn';
          bar.innerHTML = '<span class="mdBarIcon">📦</span>' +
            '<span class="mdBarTxt"><b>' + g.icon + ' ' + AIWS.esc(g.name) + '</b>' +
            (g.count ? ' 还差 ' + (g.count - g.have) + ' 个模型（共 ' + g.count + ' 个）'
                     : ' 的模型清单还没补齐') +
            '，而且暂时没有正规下载源：' + AIWS.esc(g.blocked) + '</span>';
          return;
        }
        bar.className = 'mdBar';
        // ★ 2026-10-03 主人要求：**所有模型下载统一收进「设置」页** ✓
        //   每个标签页顶部只留**提示 + 跳转**，不再就地下载 ✓（避免多点开花、也避开原生窗口的问题 ✓）
        bar.innerHTML = '<span class="mdBarIcon">📦</span>' +
          '<span class="mdBarTxt"><b>' + g.icon + ' ' + AIWS.esc(g.name) + '</b> 还差 ' +
          (g.count - g.have) + ' 个模型（共 ' + g.count + ' 个）' +
          (g.note ? '　' + AIWS.esc(g.note) : '') +
          '　→ <b>下载请到「⚙️ 设置」页</b></span>' +
          '<button class="mini">去设置页下载 →</button>';
        const btn = bar.querySelector('button');
        if (btn) btn.onclick = async function () {
          const sb = document.querySelector('[data-tab="settings"]');
          if (sb) sb.click();                     // 只跳转，不下载 ✓
        };
      } catch (e) { /* 提示条而已，出错就静默，别打扰用的人 */ }
    },

    // ---------------- 健康检查 ----------------
    async health() {
      const box = document.getElementById('svcText');
      const wrap = document.querySelector('.hero-status');
      try {
        const r = await AIWS.api('/api/health');
        box.textContent = '服务正常 · 端口 ' + r.port;
        wrap.classList.add('up'); wrap.classList.remove('down');
      } catch (e) {
        box.textContent = '服务未响应';
        wrap.classList.add('down'); wrap.classList.remove('up');
      }
    },

    // ---------------- 设置页 ----------------
    initSettings() {
      document.getElementById('akSave').addEventListener('click', async function () {
        const v = document.getElementById('akInput').value.trim();
        const st = document.getElementById('status');
        if (!v) { alert('先粘贴 API Key'); return; }
        await AIWS.saveConfig({ apikey: v }, st);
        document.getElementById('akInput').value = '';
        alert('API Key 已保存（本机 data/config.json）');
      });
      document.getElementById('dsKeySave').addEventListener('click', async function () {
        const v = document.getElementById('dsKeyInput').value.trim();
        if (!v) { alert('先粘贴 DeepSeek 的 API Key'); return; }
        await AIWS.saveConfig({
          dsKey: v,
          dsSearchModel: document.getElementById('dsModelInput').value.trim(),
          dsSearchBase: document.getElementById('dsBaseInput').value.trim()
        });
        document.getElementById('dsKeyInput').value = '';
        alert('DeepSeek API Key 已保存（本机 data/config.json）\n「🔍 AI 搜索引擎」现在就能用了 ✓');
      });
      document.getElementById('dsKeyClear').addEventListener('click', async function () {
        if (await AIWS.no('确定清掉已保存的 DeepSeek API Key？')) return;
        await AIWS.saveConfig({ clearDsKey: true });
        alert('已清除');
      });
      document.getElementById('dirSave').addEventListener('click', async function () {
        await AIWS.saveConfig({ outputDir: document.getElementById('dirInput').value.trim() });
        alert('输出目录已保存');
      });
      document.getElementById('dirOpen').addEventListener('click', async function () {
        const r = await AIWS.api('/api/openfolder', { dir: document.getElementById('dirInput').value.trim() });
        if (!r.ok) alert(r.error || '打不开');
      });
      document.getElementById('resetTips').addEventListener('click', function () {
        AIWS.resetTips();
        alert('已重置。下次切到各标签页时会重新弹说明。');
      });
      document.getElementById('quitBtn').addEventListener('click', async function () {
        if (await AIWS.no('确定关闭后台服务吗？关掉后这个页面就不能用了。')) return;
        await AIWS.api('/api/quit', {});
        document.body.innerHTML = '<div style="padding:80px;text-align:center;font-size:20px;font-weight:700;color:#11406e">' +
          '服务已关闭。重新双击「启动AI工作站」即可再打开。</div>';
      });
    },

    // ---------------- ★ 一键关掉所有后端（2026-10-02） ----------------
    initKillAll() {
      const b = document.getElementById('killAllBackends');
      if (!b || b.__bound) return;
      b.__bound = true;
      b.onclick = async function () {
        if (await AIWS.no('确定关掉所有后端吗？\n\n' +
          '会停掉：\n· ComfyUI（本地千问 / 抠图 / 溶图 的引擎）\n· AI 音乐工坊\n· 小鲸鱼生图\n· PSD2Live\n\n' +
          '工作站本身和 DSH 不受影响，要用时按需再启动即可。')) return;
        b.disabled = true;
        b.textContent = '⏻ 正在关…';
        try {
          const r = await AIWS.api('/api/backends/stop-all', {});
          const lines = (r.items || []).map(function (it) {
            const st = it.killed ? '已停止' : (it.note || '没在跑');
            return '· ' + it.name + '：' + st;
          });
          const mf = r.mem && r.mem.freedMb != null ? '内存 +' + (r.mem.freedMb / 1024).toFixed(2) + ' GB' : '内存 —';
          const vf = r.vram && r.vram.freedMb != null ? '，显存 +' + (r.vram.freedMb / 1024).toFixed(2) + ' GB' : '';
          alert('已关掉所有后端 ✓\n\n' + mf + vf + '\n\n' + lines.join('\n'));
          b.classList.add('done');
          b.textContent = '✓ 后端已关';
          setTimeout(async function () {
            b.classList.remove('done'); b.textContent = '⏻ 关掉所有后端'; b.disabled = false;
          }, 6000);
          AIWS.health();
        } catch (e) {
          alert('关失败了：' + (e.message || e));
          b.textContent = '⏻ 关掉所有后端';
          b.disabled = false;
        }
      };
    },

    /* ---------------------------------------------------------------
       ★ 功能注册表（服务端 features.py = 唯一事实来源）
          · 补 TABS / TAB_KIND —— 新标签不会因为没兜底而被弹回首页 ✓
          · 导航里缺按钮 → 自动补一个（免得忘了改 index.html）
          · 有按钮却没登记 → 控制台报警（这种最坑：点进去被弹回首页 ✗）
       --------------------------------------------------------------- */
    async applyFeatureRegistry() {
      try {
        const r = await AIWS.api('/api/features');
        if (!r || !r.ok || !r.nav || !r.nav.length) return;
        AIWS.features = r;
        const known = {};
        r.nav.forEach(function (f) {
          known[f.id] = true;
          if (!TABS[f.id]) {
            TABS[f.id] = {
              icon: f.icon, title: f.name,
              tipsTitle: f.name + ' · 使用说明',
              tipsBody: '<p>' + AIWS.esc(f.kindNote || '') + '</p>'
            };
            console.warn('[features] TABS 缺 ' + f.id + ' → 已按注册表兜底');
          }
          AIWS.TAB_KIND[f.id] = [f.kind, f.kindNote || ''];
        });
        const bar = document.getElementById('tabs');
        if (bar) {
          const rows = bar.querySelectorAll('.tabRow');
          const row = rows.length ? rows[rows.length - 1] : bar;
          const before = bar.querySelector('[data-tab="settings"]');
          r.nav.forEach(function (f) {
            if (bar.querySelector('[data-tab="' + f.id + '"]')) return;
            const b = document.createElement('button');
            b.className = 'tab';
            b.setAttribute('data-tab', f.id);
            b.innerHTML = '<i>' + f.icon + '</i><span>' + AIWS.esc(f.name) + '</span>';
            row.insertBefore(b, (before && before.parentNode === row) ? before : null);
            console.warn('[features] 导航缺 ' + f.id + ' → 已自动补上（顺手补 index.html 更整齐）');
          });
          Array.prototype.forEach.call(bar.querySelectorAll('[data-tab]'), async function (b) {
            const id = b.getAttribute('data-tab');
            if (!known[id]) {
              console.warn('[features] 按钮 ' + id + ' 没在 features.py 里登记 ✗ 点它会跳回首页');
            }
          });
        }
      } catch (e) {
        console.warn('[features] 读注册表失败（用内置兜底继续）：', e && e.message);
      }
    },

    // ---------------- 启动 ----------------
    async boot() {
      await AIWS.applyFeatureRegistry();   // ★ 先拉注册表，再把标签补齐
      AIWS.initTips();
      AIWS.initTabs();
      AIWS.showWelcome();     // ★ 打开网页就弹（盖在各标签说明之上）
  AIWS.initGlobalDrop();       // 全站拖拽上传
      AIWS.initSettings();
      AIWS.initKillAll();        // ★ 一键关后端
      await AIWS.health();
      try { await AIWS.loadConfig(); } catch (e) { console.error(e); }
      setInterval(AIWS.health, 20000);
      document.dispatchEvent(new CustomEvent('aiws:ready'));
    }
  };

  window.AIWS = AIWS;
  document.addEventListener('DOMContentLoaded', AIWS.boot);
})();
