Option Explicit

Dim shell, environment, pythonPath, appDirectory, command

Set shell = CreateObject("WScript.Shell")
pythonPath = "D:\FinGuard_Project\.venv\Scripts\python.exe"
appDirectory = "D:\FinGuard_Project\current"

shell.CurrentDirectory = appDirectory
Set environment = shell.Environment("PROCESS")
environment("PYTHONPATH") = appDirectory
environment("PYTHONDONTWRITEBYTECODE") = "1"
environment("PYTHONPYCACHEPREFIX") = shell.ExpandEnvironmentStrings("%TEMP%") & _
    "\FinGuard_pycache_" & CStr(CLng(Timer * 1000))
environment("FINGUARD_AUTH_REQUIRED") = "1"

command = Chr(34) & pythonPath & Chr(34) & _
    " -m streamlit run app.py --server.headless=true --server.port=80"
shell.Run command, 0, False

WScript.Sleep 4500
shell.Run "http://localhost", 1, False
