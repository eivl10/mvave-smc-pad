' Ярлык на рабочем столе для M-Vave SMC-PAD.
'
' Путь берётся от самого скрипта, а не прописан строкой: прежняя версия вела
' на копию проекта в скретче agy, и запуск с ярлыка поднимал приложение
' недельной давности со своим отдельным конфигом.
'
' Имя файла ярлыка совпадает с тем, что уже лежит на рабочем столе, — иначе
' повторный запуск создаёт второй ярлык рядом со старым.

Set oWS = WScript.CreateObject("WScript.Shell")
Set oFS = WScript.CreateObject("Scripting.FileSystemObject")

sRoot = oFS.GetParentFolderName(WScript.ScriptFullName)

sLinkFile = oWS.SpecialFolders("Desktop") & "\M-Vave SMC-PAD.lnk"
Set oLink = oWS.CreateShortcut(sLinkFile)
oLink.TargetPath = "C:\Program Files\Python\pythonw.exe"
oLink.Arguments = """" & sRoot & "\midi_gui.py"""
oLink.IconLocation = sRoot & "\mvave_icon.ico"
oLink.WorkingDirectory = sRoot
oLink.Description = "M-Vave SMC-PAD Controller"
oLink.Save

' Латиницей намеренно: cscript печатает в консольной кодировке, и кириллица
' в выводе превращается в нечитаемые символы.
WScript.Echo "Shortcut updated: " & sLinkFile & vbCrLf & "Target: " & sRoot & "\midi_gui.py"
