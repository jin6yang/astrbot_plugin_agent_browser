import asyncio
import hashlib
import json
import logging
import os
import platform
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import aiohttp

logger = logging.getLogger(__name__)

GITHUB_REPO = "h4ckf0r0day/obscura"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
GH_PROXY_URL = "https://ghproxy.net/"

class ObscuraManager:
    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir if base_dir else Path(os.getcwd())
        self.obscura_dir = self.base_dir / "obscura"
        self.manifest_path = self.obscura_dir / "obscura_manifest.json"

    def _get_executable_path(self) -> Path:
        if platform.system().lower() == "windows":
            return self.obscura_dir / "obscura.exe"
        return self.obscura_dir / "obscura"

    def get_local_status(self) -> Dict[str, Any]:
        """Returns the local installation status including integrity check."""
        if not self.manifest_path.exists() or not self.obscura_dir.exists():
            return {
                "installed": False,
                "version": None,
                "status": "not_installed",
                "message": "未安装"
            }

        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as e:
            return {
                "installed": True,
                "version": "unknown",
                "status": "manifest_error",
                "message": f"清单读取失败: {e}"
            }

        executable = self._get_executable_path()
        if not executable.exists():
             return {
                "installed": True,
                "version": manifest.get("version"),
                "status": "missing_executable",
                "message": "可执行文件丢失"
            }

        # Calculate SHA256
        sha256_hash = hashlib.sha256()
        try:
            with open(executable, "rb") as f:
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            current_hash = sha256_hash.hexdigest()
        except Exception as e:
            return {
                "installed": True,
                "version": manifest.get("version"),
                "status": "hash_error",
                "message": f"哈希计算失败: {e}"
            }

        expected_hash = manifest.get("executable_sha256")
        if current_hash != expected_hash:
            return {
                "installed": True,
                "version": manifest.get("version"),
                "status": "sha256_mismatch",
                "message": "文件已被篡改或损坏"
            }

        return {
            "installed": True,
            "version": manifest.get("version"),
            "status": "ok",
            "message": "正常运行",
            "install_time": manifest.get("install_time")
        }

    async def get_versions(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Fetch latest releases from GitHub."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(GITHUB_API_URL, timeout=10) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    results = []
                    for release in data[:limit]:
                        results.append({
                            "version": release.get("tag_name"),
                            "name": release.get("name"),
                            "published_at": release.get("published_at"),
                            "assets": release.get("assets", [])
                        })
                    return results
            except Exception as e:
                logger.error(f"获取版本列表失败: {e}")
                return []

    async def get_latest_version(self) -> Optional[str]:
        versions = await self.get_versions(limit=1)
        if versions:
            return versions[0]["version"]
        return None

    def _match_asset(self, assets: List[Dict[str, Any]]) -> Optional[str]:
        """Find the correct asset URL for the current OS."""
        sys_os = platform.system().lower()
        arch = platform.machine().lower()

        # Simplify arch names
        if arch in ["x86_64", "amd64"]:
            arch_key = "amd64"
        elif arch in ["arm64", "aarch64"]:
            arch_key = "arm64"
        else:
            arch_key = arch

        os_key = sys_os
        if sys_os == "darwin":
            os_key = "darwin"
        elif sys_os == "windows":
            os_key = "windows"
        
        # We look for a zip that contains os_key and arch_key
        for asset in assets:
            name = asset.get("name", "").lower()
            if name.endswith(".zip") and os_key in name and arch_key in name:
                return asset.get("browser_download_url")

        # Fallback 1: Just OS key
        for asset in assets:
            name = asset.get("name", "").lower()
            if name.endswith(".zip") and os_key in name:
                return asset.get("browser_download_url")

        return None

    async def install(self, version: str, force: bool = False, use_proxy: bool = False, progress_callback=None) -> Dict[str, Any]:
        """Download and install the specified version."""
        status = self.get_local_status()
        if not force and status.get("installed") and status.get("version") == version and status.get("status") == "ok":
            return {"success": True, "message": f"版本 {version} 已经正确安装。"}

        versions = await self.get_versions(limit=20)
        target_release = next((v for v in versions if v["version"] == version), None)
        if not target_release:
            return {"success": False, "message": f"找不到版本: {version}"}

        download_url = self._match_asset(target_release["assets"])
        if not download_url:
            return {"success": False, "message": f"在版本 {version} 中找不到适合当前系统的发布包。"}

        if use_proxy:
            download_url = f"{GH_PROXY_URL}{download_url}"

        # Setup paths
        zip_path = self.base_dir / "obscura_temp.zip"
        
        if progress_callback:
            progress_callback("正在下载...", 10)

        # Download
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(download_url, timeout=300) as resp:
                    resp.raise_for_status()
                    total_size = int(resp.headers.get('content-length', 0))
                    downloaded = 0
                    
                    with open(zip_path, 'wb') as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_callback and total_size > 0:
                                percent = 10 + int((downloaded / total_size) * 60)
                                progress_callback(f"下载中... {downloaded//1024}KB / {total_size//1024}KB", percent)
            except Exception as e:
                if zip_path.exists():
                    zip_path.unlink()
                return {"success": False, "message": f"下载失败: {e}"}

        if progress_callback:
            progress_callback("正在清理旧版本...", 75)
            
        # Uninstall old version completely
        self.uninstall()

        if progress_callback:
            progress_callback("正在解压...", 80)

        # Extract
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                # Often zips contain a single root folder, we extract all to obscura_dir
                # But to avoid obscura/obscura-windows/..., we might need to flatten it.
                # For simplicity, extract all. If there is a top level folder, we can move files later,
                # but standard practice is assuming obscura executable is at the root or within the first directory.
                self.obscura_dir.mkdir(parents=True, exist_ok=True)
                zip_ref.extractall(self.obscura_dir)
                
            # Quick check if it extracted into a subfolder
            contents = list(self.obscura_dir.iterdir())
            if len(contents) == 1 and contents[0].is_dir():
                subfolder = contents[0]
                for item in subfolder.iterdir():
                    shutil.move(str(item), str(self.obscura_dir))
                subfolder.rmdir()
                
        except Exception as e:
            if zip_path.exists():
                zip_path.unlink()
            return {"success": False, "message": f"解压失败: {e}"}
        finally:
            if zip_path.exists():
                zip_path.unlink()

        if progress_callback:
            progress_callback("校验并生成清单...", 90)

        # Make executable
        executable = self._get_executable_path()
        if not executable.exists():
            return {"success": False, "message": "解压后未找到核心可执行文件。"}
            
        if platform.system().lower() != "windows":
            os.chmod(executable, 0o755)

        # Calculate Hash
        sha256_hash = hashlib.sha256()
        with open(executable, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        current_hash = sha256_hash.hexdigest()

        # Write manifest
        manifest = {
            "version": version,
            "install_time": datetime.utcnow().isoformat() + "Z",
            "executable_sha256": current_hash
        }
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

        if progress_callback:
            progress_callback("安装完成！", 100)

        return {"success": True, "message": f"成功安装 {version}"}

    def uninstall(self) -> bool:
        """Removes the obscura directory."""
        if self.obscura_dir.exists():
            shutil.rmtree(self.obscura_dir, ignore_errors=True)
            return True
        return False
