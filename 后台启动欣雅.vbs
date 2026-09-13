' ============================================================
'  欣雅 桌面智能管家 - 后台无窗口启动
' ============================================================
'  双击本文件即在后台启动欣雅，不弹出黑色终端窗口。
'
'  【为什么不用写死路径】本文件曾因此完全失效：
'     上一版把项目目录写死成 "E:\程序\桌面宠物\xiaoyi-vrm-worktree"，
'     而实际目录是 "xbya-vrm-worktree"（"小忆"改名"欣雅"时漏改）。
'     路径不存在时 WScript 静默失败 —— 双击毫无反应、也没有任何报错，
'     用户只能以为"程序坏了"。
'     现在改成从本文件自身位置推导项目目录，跟着文件夹走，
'     移动 / 改名 / 换机器都不用改这里。
'
'  【编码】本文件存为 GBK(ANSI)。cscript 默认按 ANSI 读 .vbs，
'     存 UTF-8 会让下面的中文提示变成乱码。
' ============================================================

Option Explicit

Dim fso, shell, baseDir, runPy, pythonw, cmd

Set fso   = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

' 本文件所在目录 = 项目根目录（本文件就放在项目根下）
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
runPy   = fso.BuildPath(baseDir, "run.py")

' ---- 启动前检查：缺东西就明确报错，别静默失败 ----
If Not fso.FileExists(runPy) Then
    MsgBox "找不到启动脚本：" & vbCrLf & runPy & vbCrLf & vbCrLf & _
           "请确认本文件与 run.py 在同一个文件夹内。", _
           vbCritical, "欣雅 - 启动失败"
    WScript.Quit 1
End If

' pythonw.exe：无控制台窗口的 Python。
' 优先用与本机 python 同目录的那个；找不到就交给 PATH 解析。
pythonw = "C:\Python312\pythonw.exe"
If Not fso.FileExists(pythonw) Then
    pythonw = "pythonw.exe"
End If

' 工作目录必须是 baseDir，否则 config.yaml / resources 找不到
shell.CurrentDirectory = baseDir

' 0 = 隐藏窗口；False = 不等待（异步）
cmd = """" & pythonw & """ """ & runPy & """"
shell.Run cmd, 0, False