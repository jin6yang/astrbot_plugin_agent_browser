import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Button,
  Card,
  ProgressBar,
  Alert,
  Chip,
  Spinner,
  Table,
  Modal,
  Label,
  TableHeader,
  TableColumn,
  TableBody,
  TableRow,
  TableCell,
  AlertDialog,
} from "@heroui/react";
import { ToastProvider, toast } from "@heroui/react";

import {
  fetchStatus,
  fetchVersions,
  installVersion,
  uninstall,
  fetchProgress,
} from "./api";
import { TiltIcon } from "./components/TiltIcon";
import SettingsDialog from "./components/SettingsDialog";
import LoadingScreen from "./components/LoadingScreen";

import {
  Gear,
  ArrowRotateRight,
  TrashBin,
  ArrowUturnCcwLeft,
  ArrowDownToSquare,
  ArrowsRotateRight,
  CheckShapeFill,
  Cloud,
  Layers3Diagonal,
} from "@gravity-ui/icons";

import obscuraIcon from "./assets/obscura_icon.png";

export default function App() {
  const [status, setStatus] = useState<any>(null);
  const [versions, setVersions] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showLoadingScreen, setShowLoadingScreen] = useState(true);

  useEffect(() => {
    if (loading) {
      setShowLoadingScreen(true);
    }
  }, [loading]);

  const [isInstalling, setIsInstalling] = useState(false);
  const [progressMsg, setProgressMsg] = useState("");
  const [progressPct, setProgressPct] = useState(0);

  type AlertStatus = "success" | "accent" | "danger" | "warning" | "default";
  interface AlertItem {
    id: string;
    status: AlertStatus;
    title: string;
    description: string;
    timestamp: number;
  }
  const [persistentAlerts, setPersistentAlerts] = useState<AlertItem[]>([]);

  const addPersistentAlert = (
    status: AlertStatus,
    title: string,
    description: string,
  ) => {
    const id = Math.random().toString(36).slice(2);
    setPersistentAlerts((prev) => {
      const filtered = prev.filter(
        (a) => !(a.status === status && a.title === title),
      );
      return [
        ...filtered,
        { id, status, title, description, timestamp: Date.now() },
      ];
    });
  };

  const [showSettings, setShowSettings] = useState(false);
  const [selectedVersion, setSelectedVersion] = useState<string | null>(null);
  const [useProxy, setUseProxy] = useState(
    () => localStorage.getItem("useProxy") === "true",
  );
  const [backendError, setBackendError] = useState(false);

  useEffect(() => {
    localStorage.setItem("useProxy", String(useProxy));
  }, [useProxy]);

  const loadData = async (
    showLoading = true,
    fetchVers = true,
    forceVers = false,
  ) => {
    let success = true;
    try {
      if (showLoading && !isInstalling) setLoading(true);
      setBackendError(false);
      const st = await fetchStatus();
      setStatus(st);

      if (fetchVers) {
        try {
          const vers = await fetchVersions(forceVers);
          setVersions(vers);
        } catch (e: any) {
          success = false;
          if (forceVers) {
            throw e;
          } else if (showLoading) {
            if (e.message === "github_rate_limit") {
              toast("达到检查上限", {
                description: "检查更新过于频繁，已触发 GitHub 速率限制，请稍后再试。",
                variant: "danger",
                timeout: 5000,
              });
            } else if (e.message === "network_timeout") {
              toast("请求超时", {
                description: "获取云端版本列表超时，请检查网络环境。",
                variant: "danger",
                timeout: 5000,
              });
            } else {
              toast("获取失败", {
                description: "获取云端版本列表发生错误：" + e.message,
                variant: "danger",
                timeout: 5000,
              });
            }
          }
        }
      }
    } catch (e: any) {
      console.error("Backend connection failed:", e);
      setBackendError(true);
      success = false;
      if (forceVers) throw e;
    } finally {
      if (showLoading) setLoading(false);
    }
    return success;
  };

  const handleCheckUpdate = () => {
    toast.promise(loadData(false, true, true), {
      loading: "正在检查更新...",
      success: "检查完毕，版本状态及版本列表已刷新。",
      error: (e: any) => {
        if (e.message === "github_rate_limit") {
          return "检查更新过于频繁，已触发 GitHub 速率限制，请稍后再试。";
        } else if (e.message === "network_timeout") {
          return "获取云端版本列表超时，请检查网络环境。";
        } else {
          return "获取云端版本列表发生错误：" + e.message;
        }
      },
    });
  };

  useEffect(() => {
    loadData(true, true, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const handleFocus = () => {
      if (!isInstalling) loadData(false, false, false);
    };
    window.addEventListener("focus", handleFocus);

    // 15 seconds: status only
    const statusInterval = setInterval(() => {
      if (!isInstalling) loadData(false, false, false);
    }, 15000);

    // Schedule check at 12:00 and 24:00
    let scheduleTimeout: ReturnType<typeof setTimeout>;
    const scheduleNextCheck = () => {
      const now = new Date();
      const next12 = new Date();
      next12.setHours(12, 0, 0, 0);
      const next24 = new Date();
      next24.setHours(24, 0, 0, 0);

      let delay = 0;
      if (now.getTime() < next12.getTime()) {
        delay = next12.getTime() - now.getTime();
      } else {
        delay = next24.getTime() - now.getTime();
      }

      scheduleTimeout = setTimeout(() => {
        if (!isInstalling) loadData(false, true, false);
        scheduleNextCheck();
      }, delay);
    };

    scheduleNextCheck();

    return () => {
      window.removeEventListener("focus", handleFocus);
      clearInterval(statusInterval);
      clearTimeout(scheduleTimeout);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isInstalling]);

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
              toast("操作成功", {
                description: prg.result_message || "任务已顺利完成。",
                variant: "success",
                timeout: 5000,
              });
              setTimeout(() => loadData(false, true, false), 1000);
            } else if (prg.success === false) {
              addPersistentAlert(
                "danger",
                "操作失败",
                prg.result_message || "任务执行过程中发生错误。",
              );
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
        toast("启动任务失败", { description: res.message || "未知错误", variant: "danger", timeout: 5000 });
      }
    } catch (e: any) {
      toast("启动任务失败", {
        description: "与后端通信发生异常：" + e.message,
        variant: "danger",
        timeout: 5000,
      });
    }
  };

  const handleUninstall = async () => {
    try {
      const res = await uninstall();
      if (res.success) {
        toast("卸载成功", { description: "插件已被顺利卸载。", variant: "success", timeout: 5000 });
        loadData(false, true, false);
      } else {
        addPersistentAlert("danger", "卸载失败", res.message || "未知错误");
      }
    } catch (e: any) {
      toast("卸载失败", { description: "与后端通信发生异常：" + e.message, variant: "danger", timeout: 5000 });
    }
  };

  if (showLoadingScreen) {
    return <LoadingScreen isAppReady={!loading} onFinish={() => setShowLoadingScreen(false)} />;
  }

  const workerStatus: string | undefined = status?.worker?.status;
  const isWorkerAbnormal = Boolean(workerStatus && workerStatus !== "ok");
  const isTampered =
    status?.status === "sha256_mismatch" ||
    status?.status === "manifest_error" ||
    status?.status === "missing_executable" ||
    status?.status === "missing_manifest" ||
    isWorkerAbnormal;
  const isInstalled = status?.installed;
  const currentVersion = status?.version;
  const latestVersion = versions.length > 0 ? versions[0].version : null;
  const platform = status?.platform || "Unknown";

  const isMaintenance =
    isInstalled &&
    !isTampered &&
    latestVersion &&
    currentVersion !== latestVersion;
  const isNormal = isInstalled && !isTampered && !isMaintenance;

  const computedAlerts: AlertItem[] = [...persistentAlerts];

  if (isTampered) {
    computedAlerts.push({
      id: "env_tampered",
      status: "danger",
      title: "安全警告",
      description:
        "检测到文件损坏或被修改，为确保安全与插件的正常运行，建议执行重新安装",
      timestamp: 0,
    });
  }

  if (backendError) {
    computedAlerts.push({
      id: "backend_error",
      status: "warning",
      title: "无法连接到后端",
      description:
        "检测到 WebUI 无法与后端的 Python 服务通信。请检查 Python 环境是否正常启动。",
      timestamp: 0,
    });
  }

  const statusPriority: Record<string, number> = {
    success: 1,
    accent: 2,
    danger: 3,
    warning: 4,
    default: 5,
  };

  computedAlerts.sort((a, b) => {
    const pA = statusPriority[a.status] || 5;
    const pB = statusPriority[b.status] || 5;
    if (pA !== pB) return pA - pB;
    return b.timestamp - a.timestamp;
  });

  return (
    <motion.div 
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.5 }}
      className="min-h-screen bg-background text-foreground p-8 flex flex-col items-center"
      onClick={(e: React.MouseEvent) => {
        const target = e.target as HTMLElement;
        if (!target.closest("table") && !target.closest("button")) {
          setSelectedVersion(null);
        }
      }}
    >
      <div className="w-full max-w-3xl flex justify-between items-center mb-8">
        <div>
          <h1 className="text-3xl font-bold">Obscura Agent Browser 看板</h1>
          <p className="text-default-500">管理插件及其运行环境</p>
        </div>
        <Button
          isIconOnly
          variant="flat"
          onPress={() => setShowSettings(true)}
          className="transition-all duration-300 hover:rotate-90 hover:scale-110 active:scale-95"
        >
          <Gear />
        </Button>
      </div>

      <div className="w-full max-w-3xl flex flex-col gap-4 mb-8">
        <AnimatePresence>
          {computedAlerts.map((alert) => (
            <motion.div
              key={alert.id}
              initial={{ opacity: 0, y: -20, scale: 0.95 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95, transition: { duration: 0.2 } }}
              transition={{ duration: 0.3 }}
            >
              <Alert
                status={alert.status === "default" ? undefined : alert.status}
              >
                <Alert.Indicator />
                <Alert.Content>
                  <Alert.Title>{alert.title}</Alert.Title>
                  <Alert.Description>{alert.description}</Alert.Description>
                </Alert.Content>
              </Alert>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>

      {/* 状态面板 (3-block Grid Layout) */}
      <div className="w-full max-w-3xl mb-8 grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* 左上：当前版本块 */}
        <Card className="md:col-span-2 shadow-sm border border-divider">
          <Card.Content className="p-6 flex flex-col justify-between h-full">
            <div>
              <div className="flex items-center gap-3">
                <h2 className="text-4xl font-semibold font-josefin">
                  {!isInstalled
                    ? "未安装"
                    : isTampered
                      ? "异常"
                      : currentVersion}
                </h2>
                <div className="flex flex-col gap-1">
                  {isNormal && (
                    <Chip color="success" variant="flat" size="sm">
                      正常
                    </Chip>
                  )}
                  {isMaintenance && (
                    <Chip color="warning" variant="flat" size="sm">
                      维护
                    </Chip>
                  )}
                  {isTampered && (
                    <Chip color="danger" variant="flat" size="sm">
                      异常
                    </Chip>
                  )}
                </div>
              </div>
            </div>

            <div className="flex gap-3 mt-6">
              <Button
                color="primary"
                variant={!isInstalled ? undefined : "tertiary"}
                isDisabled={isInstalling}
                onPress={() =>
                  handleInstall(
                    isInstalled ? currentVersion : latestVersion || "",
                    isInstalled,
                  )
                }
              >
                {!isInstalled ? <ArrowDownToSquare /> : <ArrowRotateRight />}
                {!isInstalled ? "安装" : "重新安装"}
              </Button>
              {isInstalled && (
                <AlertDialog>
                  <Button
                    color="danger"
                    variant="danger-soft"
                    isDisabled={isInstalling}
                  >
                    <TrashBin />
                    卸载
                  </Button>
                  <AlertDialog.Backdrop>
                    <AlertDialog.Container>
                      <AlertDialog.Dialog className="sm:max-w-[400px]">
                        <AlertDialog.CloseTrigger />
                        <AlertDialog.Header>
                          <AlertDialog.Icon status="danger" />
                          <AlertDialog.Heading>
                            永久删除 Obscura 二进制文件？
                          </AlertDialog.Heading>
                        </AlertDialog.Header>
                        <AlertDialog.Body>
                          <p>
                            注意，这将从您的设备中永久删除这个二进制文件，这是本插件的必备文件，这一行为无法撤销！
                          </p>
                        </AlertDialog.Body>
                        <AlertDialog.Footer>
                          <Button slot="close" variant="tertiary">
                            取消
                          </Button>
                          <Button
                            slot="close"
                            variant="danger"
                            onPress={handleUninstall}
                          >
                            确认删除
                          </Button>
                        </AlertDialog.Footer>
                      </AlertDialog.Dialog>
                    </AlertDialog.Container>
                  </AlertDialog.Backdrop>
                </AlertDialog>
              )}
            </div>
          </Card.Content>
        </Card>

        {/* 右上：额外信息块 */}
        <Card className="shadow-sm border border-divider flex flex-col justify-center items-center py-6">
          <TiltIcon src={obscuraIcon} alt="Obscura Icon" href="https://github.com/h4ckf0r0day/obscura" />
          <span className="font-josefin text-default-500 font-medium tracking-wide">
            {platform}
          </span>
        </Card>

        {/* 底部：更新块 */}
        <Card className="md:col-span-3 shadow-sm border border-divider">
          <Card.Content className="px-6 py-4">
            {isInstalling ? (
              <ProgressBar
                value={progressPct}
                color="primary"
                className="w-full gap-2"
              >
                <div className="flex justify-between w-full text-sm">
                  <Label>{progressMsg || "准备中..."}</Label>
                  <ProgressBar.Output />
                </div>
                <ProgressBar.Track>
                  <ProgressBar.Fill />
                </ProgressBar.Track>
              </ProgressBar>
            ) : (
              <div className="flex justify-between items-center">
                <span className="font-josefin text-2xl font-semibold flex items-center gap-2">
                  {latestVersion &&
                  isInstalled &&
                  currentVersion === latestVersion ? (
                    <>
                      <CheckShapeFill
                        className="text-success"
                        width={24}
                        height={24}
                      />
                      <span className="text-success text-lg">
                        当前已是最新版本！
                      </span>
                    </>
                  ) : latestVersion ? (
                    <>
                      {latestVersion}
                      {isInstalled && currentVersion !== latestVersion && (
                        <Chip size="sm" color="warning">
                          有可用更新
                        </Chip>
                      )}
                    </>
                  ) : (
                    "..."
                  )}
                </span>

                {latestVersion &&
                isInstalled &&
                currentVersion !== latestVersion ? (
                  <Button
                    color="primary"
                    onPress={() => handleInstall(latestVersion, false)}
                  >
                    <ArrowsRotateRight />
                    更新
                  </Button>
                ) : (
                  <Button
                    color="primary"
                    variant="tertiary"
                    onPress={handleCheckUpdate}
                  >
                    <Cloud />
                    检查更新
                  </Button>
                )}
              </div>
            )}
          </Card.Content>
        </Card>
      </div>

      <div className="w-full max-w-3xl mb-2 mt-4 px-6 flex flex-row justify-between items-center">
        <h3 className="text-lg font-bold m-0 flex items-center gap-2">
          <Layers3Diagonal width={20} height={20} />
          版本列表
        </h3>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant={
              selectedVersion === currentVersion ? "tertiary" : "secondary"
            }
            isDisabled={
              isInstalling ||
              !selectedVersion ||
              selectedVersion === currentVersion
            }
            onPress={() =>
              selectedVersion && handleInstall(selectedVersion, true)
            }
          >
            {selectedVersion === currentVersion ? (
              "当前版本"
            ) : (
              <>
                <ArrowDownToSquare />
                安装
              </>
            )}
          </Button>
        </div>
      </div>

      <Table className="w-full max-w-3xl mb-8">
        <Table.ScrollContainer>
          <Table.Content
            selectionMode="single"
            selectedKeys={
              selectedVersion ? new Set([selectedVersion]) : new Set()
            }
            onSelectionChange={(keys: any) => {
              const arr = Array.from(keys);
              setSelectedVersion(arr.length > 0 ? String(arr[0]) : null);
            }}
            aria-label="历史版本列表"
          >
            <Table.Header>
              <Table.Column>版本号</Table.Column>
              <Table.Column>发布时间</Table.Column>
            </Table.Header>
            <Table.Body>
              {versions.map((v: any, index: number) => (
                <Table.Row key={v.version} id={v.version}>
                  <Table.Cell>
                    <span className="font-medium">{v.version}</span>
                    {index === 0 && (
                      <Chip
                        size="sm"
                        color="success"
                        variant="flat"
                        className="ml-2"
                      >
                        最新
                      </Chip>
                    )}
                    {v.version === currentVersion && (
                      <Chip
                        size="sm"
                        color="accent"
                        variant="flat"
                        className="ml-2"
                      >
                        当前版本
                      </Chip>
                    )}
                  </Table.Cell>
                  <Table.Cell>
                    <span className="text-sm text-default-500">
                      {new Date(v.published_at).toLocaleString()}
                    </span>
                  </Table.Cell>
                </Table.Row>
              ))}
            </Table.Body>
          </Table.Content>
        </Table.ScrollContainer>
      </Table>

      <SettingsDialog
        isOpen={showSettings}
        onClose={() => setShowSettings(false)}
        useProxy={useProxy}
        setUseProxy={setUseProxy}
      />
      <ToastProvider placement="bottom" />
    </motion.div>
  );
}
