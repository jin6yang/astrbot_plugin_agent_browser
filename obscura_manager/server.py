import asyncio
import os
import webbrowser
from pathlib import Path
from aiohttp import web
from .core import ObscuraManager

# Global state for installation progress
progress_state = {
    "is_installing": False,
    "message": "",
    "percent": 0,
    "success": None,
    "result_message": ""
}

manager = ObscuraManager()

async def get_status(request):
    status = manager.get_local_status()
    return web.json_response(status)

async def get_versions(request):
    try:
        force = request.query.get("force", "").lower() == "true"
        versions = await manager.get_versions(limit=10, force=force)
        return web.json_response(versions)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=500)

async def uninstall(request):
    if progress_state["is_installing"]:
        return web.json_response({"success": False, "message": "正在安装中，无法卸载。"}, status=400)
    success = manager.uninstall()
    return web.json_response({"success": success})

async def get_progress(request):
    return web.json_response(progress_state)

def progress_cb(msg: str, percent: int):
    progress_state["message"] = msg
    progress_state["percent"] = percent

async def do_install_task(version: str, force: bool, use_proxy: bool):
    progress_state["is_installing"] = True
    progress_state["message"] = "初始化..."
    progress_state["percent"] = 0
    progress_state["success"] = None
    progress_state["result_message"] = ""
    
    try:
        res = await manager.install(version, force=force, use_proxy=use_proxy, progress_callback=progress_cb)
        progress_state["success"] = res.get("success")
        progress_state["result_message"] = res.get("message")
    except Exception as e:
        progress_state["success"] = False
        progress_state["result_message"] = f"安装时发生异常: {str(e)}"
    finally:
        progress_state["is_installing"] = False
        progress_state["percent"] = 100

async def install(request):
    if progress_state["is_installing"]:
        return web.json_response({"success": False, "message": "已经有安装任务在运行中。"}, status=400)
    
    data = await request.json()
    version = data.get("version")
    force = data.get("force", False)
    use_proxy = data.get("use_proxy", False)
    
    if not version:
        return web.json_response({"success": False, "message": "必须提供 version。"}, status=400)
        
    # Start task in background
    asyncio.create_task(do_install_task(version, force, use_proxy))
    
    return web.json_response({"success": True, "message": "安装任务已启动。"})

async def index(request):
    dist_dir = Path(os.getcwd()) / "web_ui" / "dist"
    index_file = dist_dir / "index.html"
    if not index_file.exists():
        return web.Response(text="WebUI 还没有构建 (dist 目录不存在)。请先执行 npm run build。", content_type='text/html')
    
    with open(index_file, "r", encoding="utf-8") as f:
        html_content = f.read()
    
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0"
    }
    return web.Response(text=html_content, content_type="text/html", headers=headers)

def setup_routes(app):
    app.router.add_get('/api/status', get_status)
    app.router.add_get('/api/versions', get_versions)
    app.router.add_post('/api/uninstall', uninstall)
    app.router.add_post('/api/install', install)
    app.router.add_get('/api/progress', get_progress)
    
    # Static files routing
    dist_dir = Path(os.getcwd()) / "web_ui" / "dist"
    if dist_dir.exists():
        app.router.add_static('/assets', path=str(dist_dir / "assets"), name='assets')
    app.router.add_get('/', index)

def main():
    app = web.Application()

    # CORS middleware for development if needed
    async def cors_factory(app, handler):
        async def cors_handler(request):
            if request.method == "OPTIONS":
                response = web.Response()
            else:
                response = await handler(request)
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
            return response
        return cors_handler

    app.middlewares.append(cors_factory)

    setup_routes(app)

    host = '127.0.0.1'

    # 端口配置：默认 8080，可用环境变量 OBSCURA_WEBUI_PORT 覆盖。
    #
    # 什么时候需要改端口？
    #   本机 8080 已被其它程序占用（例如 AstrBot 已经在跑一个 WebUI 实例）时，
    #   开发时可以临时换端口，避免冲突。
    #
    # 怎么设置（任选其一）？
    #   1) 会话级临时设置（推荐，重启终端即失效，不污染系统配置）：
    #        PowerShell:  $env:OBSCURA_WEBUI_PORT="8081"; python dev.py webui
    #        CMD:         set OBSCURA_WEBUI_PORT=8081 && python dev.py webui
    #   2) 系统级长期设置（每台开发机各自独立，设置后需重开终端生效）：
    #        Windows: SystemPropertiesAdvanced.exe -> 环境变量 -> 新建用户变量
    #
    # 注意：前端 vite 开发服务器（pnpm dev）的 /api 代理写死指向 8080。
    #   如果使用非默认端口做前端联调，需要同步修改
    #   web_ui/src/WebUI/vite.config.ts 中 server.proxy 的 target。
    #   生产模式（构建后的 dist 由本服务托管）不经过 vite，改端口无影响。
    try:
        port = int(os.environ.get("OBSCURA_WEBUI_PORT", "8080"))
    except ValueError:
        # 环境变量写成了非数字时，静默回退到默认端口，避免启动直接崩溃。
        port = 8080

    print(f"=========================================")
    print(f"Obscura Web UI 启动在 http://{host}:{port}")
    print(f"请不要关闭此窗口。关闭窗口即停止 Web UI。")
    print(f"=========================================")

    # Open browser automatically
    webbrowser.open(f"http://{host}:{port}")

    web.run_app(app, host=host, port=port, print=None)

if __name__ == "__main__":
    main()
