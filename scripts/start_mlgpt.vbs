Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "E:\Repo\MLGPT"
WshShell.Run "C:\Users\pppad\AppData\Local\Programs\Python\Python313\Scripts\streamlit.exe run app.py --server.port 8502 --server.headless true", 0, False