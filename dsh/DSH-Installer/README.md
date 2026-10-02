# DSH 全自动安装包（Windows）

> 一键装好 **DeepSeek Harness (`@deepseek-ai/dsh`)**：自动下载安装 Node.js → 执行 `npm install -g @deepseek-ai/dsh` → 在安装好的包里注入 **dsh web 启动器** → 建好桌面/开始菜单快捷方式。
> 全程图形界面操作，蓝色渐变界面，打开时先弹出 **DeepSeek 开放平台 API 密钥购买指引**。

---

## 1. 包里有什么

| 文件 | 作用 |
| --- | --- |
| `START-HERE.txt` | **先看这个**：给收包人的 3 步速览（解压 → 双击 → 启动） |
| `Install-DSH.cmd` | **入口，双击这个文件**（负责用正确参数拉起图形界面脚本） |
| `Install-DSH.ps1` | 主安装脚本（蓝色渐变 WPF 界面 + 全部安装逻辑） |
| `Check-DSH.ps1` | 环境自检脚本（只读检查，不安装、不修改任何东西） |
| `launcher\dsh-web.cmd` | 独立的 `dsh web` 启动器（手动备用版，正式启动器由安装脚本注入） |
| `launcher\dsh-web-npx.cmd` | 用官方 `npx @deepseek-ai/dsh web` 方式启动（不想全局安装时用） |
| `README.md` | 本说明 |

运行环境：Windows 10 / 11（64 位），系统自带 Windows PowerShell 5.1，**不需要**预装任何东西（Node.js 由脚本自己下载安装）。

把 zip 发给别人时提醒对方两件事：**先完整解压再运行**；如果是从微信/QQ 收到的，解压前右键 zip →「属性」勾选「解除锁定」（安装脚本和 cmd 入口也会自动尝试解除）。

---

## 2. 快速开始

1. 把整个文件夹解压到任意位置（例如桌面）。
2. 双击 **`Install-DSH.cmd`**（会弹出一个最小化的黑窗口 + 蓝色渐变安装界面）。
   - 想用「标准安装(MSI)」方式装 Node.js，会弹 **UAC 管理员授权**，点「是」即可；也可以直接在界面里选「便携安装(ZIP)」，就完全不需要管理员权限。
3. 先看完弹出的 **API 密钥购买提示**（见下一节），点「我已了解，继续安装」。
4. 等进度条走完（首次一般 2~6 分钟，取决于网速），点 **「启动 DSH Web」**，浏览器会自动打开 DSH 网页界面。

> 安装日志保存在 `%LOCALAPPDATA%\DSH-Installer\install-*.log`，界面右下角有「打开安装日志」按钮。

---

## 2.5 关于 `npm install -g @deepseek-ai/dsh` 是不是官方的

**结论：包是 DeepSeek 官方的，命令写法不是官方文档推荐的那一种，但效果等价。**

- `@deepseek-ai/dsh` 是官方包：npm 上的维护者是 DeepSeek 团队账号（`tianyi@deepseek.com`），仓库/主页指向官方仓库 <https://github.com/deepseek-ai/deepseek-harness>，许可证 MIT；官方 API 文档 <https://api-docs.deepseek.com/zh-cn/> 的「接入 Agent 工具 → DeepSeek Harness」也指向这个项目，文档站为 <https://deepseek-harness.github.io/deepseek-harness/>。
- 官方 README 给出的启动方式是 **`npx @deepseek-ai/dsh web`**（不全局安装，npx 临时下载官方包运行），也支持从源码 `pnpm dsh web`。
- 图片里的 **`npm install -g @deepseek-ai/dsh`** 是把这个**同一个官方包全局装到本机**，装完就多出 `dsh` 命令，然后 `dsh web` 启动——本安装脚本用的就是这条（按你的要求），并额外做了版本校验、镜像可选、启动器注入等。
- 需要注意的只是：DeepSeek Harness 目前是**开发者预览版**，版本号还是 `0.1.x-rc.x`，官方明确说会有破坏性变更；升级就重跑一遍安装脚本或 `npm install -g @deepseek-ai/dsh@latest`。
- 顺带一提：任何从图片/聊天里抄来的 npm 命令，装之前都要逐字核对包名（`@deepseek-ai/dsh`，不是 `@deepseekai/`、`@deep-seek-ai/` 之类），这正是 npm 上常见的仿冒手法。这个包名是核对无误的官方包名。

---

## 3. 先准备好 DeepSeek API 密钥（重要）

DSH 本体**不包含**任何密钥，它需要调用 DeepSeek API 才能对话。密钥在 DeepSeek 开放平台购买/申请，**5 步、约 2 分钟**：

| 步骤 | 操作 |
| --- | --- |
| **① 打开并登录** | 浏览器访问 <https://platform.deepseek.com/> ，支持**手机号验证码**或**微信扫码**登录；没有账号时登录即自动注册，不需要单独填资料。 |
| **② 充值（就是“购买 API 额度”）** | 登录后点左侧菜单 **「充值」** → 选金额（¥10 / ¥20 / ¥50 或自定义）→ 选 **支付宝** 或 **微信支付** → 点 **「去支付」**。充值立即到账，可在左侧 **「用量信息」** 核对余额。 |
| **③ 创建密钥** | 点左侧菜单 **「API keys」** → 点右上角 **「创建 API key」** → 起个名字 → 确定。 |
| **④ 立刻复制** | 弹窗里会出现一串以 **`sk-`** 开头的字符，点 **「复制」** 并先存到安全的地方（密码管理器 / 本地记事本）。**关闭弹窗后就再也看不到完整密钥了**。 |
| **⑤ 填进 DSH** | 两种方式任选：<br>• 安装完成后打开 DSH 网页，在 **「设置 → 模型（Models）」** 页粘贴密钥；<br>• 或者直接在本安装界面的 **「API 密钥」** 输入框里粘贴，脚本会帮你写入用户环境变量 `DEEPSEEK_API_KEY`。 |

### 计费与排错小贴士

- **按 token 计费**，不同模型价格不同，官方价格页：<https://api-docs.deepseek.com/zh-cn/quick_start/pricing>
- **网页版 / App 里的免费聊天不消耗 API 余额**，两者相互独立；充值只用于 API 调用。
- 常见报错：
  - `401 Authentication Fails` → 密钥填错、没保存，或环境变量没生效（重开窗口）；
  - `402 Insufficient Balance` → 余额不足，回第 ② 步充值；
  - `429 Too Many Requests` → 请求过于频繁，稍后再试。
- **安全**：密钥等同密码。不要发到群里、不要提交到 GitHub、不要截图外发；怀疑泄露就立刻到「API keys」页删除并新建一个。单个账号最多保留 100 个密钥，建议按用途分别创建，方便单独吊销。

---

## 4. 安装脚本到底做了什么

界面里的 7 个步骤，全部自动：

1. **环境检查** —— 系统位数、CPU 架构(x64/ARM64)、磁盘剩余空间、是否管理员。
2. **获取 Node.js 版本** —— 读 <https://nodejs.org/dist/index.json> 取官方**最新 LTS**（取不到就用脚本内置的兜底版本 `v24.21.0`；也可以在界面里手填版本）。
3. **下载安装包** —— 从 `nodejs.org` 下载 `node-<版本>-x64.msi`（或便携版 zip），并下载官方 `SHASUMS256.txt` **做 SHA256 校验**，防止文件被篡改；已下载且校验一致时会跳过重复下载。
4. **安装 Node.js** —— MSI 静默安装（`msiexec /qn`）；如果失败或没有管理员权限，**自动回退**为解压便携版到 `%LOCALAPPDATA%\Programs\nodejs`。随后把 Node 目录和 npm 全局目录 `%APPDATA%\npm` 补进用户 PATH。
5. **安装 DSH** —— 执行图片里的命令：`npm install -g @deepseek-ai/dsh`（额外加了 `--no-fund --no-audit` 让输出干净些；勾选「用国内镜像加速」时会附加 `--registry https://registry.npmmirror.com`）。
6. **验证** —— 确认包目录存在、读出版本号，并执行一次 `dsh --help` 冒烟测试（只打印帮助，不启动服务）。
7. **注入 `dsh web` 启动器** —— 见下一节；可选把 API 密钥写入用户环境变量 `DEEPSEEK_API_KEY`。

在线安装的完整程序包（脚本本身不会执行任何其他命令，也不会联网上传任何数据）。

---

## 5. 装完之后：`dsh web` 启动器

安装脚本会把启动器**塞进 DSH 包里**，一共有三处：

```
%APPDATA%\npm\node_modules\@deepseek-ai\dsh\dsh-web.cmd   ← 包内启动器（+ 同名中文说明 txt）
%APPDATA%\npm\dsh-web.cmd                                 ← npm 全局目录里的启动器
桌面「DSH Web」快捷方式 / 开始菜单「DSH」文件夹           ← 双击即用
```

启动 DSH Web 的三种方式：

1. 双击桌面 **「DSH Web」** 快捷方式（最省事）；
2. 双击 `%APPDATA%\npm\dsh-web.cmd`；
3. 命令行执行 `dsh web`（默认端口 **3080**）。

如果全局的 `dsh` 命令出问题（或你压根不想全局安装），用包里的 `launcher\dsh-web-npx.cmd`：它执行的是官方文档的写法 `npx -y @deepseek-ai/dsh web`，不依赖全局安装，只要求机器上有 Node.js。

细节：

- 启动器默认执行 `dsh web --port 3080`，**浏览器会自动打开带授权令牌的地址**。手动在浏览器里输 `http://127.0.0.1:3080` 是打不开的，请用自动打开的那个地址。
- **改端口**：用记事本打开 `dsh-web.cmd`，改 `set "DSH_PORT=3080"` 这一行即可。
- **传其它参数**：`dsh-web.cmd --port 8080`、`dsh-web.cmd --no-open`（不自动开浏览器）等，参数会原样传给 `dsh web`。
- **关闭启动器的窗口 = 停止 DSH 服务**（服务是前台进程）。
- 启动器自身的提示文字是英文（避免 Windows 代码页乱码），DSH 本体界面仍是中文。

---

## 6. 命令行 / 静默安装

```powershell
# 图形界面（等同于双击 cmd）
powershell -NoProfile -STA -ExecutionPolicy Bypass -File .\Install-DSH.ps1

# 静默安装（无界面，日志打到控制台，同样会写日志文件）
powershell -NoProfile -STA -ExecutionPolicy Bypass -File .\Install-DSH.ps1 -Silent

# 免管理员：使用便携版 Node.js
powershell -NoProfile -STA -ExecutionPolicy Bypass -File .\Install-DSH.ps1 -NodeInstallMode Zip

# 指定 Node 版本 / 端口 / 顺带写入 API 密钥 / 用国内镜像
powershell -NoProfile -STA -ExecutionPolicy Bypass -File .\Install-DSH.ps1 `
  -NodeVersion v24.21.0 -Port 8080 -ApiKey "sk-你的密钥" -SaveApiKey -UseNpmMirror
```

| 参数 | 说明 |
| --- | --- |
| `-NodeInstallMode Msi\|Zip` | 标准安装（默认，需管理员）/ 便携安装（免管理员） |
| `-NodeVersion v24.21.0` | 指定 Node.js 版本；留空 = 自动取最新 LTS |
| `-Port 3080` | 写进启动器的监听端口 |
| `-ApiKey` / `-SaveApiKey` | 密钥 / 写入用户环境变量 `DEEPSEEK_API_KEY` |
| `-UseNpmMirror` | npm 走 `registry.npmmirror.com` 加速（镜像同步可能滞后，失败就关掉） |
| `-Silent` | 无界面 |
| `-SkipApiNotice` | 跳过启动时的购买提示弹窗 |
| `-NoShortcuts` | 不创建桌面 / 开始菜单快捷方式 |
| `-WorkRoot <路径>` | 下载缓存与日志目录（默认 `%LOCALAPPDATA%\DSH-Installer`） |

---

## 7. 常见问题

**Q: 双击 `Install-DSH.cmd` 一闪而过 / 没反应？**
A: 先看日志 `%LOCALAPPDATA%\DSH-Installer\install-*.log`；再运行 `Check-DSH.ps1` 自检。若系统禁止运行脚本，请用 `Install-DSH.cmd`（它内部已带 `-ExecutionPolicy Bypass`），不要直接双击 `.ps1`。

**Q: 微信/QQ 收到的 zip，解压后双击没反应，或被 SmartScreen / 杀毒软件拦截？**
A: 这是 Windows 对“来自网络的文件”的锁定标记（MOTW）。右键 zip →「属性」→ 勾选「解除锁定」→ 再解压；或者解压后在该文件夹执行一次 `Get-ChildItem -Recurse | Unblock-File`。`Install-DSH.cmd` 和脚本本身也会自动尝试解除该目录下所有文件的锁定。出现「Windows 已保护你的电脑」时点「更多信息 → 仍要运行」。

**Q: npm 安装很慢或超时？**
A: 在界面里勾选「用国内镜像加速」，或用命令行加 `-UseNpmMirror`；公司网络需要代理时，先执行 `npm config set proxy http://用户:密码@代理:端口` 再重试。

**Q: 已经装过 Node.js 了，会不会冲突？**
A: 不会。脚本会重新装一份官方 MSI（同版本会走“已安装”流程）或便携版，然后重新解析 `node.exe` / `npm.cmd` 路径；已有版本比 LTS 新也能正常使用。

**Q: 提示 `402 Insufficient Balance`？**
A: 账户余额不足，去 <https://platform.deepseek.com/> 左侧「充值」付款，充值立即到账。

**Q: 提示 `401 Authentication Fails`？**
A: 密钥不对或没生效。若用环境变量写入的密钥，**需要新开一次 DSH Web**（已开的窗口读不到新变量）；也可以直接到 DSH 网页「设置 → 模型」里重新粘贴。

**Q: 黑窗口关掉后网页就打不开了？**
A: DSH 的服务是前台进程，属于正常行为。想常驻可以配合「任务计划程序」或 Windows Terminal 后台运行。

**Q: 想彻底卸载？**
A: ① 卸载 Node.js（设置 → 应用）；② 删除 `%APPDATA%\npm\node_modules\@deepseek-ai\dsh`、`%APPDATA%\npm\dsh*.cmd`、桌面「DSH Web」快捷方式、开始菜单「DSH」文件夹；③ 删除 `%LOCALAPPDATA%\DSH-Installer` 与 `%USERPROFILE%\.dsh`（DSH 的配置与会话）。只卸载 DSH 包的话，执行 `npm uninstall -g @deepseek-ai/dsh` 即可。

---

## 8. 安全与免责

- 脚本只做三件事：从 `nodejs.org` 下载 Node.js 安装包、执行 `npm install -g @deepseek-ai/dsh`、往 DSH 包目录和快捷方式写入 `dsh web` 启动器；可选把 API 密钥写入**本机用户环境变量**。
- 不收集、不上传任何信息；密钥不会出现在日志里（日志只记录前 6 位 + 长度）。
- 下载的 Node.js 安装包会用官方 `SHASUMS256.txt` 做 SHA256 校验。
- 请从官方渠道获取 DSH 说明与更新：<https://deepseek-harness.github.io/deepseek-harness/guide/quickstart> ；DeepSeek API 文档：<https://api-docs.deepseek.com/zh-cn/>

---

*安装包版本 1.0.0 · 仅用于 Windows 64 位系统*
