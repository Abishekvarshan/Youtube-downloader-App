Option Explicit

Dim shell, files, appRoot, appExe
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
appRoot = files.GetParentFolderName(WScript.ScriptFullName)
appExe = files.BuildPath(appRoot, "Streamline.exe")
shell.CurrentDirectory = appRoot
shell.Run Chr(34) & appExe & Chr(34), 0, False
