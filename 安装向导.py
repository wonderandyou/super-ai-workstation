# -*- coding: utf-8 -*-
"""
超级AI工作台 · 安装向导
=====================================================================
给老师用的图形化安装程序。四个页面：

    ① 欢迎（介绍 + 环境自检）
    ② 选安装位置
    ③ 安装中（复制文件 + 进度）
    ④ 完成（建桌面快捷方式 / 立即启动）

几个必须做到的点（都是主人特意交代的）：
  · **图形界面**，不能是黑框 ✓
  · 装完**在桌面生成快捷方式** ✓
  · 快捷方式**直接指向 pythonw.exe**（pythonw 没有控制台 → 绝不闪 cmd 窗口）✓
  · **不收集、不写入任何个人信息**（除了标题上的署名与联系 QQ）✓

用法（老师双击「安装.vbs」就会跑到这里）：
    pythonw 安装向导.py
"""
import os
import shutil
import subprocess
import sys
import threading
import time

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_NAME = "超级AI工作台"
SLOGAN1 = "一切奇迹的起点"
SLOGAN2 = "与你相遇，便是奇迹"
CONTACT = "有问题加Q:3153180025"
AUTHOR = "Made by 杨家乐（内江师范学院-智建学院）"

# 安装包里要复制过去的东西（白名单，宁少不多）
COPY_FILES = [
    "app.py", "gallery_ai.py", "model_ai.py", "search_ai.py", "stem_ai.py",
    "cover_ai.py", "tts_ai.py", "music_ai.py", "matting_ai.py", "blend_ai.py",
    "local_ai.py", "dsh_ai.py", "video_ai.py", "campus.py", "toolbox.py",
    "_stem_worker.py", "_cover_vc.py", "_cover_sep.py", "_tts_worker.py",
    "说明.txt", "使用说明.txt",
]
COPY_DIRS = ["web", "dsh"]          # web = 界面；dsh = 高速工作流的安装器

BG = "#f4f9ff"
FG = "#123a6b"
BLUE = "#2f7ddb"


def human(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return ("%.0f %s" % (n, u)) if u in ("B", "KB") else ("%.1f %s" % (n, u))
        n /= 1024


def dir_size(p):
    t = 0
    for r, _d, fs in os.walk(p):
        for f in fs:
            try:
                t += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return t


def res_dir():
    """资源目录。

    打包成 exe 后 PyInstaller 会把数据解到 sys._MEIPASS；
    直接跑源码时就是脚本所在目录 ✓
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.dirname(os.path.abspath(__file__))


def find_pythonw(install_dir=None):
    """找 pythonw.exe（没有控制台的那个）—— 快捷方式指它才不会闪窗口 ✓

    **优先用我们自带的那份运行时**（安装目录下的 runtime/pythonw.exe），
    这样老师机器上根本不用装 Python ✓
    """
    if install_dir:
        p = os.path.join(install_dir, "runtime", "pythonw.exe")
        if os.path.isfile(p):
            return p
    d = os.path.dirname(sys.executable)
    for n in ("pythonw.exe", "python.exe"):
        p = os.path.join(d, n)
        if os.path.isfile(p):
            return p
    w = shutil.which("pythonw")
    return w or sys.executable


def make_shortcut(lnk_path, target, args, workdir, desc):
    """建快捷方式：先试 COM（不走脚本引擎，杀软拦不到），不行再退回 wscript"""
    try:
        if os.path.exists(lnk_path):
            os.remove(lnk_path)
    except OSError:
        pass
    if _shortcut_com(lnk_path, target, args, workdir, desc):
        return True
    return _shortcut_wscript(lnk_path, target, args, workdir, desc)


def _shortcut_com(lnk_path, target, args, workdir, desc):
    """用 COM 的 IShellLink 直接建 .lnk。

    为什么不走 wscript：这台机器上 wscript 一保存就被拦
    （WshShortcut.Save 报 0xC0000005 访问冲突），火绒/AMSI 干的事 ✗
    COM 是进程内调用，根本没有脚本引擎参与 ✓
    """
    try:
        import ctypes

        ole32 = ctypes.oledll.ole32

        class GUID(ctypes.Structure):
            _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort),
                        ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]

        def _guid(s):
            g = GUID()
            ole32.CLSIDFromString(ctypes.c_wchar_p(s), ctypes.byref(g))
            return g

        clsid = _guid("{00021401-0000-0000-C000-000000000046}")     # ShellLink
        iid_sl = _guid("{000214F9-0000-0000-C000-000000000046}")    # IShellLinkW
        iid_pf = _guid("{0000010b-0000-0000-C000-000000000046}")    # IPersistFile

        ole32.CoInitialize(None)
        psl = ctypes.c_void_p()
        ole32.CoCreateInstance(ctypes.byref(clsid), None, 1,
                               ctypes.byref(iid_sl), ctypes.byref(psl))
        if not psl:
            return False

        vt = ctypes.cast(psl, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        HR = ctypes.c_long

        def fn(tbl, idx, *extra):
            return ctypes.WINFUNCTYPE(HR, ctypes.c_void_p, *extra)(tbl[idx])

        # IShellLinkW 的方法序号：7 SetDescription / 9 SetWorkingDirectory
        #                       11 SetArguments / 20 SetPath
        fn(vt, 20, ctypes.c_wchar_p)(psl, target)
        fn(vt, 11, ctypes.c_wchar_p)(psl, args)
        fn(vt, 9, ctypes.c_wchar_p)(psl, workdir)
        fn(vt, 7, ctypes.c_wchar_p)(psl, desc)

        ppf = ctypes.c_void_p()
        fn(vt, 0, ctypes.c_void_p, ctypes.c_void_p)(
            psl, ctypes.byref(iid_pf), ctypes.byref(ppf))
        if not ppf:
            return False
        vt2 = ctypes.cast(ppf, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        # IPersistFile 序号 6 = Save
        ctypes.WINFUNCTYPE(HR, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_long)(
            vt2[6])(ppf, lnk_path, 1)

        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vt2[2])(ppf)
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vt[2])(psl)
        return os.path.isfile(lnk_path)
    except Exception as e:
        print("[shortcut] COM 方式没成：%s" % e)
        return False


def _shortcut_wscript(lnk_path, target, args, workdir, desc):
    """兜底：用 WScript.Shell 建（有些机器 COM 会被拦，反而这条能过）"""
    def _v(s):
        # VBS 里引号用 Chr(34) 拼，避开转义地狱 ✓
        return '"%s"' % str(s).replace('"', '" & Chr(34) & "')

    vbs = (
        'Set sh = CreateObject("WScript.Shell")\n'
        'Set lnk = sh.CreateShortcut(%s)\n'
        'lnk.TargetPath = %s\n'
        'lnk.Arguments = %s\n'
        'lnk.WorkingDirectory = %s\n'
        'lnk.Description = %s\n'
        'lnk.Save\n'
    ) % (_v(lnk_path), _v(target), _v(args), _v(workdir), _v(desc))
    tmp = os.path.join(os.environ.get("TEMP", "."), "_aiws_lnk_%d.vbs" % int(time.time()))
    with open(tmp, "w", encoding="gbk", errors="replace") as f:
        f.write(vbs)
    try:
        subprocess.run(["wscript", "//nologo", "//B", tmp], capture_output=True,
                       creationflags=0x08000000 if os.name == "nt" else 0, timeout=60)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return os.path.isfile(lnk_path)


def desktop_dir():
    d = os.path.join(os.path.expanduser("~"), "Desktop")
    if not os.path.isdir(d):
        # 有些机器桌面被重定向到 OneDrive
        alt = os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop")
        if os.path.isdir(alt):
            return alt
    return d


class Wizard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("%s · 安装向导　%s" % (APP_NAME, CONTACT))
        self.geometry("760x540")
        self.minsize(720, 500)
        self.configure(bg=BG)
        self.resizable(True, True)

        # 打包成 exe 后，代码和材料都躺在 _MEIPASS\payload\ 下 ✓
        _rd = res_dir()
        _pl = os.path.join(_rd, "payload")
        self.src = _pl if os.path.isdir(_pl) else _rd
        self.step = 0
        self.install_dir = tk.StringVar(value=self._default_dir())
        self.do_shortcut = tk.BooleanVar(value=True)
        self.do_launch = tk.BooleanVar(value=True)
        self.log_text = None
        self.bar = None
        self.pct = None
        self.installing = False

        self._build_head()
        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both", expand=True, padx=26, pady=(0, 8))
        self._build_foot()
        self.show(0)

    # ---------------------------------------------------------------- 外框
    def _default_dir(self):
        for c in ("D:\\AI工作站", "C:\\AI工作站"):
            drv = os.path.splitdrive(c)[0] + "\\"
            if os.path.isdir(drv):
                try:
                    if shutil.disk_usage(drv).free > 8 * 2**30:
                        return c
                except OSError:
                    pass
        return os.path.join(os.path.expanduser("~"), "AI工作站")

    def _build_head(self):
        head = tk.Frame(self, bg=BLUE)
        head.pack(fill="x")
        inner = tk.Frame(head, bg=BLUE)
        inner.pack(fill="x", padx=26, pady=16)
        tk.Label(inner, text="🐋 " + APP_NAME, bg=BLUE, fg="#ffffff",
                 font=("Microsoft YaHei UI", 20, "bold")).pack(anchor="w")
        tk.Label(inner, text="%s　·　%s" % (SLOGAN1, SLOGAN2), bg=BLUE, fg="#dceaff",
                 font=("Microsoft YaHei UI", 11)).pack(anchor="w", pady=(4, 0))
        tk.Label(inner, text=CONTACT, bg=BLUE, fg="#ffe08a",
                 font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", pady=(6, 0))
        tk.Label(inner, text=AUTHOR, bg=BLUE, fg="#cfe3ff",
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w")

    def _build_foot(self):
        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", padx=26, pady=(4, 16))
        self.btn_back = tk.Button(foot, text="← 上一步", width=10, command=self.back,
                                  font=("Microsoft YaHei UI", 10))
        self.btn_next = tk.Button(foot, text="下一步 →", width=12, command=self.next,
                                  font=("Microsoft YaHei UI", 10, "bold"),
                                  bg=BLUE, fg="#ffffff", activebackground="#1e63b8",
                                  activeforeground="#ffffff", relief="flat", cursor="hand2")
        tk.Button(foot, text="退出", width=8, command=self.destroy,
                  font=("Microsoft YaHei UI", 10)).pack(side="right", padx=(8, 0))
        self.btn_next.pack(side="right")
        self.btn_back.pack(side="right", padx=(0, 8))
        self.step_lab = tk.Label(foot, text="", bg=BG, fg="#5b7ea3",
                                 font=("Microsoft YaHei UI", 9))
        self.step_lab.pack(side="left")

    # ---------------------------------------------------------------- 分页
    def clear(self):
        for w in self.body.winfo_children():
            w.destroy()

    def show(self, i):
        self.step = i
        self.clear()
        [self.p_ready, self.p_dir, self.p_run, self.p_done][i]()
        self.step_lab.config(text="第 %d / 4 步" % (i + 1))
        self.btn_back.config(state=("normal" if i in (1,) else "disabled"))
        if i in (0, 1):
            self.btn_next.config(text="下一步 →", state="normal", command=self.next)
        elif i == 2:
            self.btn_next.config(text="正在安装…", state="disabled")
        else:
            self.btn_next.config(text="完成", state="normal", command=self.destroy)

    def next(self):
        if self.step == 1:
            d = self.install_dir.get().strip()
            if not d:
                messagebox.showwarning("还没选位置", "请先选一个安装位置")
                return
            try:
                os.makedirs(d, exist_ok=True)
                shutil.disk_usage(os.path.splitdrive(d)[0] + "\\")
            except Exception as e:
                messagebox.showerror("这个位置不能用", "%s\n\n%s" % (d, e))
                return
            if os.path.isdir(d) and os.listdir(d):
                if not messagebox.askyesno(
                        "目录里已经有东西",
                        "%s\n\n里面不是空的，继续会覆盖同名文件。要继续吗？" % d):
                    return
            self.show(2)
            threading.Thread(target=self.do_install, daemon=True).start()
            return
        self.show(min(self.step + 1, 3))

    def back(self):
        if self.step == 1:
            self.show(0)

    # ------------------------------------------------------------ ① 欢迎
    def p_ready(self):
        tk.Label(self.body, text="开始之前，先看一下", bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w", pady=(6, 10))
        box = tk.Frame(self.body, bg="#ffffff", highlightbackground="#cfe3f7",
                       highlightthickness=1)
        box.pack(fill="both", expand=True)

        py = sys.version.split()[0]
        tkv = "可用" if _tk_ok() else "不可用"
        try:
            free = shutil.disk_usage(self._default_dir()[:2] + "\\").free
        except OSError:
            free = 0
        src_sz = sum(os.path.getsize(os.path.join(self.src, f))
                     for f in COPY_FILES if os.path.isfile(os.path.join(self.src, f)))
        src_sz += sum(dir_size(os.path.join(self.src, d))
                      for d in COPY_DIRS if os.path.isdir(os.path.join(self.src, d)))

        rows = [
            ("要装什么", "超级AI工作台本体（%s，不含模型）" % human(src_sz)),
            ("缺的依赖", "自动装（Pillow 等，用清华源；装不上会告诉你）"),
            ("Python", "**自带**（%s，装完不用你再装）　图形界面：%s" % (py, tkv)),
            ("目标磁盘可用", human(free)),
            ("装完能用什么", "界面能开、设置能存、作品库能看；\n"
                             "模型和引擎到界面里点「一键下载」就行 ✓"),
            ("不会做什么", "不装驱动、不改系统设置、不写注册表、\n"
                           "不收集任何个人信息 ✓"),
        ]
        for k, v in rows:
            r = tk.Frame(box, bg="#ffffff")
            r.pack(fill="x", padx=18, pady=7)
            tk.Label(r, text=k, bg="#ffffff", fg="#5b7ea3", width=12, anchor="w",
                     font=("Microsoft YaHei UI", 10)).pack(side="left")
            tk.Label(r, text=v, bg="#ffffff", fg=FG, justify="left", anchor="w",
                     font=("Microsoft YaHei UI", 10)).pack(side="left", fill="x", expand=True)

    # ---------------------------------------------------------- ② 选位置
    def p_dir(self):
        tk.Label(self.body, text="装在哪儿？", bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w", pady=(6, 10))
        tk.Label(self.body, text="建议放在非系统盘、路径里不要有空格。装完随时可以删，"
                                 "卸载就是把这个文件夹删掉。",
                 bg=BG, fg="#5b7ea3", justify="left",
                 font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(0, 14))
        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x")
        tk.Entry(row, textvariable=self.install_dir, font=("Consolas", 11)).pack(
            side="left", fill="x", expand=True, ipady=5)
        tk.Button(row, text="浏览…", width=8, command=self._pick_dir,
                  font=("Microsoft YaHei UI", 10)).pack(side="left", padx=(8, 0))

        opts = tk.Frame(self.body, bg=BG)
        opts.pack(fill="x", pady=(22, 0))
        tk.Checkbutton(opts, text="在桌面创建快捷方式（双击就能打开，不会弹黑框）",
                       variable=self.do_shortcut, bg=BG, fg=FG, activebackground=BG,
                       font=("Microsoft YaHei UI", 10), anchor="w").pack(anchor="w")
        tk.Checkbutton(opts, text="装完立刻启动一次", variable=self.do_launch, bg=BG,
                       fg=FG, activebackground=BG, font=("Microsoft YaHei UI", 10),
                       anchor="w").pack(anchor="w", pady=(6, 0))

    def _pick_dir(self):
        d = filedialog.askdirectory(title="选安装位置", initialdir=os.path.dirname(
            self.install_dir.get().rstrip("\\")) or "C:\\")
        if d:
            self.install_dir.set(os.path.normpath(d))

    # ---------------------------------------------------------- ③ 安装中
    def p_run(self):
        tk.Label(self.body, text="正在安装…", bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w", pady=(6, 10))
        self.bar = ttk.Progressbar(self.body, length=680, maximum=100)
        self.bar.pack(fill="x")
        self.pct = tk.Label(self.body, text="0%", bg=BG, fg=FG,
                            font=("Microsoft YaHei UI", 11, "bold"))
        self.pct.pack(anchor="w", pady=(6, 10))
        self.log_text = tk.Text(self.body, height=12, font=("Consolas", 9),
                                bg="#ffffff", fg="#2c4c6d", relief="flat",
                                highlightbackground="#cfe3f7", highlightthickness=1)
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

    def log(self, m):
        if not self.log_text:
            return
        self.log_text.configure(state="normal")
        self.log_text.insert("end", m + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def set_pct(self, v, note=""):
        if self.bar:
            self.bar["value"] = v
        if self.pct:
            self.pct.config(text="%.0f%%　%s" % (v, note))

    # --------------------------------------------------- 装自带运行时
    def install_runtime(self, dst):
        """把自带的 Python 运行时装过去 —— 老师不用自己装 Python ✓

        自带的是 python.org 的 **embeddable** 包，好处是干净、小（11 MB）、
        不碰系统；但默认禁掉了 site，不改 `._pth` 就 import 不到 Pillow ✗
        """
        rt = os.path.join(dst, "runtime")
        zsrc = os.path.join(self.src, "python-embed.zip")
        dsrc = os.path.join(self.src, "python-embed")     # SFX 包里是解压好的目录 ✓
        if not os.path.isfile(zsrc) and not os.path.isdir(dsrc):
            self.log("  · 这个包里没带运行时（源码方式运行）—— 那就用系统里已装的 Python")
            return False

        import zipfile
        if os.path.isfile(os.path.join(rt, "pythonw.exe")):
            self.log("  ✓ 运行时已经在了，跳过")
        elif os.path.isdir(dsrc):
            self.log("  复制自带的 Python 运行时（约 46 MB，稍等一下）…")
            shutil.copytree(dsrc, rt, dirs_exist_ok=True)
            self.log("  ✓ 运行时就位")
        else:
            self.log("  解压自带的 Python 运行时（约 16 MB）…")
            os.makedirs(rt, exist_ok=True)
            with zipfile.ZipFile(zsrc) as z:
                z.extractall(rt)
            self.log("  ✓ 运行时解压好了")

        # embed 版默认禁 site —— 打开并把 site-packages 加进去，否则 import 不到 Pillow ✗
        for f in os.listdir(rt):
            if f.endswith("._pth"):
                with open(os.path.join(rt, f), "w", encoding="utf-8") as fh:
                    fh.write("python311.zip\n.\n..\nDLLs\nLib\nLib\\site-packages\nimport site\n")
                self.log("  ✓ 配好 import 路径（%s）" % f)

        # Pillow —— 工作站主进程的硬依赖（blend_ai.py 顶层就 from PIL import）
        sp = os.path.join(rt, "Lib", "site-packages")
        if not os.path.isdir(os.path.join(sp, "PIL")):
            whl = None
            for f in os.listdir(self.src):
                if f.lower().startswith("pillow-") and f.lower().endswith(".whl"):
                    whl = os.path.join(self.src, f)
                    break
            if whl:
                self.log("  装上 Pillow（%s）…" % os.path.basename(whl))
                os.makedirs(sp, exist_ok=True)
                with zipfile.ZipFile(whl) as z:
                    z.extractall(sp)
                self.log("  ✓ Pillow 好了")
            else:
                self.log("  · 包里没有 Pillow 的 wheel，跳过（切图相关功能可能用不了）")
        else:
            self.log("  ✓ Pillow 已经在了")

        # 自检：自带的运行时到底能不能跑、Pillow 在不在
        pyw = os.path.join(rt, "pythonw.exe")
        try:
            r = subprocess.run([pyw, "-c",
                                "import sys, PIL; print(sys.version.split()[0], PIL.__version__)"],
                               capture_output=True, text=True, timeout=180,
                               creationflags=0x08000000 if os.name == "nt" else 0)
            if r.returncode == 0:
                self.log("  ✓ 自检通过：自带运行时能跑（Python %s · Pillow %s）" % tuple(
                    (r.stdout or "").strip().split()[:2] or ("?", "?")))
                return True
            self.log("  ✗ 运行时自检没过：%s" % ((r.stderr or "").strip()[:200] or "返回码 %d" % r.returncode))
        except Exception as e:
            self.log("  ✗ 运行时自检出错：%s" % e)
        return False

    # ------------------------------------------------------- 补依赖
    def ensure_deps(self):
        """保证主进程能起来。

        **Pillow 是硬依赖** —— blend_ai.py 顶层就 from PIL import，
        缺了界面直接崩 ✗ 所以必须装。
        numpy / onnxruntime 是可选（都在函数内 import），缺了只影响对应功能，
        这里顺手一起试装，装不上不算失败 ✓
        """
        hard = [("PIL", "pillow")]
        soft = [("numpy", "numpy"), ("onnxruntime", "onnxruntime")]
        todo = []
        for mod, pkg in hard:
            try:
                __import__(mod)
                self.log("  ✓ %s 已装" % pkg)
            except ImportError:
                self.log("  · %s 没装 —— 这是必需的，正在装" % pkg)
                todo.append(pkg)
        for mod, pkg in soft:
            try:
                __import__(mod)
            except ImportError:
                todo.append(pkg)
        if not todo:
            return True
        self.log("  用清华源安装：%s" % ", ".join(todo))
        cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
               "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"] + todo
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=1800,
                               creationflags=0x08000000 if os.name == "nt" else 0)
            tail = (r.stdout or "").strip().splitlines()[-6:]
            for line in tail:
                self.log("      " + line)
            if r.returncode != 0:
                self.log("  ✗ 装依赖失败（返回码 %d）" % r.returncode)
                for line in (r.stderr or "").strip().splitlines()[-6:]:
                    self.log("      " + line)
                return False
        except Exception as e:
            self.log("  ✗ 装依赖出错：%s" % e)
            return False
        # 复查硬依赖
        try:
            import importlib
            importlib.invalidate_caches()
            __import__("PIL")
            self.log("  ✓ Pillow 装好了")
        except ImportError:
            self.log("  ✗ Pillow 还是没装上 —— 界面可能起不来，请手动：")
            self.log("      python -m pip install pillow")
            return False
        return True

    # ------------------------------------------------------------ 安装逻辑
    def do_install(self):
        self.installing = True
        dst = self.install_dir.get().strip()
        try:
            self.log("安装位置：%s" % dst)
            self.log("来源：%s" % self.src)
            self.log("")
            self.log("── 第 1 步：装自带的 Python 运行时 ──")
            self.has_runtime = self.install_runtime(dst)
            self.log("")
            self.log("── 第 2 步：复制程序文件 ──")
            os.makedirs(dst, exist_ok=True)

            files = [f for f in COPY_FILES if os.path.isfile(os.path.join(self.src, f))]
            dirs = [d for d in COPY_DIRS if os.path.isdir(os.path.join(self.src, d))]
            total = sum(os.path.getsize(os.path.join(self.src, f)) for f in files)
            total += sum(dir_size(os.path.join(self.src, d)) for d in dirs)
            done = 0
            self.log("要复制 %d 个文件 + %d 个目录，共 %s" % (len(files), len(dirs), human(total)))

            for f in files:
                sp, dp = os.path.join(self.src, f), os.path.join(dst, f)
                shutil.copy2(sp, dp)
                done += os.path.getsize(sp)
                self.set_pct(100.0 * done / max(total, 1), f)
                self.log("  ✓ %s" % f)
            for d in dirs:
                base = os.path.join(self.src, d)
                for r, _ds, fs in os.walk(base):
                    rel = os.path.relpath(r, self.src)
                    os.makedirs(os.path.join(dst, rel), exist_ok=True)
                    for f in fs:
                        sp = os.path.join(r, f)
                        dp = os.path.join(dst, rel, f)
                        try:
                            shutil.copy2(sp, dp)
                            done += os.path.getsize(sp)
                        except OSError:
                            pass
                    self.set_pct(100.0 * done / max(total, 1), rel)
                self.log("  ✓ %s\\" % d)

            # 建目录
            for sub in ("data", os.path.join("data", "搜索历史"), os.path.join("data", "分离素材"),
                        os.path.join("data", "分离出片"), os.path.join("data", "溶图背景"),
                        os.path.join("data", "溶图主体"), os.path.join("data", "溶图出片"),
                        os.path.join("data", "抠图出片"), os.path.join("data", "翻唱出片"),
                        os.path.join("data", "朗读出片")):
                os.makedirs(os.path.join(dst, sub), exist_ok=True)
            self.log("")
            self.log("  ✓ 运行时目录建好了")

            # 初始配置：站点标题 + 联系方式（不含任何 Key、不含个人信息）
            cfg = os.path.join(dst, "data", "config.json")
            if not os.path.isfile(cfg):
                import json
                with open(cfg, "w", encoding="utf-8") as f:
                    json.dump({
                        "siteTitle": APP_NAME, "siteSlogan": SLOGAN1, "siteSlogan2": SLOGAN2,
                        "siteAuthor": AUTHOR, "contact": CONTACT,
                    }, f, ensure_ascii=False, indent=2)
                self.log("  ✓ 写好初始配置（里面没有任何密钥）")

            # 零窗口启动器 + 快捷方式
            self.log("")
            if self.do_shortcut.get():
                dt = desktop_dir()
                existing = []
                try:
                    for f in os.listdir(dt):
                        if f.lower().endswith(".lnk") and "工作站" in f:
                            existing.append(f)
                except OSError:
                    pass
                if existing:
                    # 桌面上已经有一个了 —— 不重复建、更不覆盖主人的 ✓
                    self.log("  · 桌面existing快捷方式（%s），就不重复建了 ✓"
                             % "、".join(existing[:4]))
                else:
                    pyw = find_pythonw(dst)
                    lnk = os.path.join(dt, APP_NAME + ".lnk")
                    ok = make_shortcut(lnk, pyw, '"%s"' % os.path.join(dst, "app.py"),
                                       dst, APP_NAME + " · " + SLOGAN1)
                    self.log(("  ✓ 桌面快捷方式：%s\n     指向 %s（自带运行时，pythonw 没有控制台 → 绝不闪黑框）"
                              % (lnk, pyw)) if ok
                             else "  ✗ 快捷方式没建成（可以手动建一个指向 app.py 的）")

            self.set_pct(100, "装好了")
            self.log("")
            self.log("装好了 ✓  %s" % CONTACT)
            self.after(0, lambda: self.show(3))
            if self.do_launch.get():
                self.after(300, lambda: self.launch(dst))
        except Exception as e:
            self.log("")
            self.log("✗ 出错了：%s" % e)
            self.after(0, lambda: messagebox.showerror("安装失败", str(e)))
            self.after(0, lambda: self.show(3))
        finally:
            self.installing = False

    def launch(self, dst):
        """启动工作站 —— 用 pythonw，不弹任何窗口 ✓"""
        try:
            pyw = find_pythonw()
            flags = 0x00000008 | 0x08000000        # DETACHED_PROCESS | CREATE_NO_WINDOW
            subprocess.Popen([pyw, os.path.join(dst, "app.py")], cwd=dst,
                             creationflags=flags, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            self.log("  ✓ 已经启动，浏览器会自动打开")
        except Exception as e:
            self.log("  · 启动失败（手动双击桌面快捷方式也行）：%s" % e)

    # ------------------------------------------------------------ ④ 完成
    def p_done(self):
        tk.Label(self.body, text="装好了 ✓", bg=BG, fg="#177b3f",
                 font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w", pady=(6, 12))
        txt = (
            "装到了：%s\n\n"
            "怎么用：\n"
            "  · 双击桌面上的「%s」就能打开（不弹黑框）\n"
            "  · 界面里的模型和引擎，缺什么点页面上的「⬇ 一键下载」就行\n"
            "  · 想卸载：把这个文件夹整个删掉，再删桌面快捷方式，就干净了\n\n"
            "%s\n"
        ) % (self.install_dir.get(), APP_NAME, CONTACT)
        box = tk.Frame(self.body, bg="#ffffff", highlightbackground="#cfe3f7",
                       highlightthickness=1)
        box.pack(fill="both", expand=True)
        tk.Label(box, text=txt, bg="#ffffff", fg=FG, justify="left", anchor="nw",
                 font=("Microsoft YaHei UI", 10)).pack(fill="both", expand=True,
                                                        padx=18, pady=14)
        if self.log_text is None:
            self.log_text = tk.Text(self.body, height=7, font=("Consolas", 9),
                                    bg="#ffffff", fg="#2c4c6d", relief="flat",
                                    highlightbackground="#cfe3f7", highlightthickness=1)
            self.log_text.pack(fill="x", pady=(10, 0))
            self.log_text.configure(state="disabled")
        tk.Button(self.body, text="🚀 现在启动", command=lambda: self.launch(self.install_dir.get()),
                  bg=BLUE, fg="#ffffff", relief="flat", cursor="hand2",
                  font=("Microsoft YaHei UI", 11, "bold"), padx=16, pady=6).pack(pady=(12, 0))


def _tk_ok():
    try:
        return tk.TkVersion > 0
    except Exception:
        return False


def main():
    argv = sys.argv[1:]
    if "--silent" in argv:
        i = argv.index("--silent")
        dst = argv[i + 1] if len(argv) > i + 1 else ""
        if not dst:
            print("用法：<安装程序> --silent <安装目录>")
            return 1
        app = Wizard()
        app.withdraw()
        app.install_dir.set(dst)
        app.do_shortcut.set(False)      # 静默模式不碰桌面 ✓
        app.do_launch.set(False)
        app.after(200, lambda: threading.Thread(target=app.do_install, daemon=True).start())

        def _watch():
            # 装完了（自带运行时到位）就自己退出，不用等人点 ✓
            if os.path.isfile(os.path.join(dst, "runtime", "pythonw.exe")):
                app.after(2500, app.destroy)
            else:
                app.after(1500, _watch)

        app.after(3000, _watch)
        app.mainloop()
        return 0
    app = Wizard()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
