' ============================================================
'  欣雅 桌面智能管家 - 启动器（无窗口）
' ============================================================
'  双击本文件即在后台启动欣雅，**不会出现黑色终端窗口**。
'
'  【为什么不用 .bat 做默认入口】
'    「启动欣雅.bat」用的是 python.exe（控制台版），必然弹出一个黑窗；
'    而且那个窗口一关程序就退出。它是给**排查问题**用的，
'    不是给日常用的 —— 所以日常入口收敛到本文件。
'
'  【为什么写 .vbs 而不是快捷方式】
'    .vbs 能同时做到三件 .lnk 做不到的事：
'      1. 零窗口启动（shell.Run 的窗口模式传 0）
'      2. 从**自身所在目录**推导项目根 —— 移动/改名/复制整个文件夹都不失效
'      3. 启动失败时能读日志并弹窗告诉你原因
'
'  【本文件必须存为 GBK(ANSI)】
'    cscript/wscript 默认按系统 ANSI 读取 .vbs，
'    存成 UTF-8 会让中文变乱码（注释乱码无所谓，MsgBox 里的字会花）。
'
'  【启动失败会弹窗】
'    pythonw.exe 没有控制台，崩了什么都看不见 —— 这正是"双击后没反应"
'    最难查的原因。所以本脚本会在启动后探测进程是否还活着：
'    若秒退，就读 logs\xbya.log 的最后几行弹出来。
' ============================================================

Option Explicit

Const WAIT_MS     = 6000     ' 启动后等多久再判断存活（毫秒）
Const TAIL_LINES  = 14       ' 失败时弹窗显示日志的最后几行

Dim fso, shell, baseDir, runPy, pythonw, cmd, logFile, i, alive

Set fso   = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

' 本文件所在目录 = 项目根目录（本文件和 run.py 放在一起）
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
runPy   = fso.BuildPath(baseDir, "run.py")
logFile = fso.BuildPath(fso.BuildPath(baseDir, "logs"), "xbya.log")

' ---- 启动前检查：缺 run.py 就明确报错，不要静默失败 ----
If Not fso.FileExists(runPy) Then
    MsgBox "找不到启动脚本：" & vbCrLf & runPy & vbCrLf & vbCrLf & _
           "请确认本文件与 run.py 在同一个文件夹内。", _
           vbCritical, "欣雅 - 启动失败"
    WScript.Quit 1
End If

' ---- 找无控制台的 Python ----
' 优先用与本机 python 同目录的 pythonw.exe；找不到就交给 PATH。
' （pythonw 是 GUI 版解释器，不会创建控制台窗口）
pythonw = "C:\Python312\pythonw.exe"
If Not fso.FileExists(pythonw) Then
    pythonw = "pythonw.exe"
End If

' ---- 工作目录设为项目根，否则 config.yaml / resources 找不到 ----
shell.CurrentDirectory = baseDir

' ---- 启动（0 = 隐藏窗口，False = 不等待）----
cmd = """" & pythonw & """ """ & runPy & """"
On Error Resume Next
shell.Run cmd, 0, False
If Err.Number <> 0 Then
    MsgBox "无法启动 Python：" & vbCrLf & pythonw & vbCrLf & vbCrLf & _
           "错误：" & Err.Description, vbCritical, "欣雅 - 启动失败"
    WScript.Quit 1
End If
On Error GoTo 0

' ---- 存活探测：秒退说明启动失败 ----
' 单实例锁会让第二个实例**正常退出（退出码 0）**，那不是故障，
' 所以判据不是退出码，而是"日志里有没有『启动失败』"。
WScript.Sleep WAIT_MS

alive = False
On Error Resume Next
For i = 0 To 0
    ' 用 WMIC 查 pythonw 进程；查不到就认为已退出
    Dim wmi, procs
    Set wmi = GetObject("winmgmts:\\.\root\cimv2")
    Set procs = wmi.ExecQuery("SELECT ProcessId FROM Win32_Process WHERE Name='pythonw.exe'")
    If procs.Count > 0 Then alive = True
Next
On Error GoTo 0

If alive Then
    ' 正常运行：什么都不做，静默退出
    WScript.Quit 0
End If

' ---- 已退出：读日志最后几行，告诉用户到底怎么了 ----
Dim tail, ts, n
tail = ""
If fso.FileExists(logFile) Then
    Set ts = fso.OpenTextFile(logFile, 1, False, -2)   ' -2 = 系统默认编码(GBK)
    Do Until ts.AtEndOfStream
        Dim line
        line = ts.ReadLine
        tail = tail & line & vbCrLf
        n = n + 1
    Loop
    ts.Close
    ' 只保留最后 TAIL_LINES 行
    Dim arr, k, start
    arr = Split(tail, vbCrLf)
    start = UBound(arr) - TAIL_LINES + 1
    If start < 0 Then start = 0
    tail = ""
    For k = start To UBound(arr)
        If Len(arr(k)) > 0 Then tail = tail & arr(k) & vbCrLf
    Next
End If

If Len(tail) = 0 Then
    tail = "（没有找到日志文件 logs\xbya.log）"
End If

MsgBox "欣雅启动失败，已经退出了。" & vbCrLf & vbCrLf & _
       "最近日志：" & vbCrLf & tail & vbCrLf & _
       "完整日志：" & vbCrLf & logFile & vbCrLf & vbCrLf & _
       "若是『已有一个欣雅实例在运行』，说明她已经开着了，不用重复启动。", _
       vbExclamation, "欣雅 - 启动失败"

WScript.Quit 1
