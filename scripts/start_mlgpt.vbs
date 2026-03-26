Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\pppad\OneDrive\Desktop\MLGPT"
WshShell.Run "C:\Users\pppad\AppData\Local\Programs\Python\Python313\Scripts\streamlit.exe run app.py --server.port 8501", 0, False
