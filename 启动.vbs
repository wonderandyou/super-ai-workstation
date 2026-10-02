' 超级AI工作台 · 启动（不会弹黑框）
' 有问题加Q:3153180025
Option Explicit

Dim sh, fso, here, pyw, app, lnk, dt
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = here & "\runtime\pythonw.exe"
app = here & "\app.py"

If Not fso.FileExists(app) Then
    MsgBox "找不到 app.py —— 请把压缩包**整个解压**到一个文件夹里再双击本文件。", 48, "超级AI工作台"
    WScript.Quit 1
End If
If Not fso.FileExists(pyw) Then
    MsgBox "找不到自带的 Python 运行时（runtime 文件夹）。" & vbCrLf & vbCrLf & _
           "请确认压缩包**完整解压**了，不要只解压一部分。", 48, "超级AI工作台"
    WScript.Quit 1
End If

' 第一次运行：顺手在桌面建个快捷方式（以后从桌面双击）
dt = sh.SpecialFolders("Desktop")
lnk = dt & "\超级AI工作台.lnk"
' 每次都刷新一次快捷方式：这样图标/路径改了能立刻生效（原来只在不存在时建，
' 导致旧快捷方式永远留着老图标）?
On Error Resume Next
Dim l
Set l = sh.CreateShortcut(lnk)
l.TargetPath = pyw
l.Arguments = """" & app & """"
l.WorkingDirectory = here
l.Description = "超级AI工作台 · 一切奇迹的起点"
If fso.FileExists(here & "\超级AI工作台.ico") Then
    l.IconLocation = here & "\超级AI工作台.ico"
End If
l.Save
On Error GoTo 0

' 启动（0 = 窗口隐藏）
sh.CurrentDirectory = here
sh.Run """" & pyw & """ """ & app & """", 0, False
