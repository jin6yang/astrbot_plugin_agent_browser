import { Modal, Button, Switch, Select, ListBox } from "@heroui/react";
import { useEffect, useState } from "react";

export default function SettingsDialog({ isOpen, onClose, useProxy, setUseProxy }: any) {
  // We can manage theme here or globally. A simple approach is adding/removing 'dark' class on HTML
  const [theme, setTheme] = useState(() => localStorage.getItem("theme") || "system");
  const [lang, setLang] = useState(() => localStorage.getItem("lang") || "zh");

  useEffect(() => {
    if (theme === "dark") {
      document.documentElement.classList.add("dark");
    } else if (theme === "light") {
      document.documentElement.classList.remove("dark");
    } else {
      // System
      if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
        document.documentElement.classList.add("dark");
      } else {
        document.documentElement.classList.remove("dark");
      }
    }
    localStorage.setItem("theme", theme);
  }, [theme]);

  useEffect(() => {
    localStorage.setItem("lang", lang);
  }, [lang]);

  return (
    <Modal isOpen={isOpen} onOpenChange={(open) => !open && onClose()}>
      <Modal.Backdrop>
        <Modal.Container>
          <Modal.Dialog>
            <Modal.Header>
              <Modal.Heading>设置</Modal.Heading>
            </Modal.Header>
            <Modal.Body>
              <div className="flex justify-between items-center py-2">
                <div>
                  <p className="font-semibold">GitHub 下载加速</p>
                  <p className="text-xs text-default-500">使用 ghproxy.net 加速下载</p>
                </div>
                <Switch isSelected={useProxy} onChange={setUseProxy}>
                  <Switch.Control>
                    <Switch.Thumb />
                  </Switch.Control>
                </Switch>
              </div>
              
              <div className="flex justify-between items-center py-2">
                <div>
                  <p className="font-semibold">界面语言</p>
                </div>
                <Select className="max-w-xs w-32" value={lang} onChange={(key: any) => setLang(key)}>
                  <Select.Trigger>
                    <Select.Value />
                    <Select.Indicator />
                  </Select.Trigger>
                  <Select.Popover>
                    <ListBox>
                      <ListBox.Item id="zh" textValue="简体中文">简体中文</ListBox.Item>
                      <ListBox.Item id="en" textValue="English">English</ListBox.Item>
                    </ListBox>
                  </Select.Popover>
                </Select>
              </div>

              <div className="flex justify-between items-center py-2">
                <div>
                  <p className="font-semibold">主题模式</p>
                </div>
                <Select className="max-w-xs w-32" value={theme} onChange={(key: any) => setTheme(key)}>
                  <Select.Trigger>
                    <Select.Value />
                    <Select.Indicator />
                  </Select.Trigger>
                  <Select.Popover>
                    <ListBox>
                      <ListBox.Item id="system" textValue="跟随系统">跟随系统</ListBox.Item>
                      <ListBox.Item id="light" textValue="浅色">浅色</ListBox.Item>
                      <ListBox.Item id="dark" textValue="深色">深色</ListBox.Item>
                    </ListBox>
                  </Select.Popover>
                </Select>
              </div>
            </Modal.Body>
            <Modal.Footer>
              <Button color="primary" onPress={onClose}>
                完成
              </Button>
            </Modal.Footer>
          </Modal.Dialog>
        </Modal.Container>
      </Modal.Backdrop>
    </Modal>
  );
}
