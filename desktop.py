"""电脑版外壳：复用安卓的本机服务、裁判和网页，不复制游戏规则。"""
import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path

import httpx

from android_server import Runtime


def main():
    parser = argparse.ArgumentParser(description="月下圆桌电脑版")
    parser.add_argument("--browser", action="store_true", help="使用浏览器窗口")
    parser.add_argument("--data-dir", type=Path, help="指定本机数据目录")
    parser.add_argument("--check", action="store_true", help="检查打包后的本机服务并退出")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if sys.platform == "darwin":
        data = Path.home() / "Library/Application Support/MoonTable"
    elif sys.platform == "win32":
        data = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MoonTable"
    else:
        data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "MoonTable"
    web = root / "web/dist"
    if not (web / "index.html").is_file():
        raise SystemExit("缺少界面资源，请先在源码目录运行 npm ci 和 npm run build。")
    runtime = Runtime(args.data_dir or data, web)
    runtime.http.daemon_threads = True
    url = f"http://127.0.0.1:{runtime.http.server_port}/"
    print(f"Moon Table: {url}", flush=True)
    try:
        if args.check:
            with httpx.Client() as client:
                response = client.get(url + "api/health")
                response.raise_for_status()
                if response.json().get("ok") is not True:
                    raise RuntimeError("Local service check failed")
                response = client.get(url + "api/settings")
                response.raise_for_status()
                if "api_key" in response.json():
                    raise RuntimeError("Settings must not expose API keys")
            print("Local service check passed", flush=True)
        elif args.browser:
            webbrowser.open(url)
            threading.Event().wait()
        else:
            import webview
            window = webview.create_window("月下圆桌", url, width=1440, height=960, min_size=(800, 600))
            window.events.closing += lambda: runtime.call(runtime.engine.pause())
            webview.start(private_mode=False, storage_path=str((args.data_dir or data) / "webview"))
    except KeyboardInterrupt:
        pass
    finally:
        runtime.call(runtime.engine.pause())
        runtime.http.shutdown()
        runtime.http.server_close()


if __name__ == "__main__":
    main()
