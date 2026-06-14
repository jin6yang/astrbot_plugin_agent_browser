import { useEffect, useState } from "react";
import { Button, Card, ProgressBar, Alert, Chip, Spinner } from "@heroui/react";
import { fetchStatus, fetchVersions, installVersion, uninstall, fetchProgress } from "./api";
import SettingsDialog from "./components/SettingsDialog";

const SettingsIcon = () => (
  <svg 
    xmlns="http://www.w3.org/2000/svg" 
    width="24" 
    height="24" 
    viewBox="0 0 24 24" 
    fill="none" 
    stroke="currentColor" 
    strokeWidth="2" 
    strokeLinecap="round" 
    strokeLinejoin="round"
  >
    <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"></path>
    <circle cx="12" cy="12" r="3"></circle>
  </svg>
);

export default function App() {
  const [status, setStatus] = useState<any>(null);
  const [versions, setVersions] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  
  const [isInstalling, setIsInstalling] = useState(false);
  const [progressMsg, setProgressMsg] = useState("");
  const [progressPct, setProgressPct] = useState(0);
  
  const [showSettings, setShowSettings] = useState(false);
  const [useProxy, setUseProxy] = useState(() => localStorage.getItem("useProxy") === "true");
  const [backendError, setBackendError] = useState(false);

  useEffect(() => {
    localStorage.setItem("useProxy", String(useProxy));
  }, [useProxy]);

  const loadData = async () => {
    try {
      setBackendError(false);
      const st = await fetchStatus();
      setStatus(st);
      const vers = await fetchVersions();
      setVersions(vers);
    } catch (e) {
      console.error("Backend connection failed:", e);
      setBackendError(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  useEffect(() => {
    let interval: any;
    if (isInstalling) {
      interval = setInterval(async () => {
        try {
          const prg = await fetchProgress();
          setProgressMsg(prg.message);
          setProgressPct(prg.percent);
          if (!prg.is_installing) {
            setIsInstalling(false);
            if (prg.success) {
               // Reload after a short delay
               setTimeout(loadData, 1000);
            }
          }
        } catch (e) {
          console.error(e);
        }
      }, 1000);
    }
    return () => clearInterval(interval);
  }, [isInstalling]);

  const handleInstall = async (version: string, force: boolean) => {
    try {
      const res = await installVersion(version, force, useProxy);
      if (res.success) {
        setIsInstalling(true);
      } else {
        alert(res.message);
      }
    } catch(e) {
      alert("启动安装任务失败");
    }
  };

  const handleUninstall = async () => {
    if(!confirm("确定要卸载吗？")) return;
    try {
      const res = await uninstall();
      if(res.success) {
        loadData();
      }
    } catch(e) {
      console.error(e);
    }
  };

  if (loading) {
    return <div className="h-screen flex items-center justify-center"><Spinner /></div>;
  }

  const isTampered = status?.status === "sha256_mismatch";
  const isInstalled = status?.installed;
  const currentVersion = status?.version;
  const latestVersion = versions.length > 0 ? versions[0].version : null;

  return (
    <div className="min-h-screen bg-background text-foreground p-8 flex flex-col items-center">
      <div className="w-full max-w-3xl flex justify-between items-center mb-8">
        <div>
          <h1 className="text-3xl font-bold">Obscura 管理器</h1>
          <p className="text-default-500">用于管理 Obscura 浏览器及其运行环境</p>
        </div>
        <Button 
          isIconOnly 
          variant="flat" 
          onPress={() => setShowSettings(true)}
          className="transition-all duration-300 hover:rotate-90 hover:scale-110 active:scale-95"
        >
          <SettingsIcon />
        </Button>
      </div>

      {backendError && (
        <Alert className="w-full max-w-3xl mb-4 bg-yellow-100 dark:bg-yellow-900/30 text-yellow-800 dark:text-yellow-400 border-none">
          <Alert.Indicator className="text-yellow-600 dark:text-yellow-500" />
          <Alert.Content>
            <Alert.Title>无法连接到后端</Alert.Title>
            <Alert.Description>检测到 WebUI 无法与后端的 Python 管理服务通信。请检查 Python 环境是否正常启动，或 AstrBot 服务是否发生异常崩溃。</Alert.Description>
          </Alert.Content>
        </Alert>
      )}

      <Card className="w-full max-w-3xl mb-8">
        <Card.Header className="flex flex-col items-start px-6 pt-6">
          <p className="text-sm uppercase font-bold text-default-500">安装状态</p>
          <div className="flex items-center gap-3 mt-1">
            <h2 className="text-2xl font-semibold">
              {isInstalled ? currentVersion : "未安装"}
            </h2>
            {isInstalled && (
              <Chip color={isTampered ? "danger" : "success"} variant="flat">
                {isTampered ? "文件异常" : "正常"}
              </Chip>
            )}
          </div>
        </Card.Header>
        <hr className="border-divider my-2" />
        <Card.Content className="px-6 py-6 gap-6">
          {isTampered && (
            <Alert color="danger">
              <Alert.Indicator />
              <Alert.Content>
                <Alert.Title>安全警告</Alert.Title>
                <Alert.Description>检测到核心可执行文件被修改或损坏。为确保系统安全与正常运行，请立刻强制重新安装。</Alert.Description>
              </Alert.Content>
            </Alert>
          )}

          {isInstalling ? (
            <div className="w-full flex flex-col gap-2">
              <div className="flex justify-between text-sm">
                <span>{progressMsg || "准备中..."}</span>
                <span>{progressPct}%</span>
              </div>
              <ProgressBar value={progressPct} color="primary" className="h-2" />
            </div>
          ) : (
            <div className="flex gap-4">
              {latestVersion && (!isInstalled || currentVersion !== latestVersion) && (
                <Button color="primary" onPress={() => handleInstall(latestVersion, false)}>
                  {isInstalled ? `更新到 ${latestVersion}` : `安装最新版 (${latestVersion})`}
                </Button>
              )}
              {isInstalled && (
                <Button color={isTampered ? "danger" : "primary"} variant={isTampered ? "solid" : "flat"} onPress={() => handleInstall(currentVersion, true)}>
                  强制重新安装
                </Button>
              )}
              {isInstalled && (
                <Button color="danger" variant="light" onPress={handleUninstall}>
                  卸载
                </Button>
              )}
            </div>
          )}
        </Card.Content>
      </Card>

      <Card className="w-full max-w-3xl">
        <Card.Header className="px-6 pt-6">
          <h3 className="text-lg font-bold">历史版本</h3>
        </Card.Header>
        <hr className="border-divider my-2" />
        <Card.Content className="px-6 py-4">
          <div className="flex flex-col gap-4">
            {versions.map((v: any, index: number) => (
              <div key={v.version} className="flex justify-between items-center">
                <div>
                  <p className="font-medium">{v.version} {index === 0 && <Chip size="sm" color="primary" variant="flat" className="ml-2">最新</Chip>}</p>
                  <p className="text-xs text-default-500">{new Date(v.published_at).toLocaleString()}</p>
                </div>
                <Button 
                  size="sm" 
                  variant="flat" 
                  isDisabled={isInstalling || v.version === currentVersion}
                  onPress={() => handleInstall(v.version, true)}
                >
                  {v.version === currentVersion ? "当前版本" : "回退"}
                </Button>
              </div>
            ))}
          </div>
        </Card.Content>
      </Card>

      <SettingsDialog 
        isOpen={showSettings} 
        onClose={() => setShowSettings(false)}
        useProxy={useProxy}
        setUseProxy={setUseProxy}
      />
    </div>
  );
}
