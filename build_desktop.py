"""在目标系统构建原生窗口桌面包；Windows 包必须在 Windows 构建。"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    output = args.output.resolve()
    if not (root / "web/dist/index.html").is_file():
        raise SystemExit("请先运行 npm ci 和 npm run build。")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="moon-desktop-build-") as work:
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
            "--name", "MoonTable", "--distpath", str(Path(work) / "dist"),
            "--workpath", str(Path(work) / "build"), "--specpath", work,
            "--add-data", f"{root / 'web/dist'}:web/dist",
            "--collect-all", "webview", "--hidden-import", "edge_tts",
            "--osx-bundle-identifier", "cn.moontable.desktop", str(root / "desktop.py"),
        ], check=True, cwd=root, env={**os.environ, "PYINSTALLER_CONFIG_DIR": str(Path(work) / "cache")})
        platform_name = ("macOS-AppleSilicon" if platform.machine() == "arm64" else "macOS-Intel") if sys.platform == "darwin" else "Windows" if sys.platform == "win32" else "Linux"
        bundle = Path(work) / "dist" / ("MoonTable.app" if sys.platform == "darwin" else "MoonTable")
        if sys.platform == "darwin":
            target = output / f"MoonTable-{platform_name}.zip"
            subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(bundle), str(target)], check=True)
        else:
            shutil.make_archive(str(output / f"MoonTable-{platform_name}"), "zip", bundle.parent, bundle.name)
        print(f"Generated: {output / ('MoonTable-' + platform_name + '.zip')}")


if __name__ == "__main__":
    main()
