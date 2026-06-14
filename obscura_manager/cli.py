import asyncio
import os
import sys
from .core import ObscuraManager

def print_status(status: dict):
    print("="*40)
    print("=== Obscura 浏览器管理工具 (CLI) ===")
    if not status.get("installed"):
        print("当前状态: 未安装")
    else:
        version = status.get("version", "未知")
        st = status.get("status")
        msg = status.get("message", "")
        if st == "ok":
            print(f"当前版本: {version} (完整性校验通过)")
        else:
            print(f"当前版本: {version} [异常: {msg}]")
    print("="*40)

def progress_cb(msg: str, percent: int):
    # Simple progress bar
    bar_length = 20
    filled = int(bar_length * percent / 100)
    bar = '=' * filled + '-' * (bar_length - filled)
    sys.stdout.write(f"\r[{bar}] {percent}% | {msg}")
    sys.stdout.flush()

async def async_main():
    manager = ObscuraManager()
    
    while True:
        os.system('cls' if os.name == 'nt' else 'clear')
        status = manager.get_local_status()
        print_status(status)
        
        print("\n[1] 检查并安装最新版")
        print("[2] 强制重新安装当前版本")
        print("[3] 回退到历史版本")
        print("[4] 卸载")
        print("[0] 退出")
        
        choice = input("\n请输入选项 (0-4): ").strip()
        
        if choice == '0':
            print("退出...")
            break
            
        elif choice == '1':
            print("\n正在获取最新版本信息...")
            latest = await manager.get_latest_version()
            if not latest:
                print("获取失败，请检查网络。")
            else:
                if status.get("installed") and status.get("version") == latest and status.get("status") == "ok":
                    print(f"当前已经是最新版本: {latest}")
                else:
                    print(f"发现最新版本: {latest}，开始安装...")
                    use_proxy = input("是否使用 GitHub 加速代理? (y/N): ").strip().lower() == 'y'
                    res = await manager.install(latest, force=True, use_proxy=use_proxy, progress_callback=progress_cb)
                    print(f"\n{res.get('message')}")
            input("\n按回车键继续...")
            
        elif choice == '2':
            if not status.get("installed") or not status.get("version"):
                print("当前未安装任何版本，无法重新安装。")
            else:
                ver = status.get("version")
                print(f"\n即将强制重新安装 {ver}...")
                use_proxy = input("是否使用 GitHub 加速代理? (y/N): ").strip().lower() == 'y'
                res = await manager.install(ver, force=True, use_proxy=use_proxy, progress_callback=progress_cb)
                print(f"\n{res.get('message')}")
            input("\n按回车键继续...")
            
        elif choice == '3':
            print("\n正在获取历史版本...")
            versions = await manager.get_versions(limit=5)
            if not versions:
                print("获取历史版本失败。")
            else:
                for i, v in enumerate(versions):
                    print(f"[{i+1}] {v['version']} ({v.get('published_at', '')})")
                
                v_choice = input(f"\n选择要回退的版本 (1-{len(versions)}，或 0 取消): ").strip()
                if v_choice.isdigit():
                    v_idx = int(v_choice)
                    if 1 <= v_idx <= len(versions):
                        target_ver = versions[v_idx-1]['version']
                        print(f"开始安装 {target_ver}...")
                        use_proxy = input("是否使用 GitHub 加速代理? (y/N): ").strip().lower() == 'y'
                        res = await manager.install(target_ver, force=True, use_proxy=use_proxy, progress_callback=progress_cb)
                        print(f"\n{res.get('message')}")
            input("\n按回车键继续...")
            
        elif choice == '4':
            confirm = input("\n确定要卸载 Obscura 吗？(y/N): ").strip().lower()
            if confirm == 'y':
                if manager.uninstall():
                    print("卸载成功。")
                else:
                    print("卸载失败或未发现已安装的文件。")
            input("\n按回车键继续...")

def main():
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\n已取消。")

if __name__ == "__main__":
    main()
