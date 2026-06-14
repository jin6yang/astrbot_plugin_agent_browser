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
    versions = await manager.get_versions(limit=10)
    return web.json_response(versions)

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
    dist_dir = Path(os.getcwd()) / "web_ui" / "src" / "WebUI" / "dist"
    index_file = dist_dir / "index.html"
    if not index_file.exists():
        return web.Response(text="WebUI 还没有构建 (dist 目录不存在)。请先执行 npm run build。", content_type='text/html')
    return web.FileResponse(index_file)

def setup_routes(app):
    app.router.add_get('/api/status', get_status)
    app.router.add_get('/api/versions', get_versions)
    app.router.add_post('/api/uninstall', uninstall)
    app.router.add_post('/api/install', install)
    app.router.add_get('/api/progress', get_progress)
    
    # Static files routing
    dist_dir = Path(os.getcwd()) / "web_ui" / "src" / "WebUI" / "dist"
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
