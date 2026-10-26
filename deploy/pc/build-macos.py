"""Build the existing desktop application as a macOS app ZIP and DMG."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


def check_startup(app: Path, work: Path) -> None:
    runtime = work / "startup-check"
    runtime.mkdir()
    (runtime / "cloud_upload.json").write_text('{"enabled": false}', encoding="utf-8")
    log_path = work / "startup.log"
    with log_path.open("w+") as log:
        process = subprocess.Popen([
            str(app / "Contents" / "MacOS" / "JimuMotion"), "--no-browser",
        ], env={**os.environ, "MOTION_DATA_DIR": str(runtime)}, stdout=log, stderr=log)
        try:
            for _ in range(60):
                if process.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen("http://127.0.0.1:8765/api/devices", timeout=1) as response:
                        payload = json.load(response)
                    with urllib.request.urlopen("http://127.0.0.1:8765/realtime", timeout=1) as response:
                        html = response.read().decode()
                    if payload.get("devices") and '<div id="app">' in html:
                        print("Packaged app startup: API and frontend route OK")
                        return
                except (OSError, ValueError):
                    pass
                time.sleep(1)
            log.seek(0)
            raise RuntimeError("Packaged app did not start:\n" + log.read())
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def build() -> None:
    if sys.platform != "darwin":
        raise SystemExit("Run this script on macOS or the GitHub Actions macOS runner.")
    repo = Path(__file__).resolve().parents[2]
    arch = platform.machine()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=repo.parent / f"JimuMotion-Mac-{arch}")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"Output already exists: {output}")
    source = repo / "vue_backend" / "runtime" / "cloud_upload.json"
    config = json.loads(source.read_text(encoding="utf-8-sig")) if source.is_file() else {}
    token = os.environ.get("JIMU_UPLOAD_TOKEN") or config.get("token", "")
    url = os.environ.get("JIMU_CLOUD_URL") or config.get("base_url", "http://39.102.212.53")
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path not in ("", "/"):
        raise SystemExit("JIMU_CLOUD_URL must use http(s)://host.")
    if len(token) < 32:
        raise SystemExit("Set the JIMU_UPLOAD_TOKEN secret before building.")

    subprocess.run(["npm", "run", "build"], cwd=repo / "vue_frontend", check=True)
    with tempfile.TemporaryDirectory(prefix="jimu-mac-build-") as folder:
        work = Path(folder)
        defaults = work / "defaults"
        shutil.copytree(repo / "vue_backend" / "defaults", defaults)
        (defaults / "cloud_upload.json").write_text(json.dumps({
            "enabled": True, "base_url": url.rstrip("/"), "token": token,
        }), encoding="utf-8")
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
            "--name", "JimuMotion", "--osx-bundle-identifier", "cn.jimu.motion",
            "--target-arch", arch, "--hidden-import", "pystray._darwin",
            "--hidden-import", "serial.tools.list_ports_osx",
            "--paths", str(repo / "vue_backend"),
            "--add-data", f"{repo / 'vue_frontend' / 'dist'}:static",
            "--add-data", f"{defaults}:defaults",
            "--distpath", str(work / "dist"), "--workpath", str(work / "build"),
            "--specpath", str(work), str(repo / "vue_backend" / "desktop_entry.py"),
        ], check=True)
        app = work / "dist" / "JimuMotion.app"
        info_path = app / "Contents" / "Info.plist"
        info = plistlib.loads(info_path.read_bytes())
        info["LSUIElement"] = True
        info_path.write_bytes(plistlib.dumps(info))
        subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
        check_startup(app, work)
        output.mkdir(parents=True)
        name = f"JimuMotion-Mac-{arch}"
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
                        str(app), str(output / f"{name}.app.zip")], check=True)
        stage = work / "dmg"
        stage.mkdir()
        shutil.move(str(app), stage / app.name)
        (stage / "Applications").symlink_to("/Applications")
        for attempt in range(4):
            result = subprocess.run([
                "hdiutil", "create", "-volname", "JimuMotion", "-srcfolder", str(stage),
                "-fs", "HFS+", "-format", "UDZO", "-ov", str(output / f"{name}.dmg"),
            ], capture_output=True, text=True)
            if result.returncode == 0:
                print(result.stdout)
                break
            if "Resource busy" not in result.stderr or attempt == 3:
                raise RuntimeError("DMG creation failed: " + result.stderr)
            print("macOS disk image resource busy; retrying")
            time.sleep(3)
    print(f"Packages created: {output}")


if __name__ == "__main__":
    build()
