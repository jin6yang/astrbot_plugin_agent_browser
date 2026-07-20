from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from .models import ObscuraError

WORKER_SHUTDOWN_TIMEOUT = 5

def resolve_obscura_worker_path(obscura_binary_path: str) -> str | None:
    binary = Path(obscura_binary_path)
    for name in ("obscura-worker.exe", "obscura-worker"):
        candidate = binary.parent / name
        if candidate.is_file():
            return str(candidate)
    return None

class ObscuraWorkerSession:
    def __init__(self, process: asyncio.subprocess.Process, timeout: float) -> None:
        self._process = process
        self._timeout = timeout
        self._lock = asyncio.Lock()
        self._broken = False
        self._closed = False

    @classmethod
    async def create(
        cls,
        worker_path: str,
        *,
        proxy: str = "",
        stealth: bool = False,
        timeout: float = 20,
    ) -> ObscuraWorkerSession:
        env = dict(os.environ)
        env["OBSCURA_PROXY"] = proxy or ""
        env["OBSCURA_STEALTH"] = "1" if stealth else ""
        try:
            process = await asyncio.create_subprocess_exec(
                worker_path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
            )
        except OSError as exc:
            raise ObscuraError(f"无法启动 obscura-worker：{exc}") from exc
        return cls(process, timeout)

    @property
    def broken(self) -> bool:
        return self._broken

    @property
    def pid(self) -> int:
        return self._process.pid

    async def navigate(self, url: str) -> dict[str, Any]:
        result = await self._command({"cmd": "navigate", "url": url}, self._timeout + 5)
        return result if isinstance(result, dict) else {}

    async def dump_html(self) -> str:
        result = await self._command({"cmd": "dump_html"}, self._timeout)
        return result if isinstance(result, str) else ""

    async def dump_text(self) -> str:
        result = await self._command({"cmd": "dump_text"}, self._timeout)
        return result if isinstance(result, str) else ""

    async def evaluate(self, expression: str) -> Any:
        return await self._command({"cmd": "evaluate", "expression": expression}, self._timeout)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._broken:
            self._kill()
            return
        try:
            async with self._lock:
                self._process.stdin.write(b'{"cmd": "shutdown"}\n')
                await self._process.stdin.drain()
        except Exception:
            pass
        try:
            await asyncio.wait_for(self._process.wait(), WORKER_SHUTDOWN_TIMEOUT)
        except asyncio.TimeoutError:
            self._kill()
        except Exception:
            pass

    async def _command(self, cmd: dict[str, Any], timeout: float) -> Any:
        if self._broken:
            raise ObscuraError("obscura-worker 会话已损坏。")
        if self._closed:
            raise ObscuraError("obscura-worker 会话已关闭。")

        async with self._lock:
            line = json.dumps(cmd, ensure_ascii=False) + "\n"
            try:
                self._process.stdin.write(line.encode("utf-8"))
                await self._process.stdin.drain()
                raw = await asyncio.wait_for(self._process.stdout.readline(), timeout)
            except Exception as exc:
                self._mark_broken()
                raise ObscuraError(f"obscura-worker 通信失败：{exc}") from exc

            if not raw:
                self._mark_broken()
                raise ObscuraError("obscura-worker 意外退出。")

            try:
                response = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError as exc:
                self._mark_broken()
                raise ObscuraError(f"obscura-worker 返回了非法响应：{exc}") from exc

            if not isinstance(response, dict) or not response.get("ok"):
                error = response.get("error") if isinstance(response, dict) else None
                raise ObscuraError(f"obscura-worker 执行失败：{error or response}")
            return response.get("result")

    def _mark_broken(self) -> None:
        self._broken = True
        self._kill()

    def _kill(self) -> None:
        try:
            self._process.kill()
        except ProcessLookupError:
            pass
        except Exception:
            pass

SessionFactory = Callable[[], Awaitable[ObscuraWorkerSession]]

class ObscuraWorkerPool:
    def __init__(
        self,
        worker_path: str,
        *,
        size: int,
        proxy: str = "",
        stealth: bool = False,
        timeout: float = 20,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self._worker_path = worker_path
        self._size = max(1, size)
        self._proxy = proxy
        self._stealth = stealth
        self._timeout = timeout
        self._session_factory = session_factory
        self._idle: list[ObscuraWorkerSession] = []
        self._total = 0
        self._closed = False
        self._cond = asyncio.Condition()

    async def __aenter__(self) -> ObscuraWorkerPool:
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[ObscuraWorkerSession]:
        session = await self._acquire()
        try:
            yield session
        finally:
            await self._release(session)

    async def close(self) -> None:
        async with self._cond:
            self._closed = True
            sessions = self._idle
            self._idle = []
            self._total -= len(sessions)
            self._cond.notify_all()
        await asyncio.gather(*(session.close() for session in sessions), return_exceptions=True)

    async def _acquire(self) -> ObscuraWorkerSession:
        to_close: list[ObscuraWorkerSession] = []
        try:
            async with self._cond:
                while True:
                    if self._closed:
                        raise ObscuraError("obscura-worker 池已关闭。")
                    while self._idle:
                        session = self._idle.pop()
                        if not session.broken:
                            return session
                        self._total -= 1
                        to_close.append(session)
                    if self._total < self._size:
                        self._total += 1
                        break
                    await self._cond.wait()
        finally:
            if to_close:
                await asyncio.gather(*(s.close() for s in to_close), return_exceptions=True)

        try:
            return await self._new_session()
        except Exception:
            async with self._cond:
                self._total -= 1
                self._cond.notify()
            raise

    async def _new_session(self) -> ObscuraWorkerSession:
        if self._session_factory is not None:
            return await self._session_factory()
        return await ObscuraWorkerSession.create(
            self._worker_path,
            proxy=self._proxy,
            stealth=self._stealth,
            timeout=self._timeout,
        )

    async def _release(self, session: ObscuraWorkerSession) -> None:
        should_close = False
        async with self._cond:
            if self._closed or session.broken:
                self._total -= 1
                should_close = True
            else:
                self._idle.append(session)
            self._cond.notify()
        if should_close:
            await session.close()
