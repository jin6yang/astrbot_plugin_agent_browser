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
GH_PROXY_URLS = [
    "https://ghfast.top/",
    "https://ghproxy.net/",
]

class ObscuraManager:
    _versions_cache = []
    _versions_cache_time = 0

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir if base_dir else Path(os.getcwd())
        self.obscura_dir = self.base_dir / "obscura"
        self.manifest_path = self.obscura_dir / "obscura_manifest.json"

    def _get_executable_path(self) -> Path:
        if platform.system().lower() == "windows":
            return self.obscura_dir / "obscura.exe"
        return self.obscura_dir / "obscura"

    def _get_worker_path(self) -> Path:
        if platform.system().lower() == "windows":
            return self.obscura_dir / "obscura-worker.exe"
        return self.obscura_dir / "obscura-worker"

    @staticmethod
    def _sha256_file(path: Path) -> str:
        sha256_hash = hashlib.sha256()
        with open(path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def _get_worker_status(self, manifest: Dict[str, Any]) -> Dict[str, Any]:
        worker_path = self._get_worker_path()
        if not worker_path.exists():
            return {
                "installed": False,
                "status": "missing",
                "message": "obscura-worker 缺失，将使用单进程模式，建议重新安装"
            }

        expected_hash = manifest.get("worker_sha256")
        if not expected_hash:
            return {
                "installed": True,
                "status": "unknown",
                "message": "清单缺少 worker 校验信息，环境可能不完整，建议重新安装"
            }

        try:
            if self._sha256_file(worker_path) != expected_hash:
                return {
                    "installed": True,
                    "status": "sha256_mismatch",
                    "message": "obscura-worker 已被篡改或损坏，建议重新安装"
                }
        except Exception as e:
            return {
                "installed": True,
                "status": "hash_error",
                "message": f"worker 哈希计算失败: {e}"
            }

        return {"installed": True, "status": "ok", "message": "正常运行"}

    def get_local_status(self) -> Dict[str, Any]:
        """Returns the local installation status including integrity check."""
        executable = self._get_executable_path()
        has_executable = executable.exists()
        has_manifest = self.manifest_path.exists()

        if not has_executable and not has_manifest:
            if self._get_worker_path().exists():
                return {
                    "installed": False,
                    "version": None,
                    "status": "missing_executable",
                    "message": "检测到残留的 obscura-worker，但主程序和清单缺失，安装不完整，建议重新安装"
                }
            return {
                "installed": False,
                "version": None,
                "status": "not_installed",
                "message": "未安装"
            }

        if has_executable and not has_manifest:
            return {
                "installed": True,
                "version": "unknown",
                "status": "missing_manifest",
                "message": "清单文件丢失"
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

        worker_status = self._get_worker_status(manifest)
        message = "正常运行"
        if worker_status["status"] != "ok":
            message = f"正常运行（{worker_status['message']}）"

        return {
            "installed": True,
            "version": manifest.get("version"),
            "status": "ok",
            "message": message,
            "install_time": manifest.get("install_time"),
            "platform": manifest.get("platform", "unknown"),
            "worker": worker_status
        }

    async def get_versions(self, limit: int = 5, force: bool = False) -> List[Dict[str, Any]]:
        """Fetch latest releases from GitHub, with caching. Use force=True to bypass cache."""
        import time
        now = time.time()
        
        if not force and ObscuraManager._versions_cache and (now - ObscuraManager._versions_cache_time) < 43200:
            return ObscuraManager._versions_cache[:limit]
            
        async with aiohttp.ClientSession(trust_env=True) as session:
            try:
                async with session.get(GITHUB_API_URL, timeout=10) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    results = []
                    for release in data:
                        results.append({
                            "version": release.get("tag_name"),
                            "name": release.get("name"),
                            "published_at": release.get("published_at"),
                            "assets": release.get("assets", [])
                        })
                    ObscuraManager._versions_cache = results
                    ObscuraManager._versions_cache_time = now
                    return results[:limit]
            except Exception as e:
                logger.error(f"获取版本列表失败: {e}")
                if not ObscuraManager._versions_cache:
                    error_msg = str(e)
                    if "rate limit" in error_msg.lower() or "403" in error_msg:
                        raise ValueError("github_rate_limit")
                    raise ValueError("network_timeout")
                return ObscuraManager._versions_cache[:limit]

    async def get_latest_version(self) -> Optional[str]:
        versions = await self.get_versions(limit=1)
        if versions:
            return versions[0]["version"]
        return None

    def _match_asset(self, assets: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Find the correct asset for the current OS."""
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
                return asset

        # Fallback 1: Just OS key
        for asset in assets:
            name = asset.get("name", "").lower()
            if name.endswith(".zip") and os_key in name:
                return asset

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

        matched_asset = self._match_asset(target_release["assets"])
        if not matched_asset:
            return {"success": False, "message": f"在版本 {version} 中找不到适合当前系统的发布包。"}

        direct_url = matched_asset.get("browser_download_url")
        asset_name = matched_asset.get("name", "")
        # Remove "obscura-" and ".zip" to get the platform string (e.g. windows-amd64)
        platform_info = asset_name.replace("obscura-", "").replace(".zip", "")

        if use_proxy:
            candidate_urls = [f"{mirror}{direct_url}" for mirror in GH_PROXY_URLS] + [direct_url]
        else:
            candidate_urls = [direct_url]

        # Setup paths
        zip_path = self.base_dir / "obscura_temp.zip"
        download_timeout = aiohttp.ClientTimeout(total=300, connect=20, sock_read=60)

        # Download, with mirror fallback
        downloaded = False
        last_error: Optional[Exception] = None
        async with aiohttp.ClientSession(trust_env=True) as session:
            for index, candidate_url in enumerate(candidate_urls):
                if progress_callback:
                    if index == 0:
                        progress_callback("正在下载...", 10)
                    else:
                        progress_callback(f"正在尝试备用下载源（{index + 1}/{len(candidate_urls)}）...", 10)
                try:
                    async with session.get(candidate_url, timeout=download_timeout) as resp:
                        resp.raise_for_status()
                        total_size = int(resp.headers.get('content-length', 0))
                        received = 0

                        with open(zip_path, 'wb') as f:
                            async for chunk in resp.content.iter_chunked(8192):
                                f.write(chunk)
                                received += len(chunk)
                                if progress_callback and total_size > 0:
                                    percent = 10 + int((received / total_size) * 60)
                                    progress_callback(f"下载中... {received//1024}KB / {total_size//1024}KB", percent)
                        downloaded = True
                        break
                except Exception as e:
                    last_error = e
                    if zip_path.exists():
                        zip_path.unlink()

        if not downloaded:
            return {"success": False, "message": f"下载失败: {last_error}"}

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

        worker_executable = self._get_worker_path()
        worker_sha256 = None
        if worker_executable.exists():
            worker_sha256 = self._sha256_file(worker_executable)

        if platform.system().lower() != "windows":
            os.chmod(executable, 0o755)
            if worker_executable.exists():
                os.chmod(worker_executable, 0o755)

        # Calculate Hash
        current_hash = self._sha256_file(executable)

        # Write manifest
        manifest = {
            "version": version,
            "install_time": datetime.utcnow().isoformat() + "Z",
            "executable_sha256": current_hash,
            "worker_sha256": worker_sha256,
            "platform": platform_info
        }
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

        if progress_callback:
            progress_callback("安装完成！", 100)

        return {"success": True, "message": f"成功安装 {version}"}

    def uninstall(self) -> bool:
        """Removes the obscura binaries and manifest."""
        if not self.obscura_dir.exists():
            return False
            
        success = False
        
        # Remove obscura binaries
        for file_name in ["obscura", "obscura.exe", "obscura-worker", "obscura-worker.exe"]:
            file_path = self.obscura_dir / file_name
            if file_path.exists() and file_path.is_file():
                try:
                    file_path.unlink()
                    success = True
                except Exception:
                    pass
        
        # Remove manifest file
        if self.manifest_path.exists():
            try:
                self.manifest_path.unlink()
                success = True
            except Exception:
                pass
                
        return success
