"""应用条件的确定性检测；固定只读命令在线程内执行，用户输入不进入命令文本。"""

from typing import Dict, List
import json
import subprocess
import sys
import unicodedata


def running_applications() -> List[Dict[str, str]]:
    if sys.platform != "win32":
        raise RuntimeError("应用打开检测目前仅支持 Windows")
    result = subprocess.run([
        "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; $ErrorActionPreference='Stop'; "
        "@(Get-Process | Where-Object { $_.Id -ne $PID } | Select-Object ProcessName,MainWindowTitle) | ConvertTo-Json -Compress",
    ], capture_output=True, text=True, encoding="utf-8-sig", check=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW)
    data = json.loads(result.stdout)
    return [{"process": item["ProcessName"], "title": item["MainWindowTitle"]} for item in data]


def matching_application(query: str, applications: List[Dict[str, str]]) -> str:
    def normalize(value: str) -> str:
        return unicodedata.normalize("NFKC", value).strip().casefold().removesuffix(".exe")
    keyword = normalize(query)
    if not keyword:
        raise ValueError("应用匹配内容不能为空")
    for app in applications:
        if keyword in normalize(app["process"]) or keyword in normalize(app["title"]):
            return f"检测到进程 {app['process']}；窗口标题：{app['title']}"[:1000]
    return ""
