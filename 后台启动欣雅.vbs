' 欣雅 桌面智能管家 - 后台无窗口启动
' 静默调用 pythonw.exe 运行 run.py，不弹出终端窗口。
' 双击本文件即在后台启动。

Set shell = CreateObject("WScript.Shell")

baseDir = "E:\程序\桌面宠物\xiaoyi-vrm-worktree"
pythonw = "C:\Python312\pythonw.exe"
runPy = baseDir & "\run.py"

shell.CurrentDirectory = baseDir
' 0 = 隐藏窗口；False = 异步不等待
shell.Run """" & pythonw & """ """ & runPy & """", 0, False
