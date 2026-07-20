import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_agent_browser.models import ObscuraError, SearchConfig, SearchResult  # noqa: E402
from astrbot_plugin_agent_browser.obscura_manager.core import ObscuraManager  # noqa: E402
from astrbot_plugin_agent_browser.obscura_service import ObscuraSearchService  # noqa: E402
from astrbot_plugin_agent_browser.obscura_worker import (  # noqa: E402
    ObscuraWorkerPool,
    ObscuraWorkerSession,
    resolve_obscura_worker_path,
)


class FakeStream:
    def __init__(self):
        self.writings: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writings.append(data)

    async def drain(self) -> None:
        pass


class FakeStdout:
    def __init__(self, responses: list[bytes]):
        self._responses = list(responses)

    async def readline(self) -> bytes:
        await asyncio.sleep(0)
        if not self._responses:
            return b""
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class HangingStdout:
    async def readline(self) -> bytes:
        await asyncio.sleep(60)
        return b""


class FakeProcess:
    def __init__(self, stdout, pid: int = 1234):
        self.stdin = FakeStream()
        self.stdout = stdout
        self.pid = pid
        self.killed = False
        self._waited = False

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self._waited = True
        return 0


def make_session(responses: list[bytes], timeout: float = 5) -> tuple[ObscuraWorkerSession, FakeProcess]:
    process = FakeProcess(FakeStdout(responses))
    return ObscuraWorkerSession(process, timeout), process


def ok_line(result) -> bytes:
    return (json.dumps({"ok": True, "result": result}) + "\n").encode("utf-8")


def err_line(message: str) -> bytes:
    return (json.dumps({"ok": False, "error": message}) + "\n").encode("utf-8")


class WorkerPathTests(unittest.TestCase):
    def test_resolve_worker_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "obscura.exe"
            binary.write_text("", encoding="utf-8")
            self.assertIsNone(resolve_obscura_worker_path(str(binary)))

            worker = Path(tmpdir) / "obscura-worker.exe"
            worker.write_text("", encoding="utf-8")
            self.assertEqual(resolve_obscura_worker_path(str(binary)), str(worker))


class ObscuraWorkerSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_sequence_single_navigate(self):
        session, process = make_session([
            ok_line({"title": "T", "url": "https://example.com"}),
            ok_line("<html></html>"),
            ok_line("body text"),
        ])

        nav = await session.navigate("https://example.com")
        html = await session.dump_html()
        text = await session.dump_text()

        self.assertEqual(nav["title"], "T")
        self.assertEqual(html, "<html></html>")
        self.assertEqual(text, "body text")

        commands = [json.loads(line.decode("utf-8"))["cmd"] for line in process.stdin.writings]
        self.assertEqual(commands, ["navigate", "dump_html", "dump_text"])

    async def test_error_response_raises_without_breaking(self):
        session, process = make_session([err_line("navigation failed"), ok_line("still alive")])

        with self.assertRaises(ObscuraError) as ctx:
            await session.navigate("https://example.com")
        self.assertIn("navigation failed", str(ctx.exception))
        self.assertFalse(session.broken)
        self.assertFalse(process.killed)

        self.assertEqual(await session.dump_text(), "still alive")

    async def test_eof_marks_broken_and_kills(self):
        session, process = make_session([])

        with self.assertRaises(ObscuraError):
            await session.navigate("https://example.com")
        self.assertTrue(session.broken)
        self.assertTrue(process.killed)

        with self.assertRaises(ObscuraError):
            await session.dump_text()

    async def test_invalid_json_marks_broken(self):
        session, process = make_session([b"not json\n"])

        with self.assertRaises(ObscuraError):
            await session.dump_html()
        self.assertTrue(session.broken)
        self.assertTrue(process.killed)

    async def test_timeout_marks_broken_and_kills(self):
        process = FakeProcess(HangingStdout())
        session = ObscuraWorkerSession(process, 0.05)

        with self.assertRaises(ObscuraError):
            await session.navigate("https://example.com")
        self.assertTrue(session.broken)
        self.assertTrue(process.killed)

    async def test_close_is_idempotent_and_sends_shutdown(self):
        session, process = make_session([])

        await session.close()
        await session.close()

        self.assertEqual(process.stdin.writings, [b'{"cmd": "shutdown"}\n'])
        self.assertTrue(process._waited)
        self.assertFalse(process.killed)

    async def test_close_kills_broken_session(self):
        session, process = make_session([])
        with self.assertRaises(ObscuraError):
            await session.navigate("https://example.com")

        await session.close()
        self.assertTrue(process.killed)


class FakePoolSession:
    def __init__(self):
        self.broken = False
        self.closed = False
        self.commands: list[str] = []

    async def close(self) -> None:
        self.closed = True


class ObscuraWorkerPoolTests(unittest.IsolatedAsyncioTestCase):
    def make_pool(self, size: int = 2) -> tuple[ObscuraWorkerPool, list[FakePoolSession]]:
        created: list[FakePoolSession] = []

        async def factory() -> FakePoolSession:
            session = FakePoolSession()
            created.append(session)
            return session

        return ObscuraWorkerPool("worker", size=size, session_factory=factory), created

    async def test_lazy_create_and_reuse(self):
        pool, created = self.make_pool(size=2)

        async with pool:
            async with pool.acquire() as s1:
                self.assertEqual(len(created), 1)
            async with pool.acquire() as s2:
                self.assertIs(s1, s2)
            self.assertEqual(len(created), 1)

    async def test_broken_session_discarded_and_recreated(self):
        pool, created = self.make_pool(size=1)

        async with pool:
            async with pool.acquire() as s1:
                s1.broken = True
            self.assertTrue(s1.closed)
            async with pool.acquire() as s2:
                self.assertIsNot(s1, s2)
            self.assertEqual(len(created), 2)

    async def test_close_shuts_down_all_sessions(self):
        pool, created = self.make_pool(size=2)

        async with pool:
            async with pool.acquire():
                pass
            async with pool.acquire():
                pass
        self.assertTrue(all(session.closed for session in created))

    async def test_acquire_after_close_raises(self):
        pool, _ = self.make_pool(size=1)
        await pool.close()

        with self.assertRaises(ObscuraError):
            async with pool.acquire():
                pass


class FakeWorkerSession:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.navigations: list[str] = []

    async def navigate(self, url: str):
        self.navigations.append(url)
        if self.fail:
            raise ObscuraError("worker boom")

    async def dump_text(self) -> str:
        return "Worker body text"

    async def dump_html(self) -> str:
        return "<h1>Worker Heading</h1>"


class FakePool:
    def __init__(self, session: FakeWorkerSession):
        self.session = session

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.session

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


class ServiceWorkerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self, **overrides) -> ObscuraSearchService:
        config = SearchConfig(enable_media_extraction=True, **overrides)
        service = ObscuraSearchService(config)
        service.fetch_calls: list[tuple[str, str]] = []

        async def fake_fetch(url: str, *, dump: str = "text") -> str:
            service.fetch_calls.append((url, dump))
            return "CLI text" if dump == "text" else "<h1>CLI Heading</h1>"

        service.fetch = fake_fetch
        return service

    async def test_worker_path_single_navigate(self):
        service = self.make_service()
        session = FakeWorkerSession()
        pool = FakePool(session)

        content, page = await service._fetch_result_evidence(
            SearchResult(title="Example", url="https://example.com"),
            needs_content=True,
            needs_evidence=True,
            pool=pool,
        )

        self.assertEqual(session.navigations, ["https://example.com"])
        self.assertEqual(service.fetch_calls, [])
        self.assertEqual(content, "Worker body text")
        self.assertIn("Worker Heading", page.headings)

    async def test_worker_failure_falls_back_to_cli(self):
        service = self.make_service()
        session = FakeWorkerSession(fail=True)
        pool = FakePool(session)

        content, page = await service._fetch_result_evidence(
            SearchResult(title="Example", url="https://example.com"),
            needs_content=True,
            needs_evidence=True,
            pool=pool,
        )

        self.assertEqual(content, "CLI text")
        self.assertIn("CLI Heading", page.headings)
        self.assertEqual(
            service.fetch_calls,
            [("https://example.com", "text"), ("https://example.com", "html")],
        )

    async def test_create_worker_pool_disabled(self):
        service = self.make_service(enable_worker_pool=False)
        self.assertIsNone(service._create_worker_pool(3))

    async def test_create_worker_pool_with_binaries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "obscura.exe"
            binary.write_text("", encoding="utf-8")
            worker = Path(tmpdir) / "obscura-worker.exe"
            worker.write_text("", encoding="utf-8")

            service = self.make_service(obscura_path=str(binary), max_workers=2)
            pool = service._create_worker_pool(5)

            self.assertIsNotNone(pool)
            self.assertEqual(pool._size, 2)

    async def test_create_worker_pool_skipped_for_custom_user_agent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            binary = Path(tmpdir) / "obscura.exe"
            binary.write_text("", encoding="utf-8")
            worker = Path(tmpdir) / "obscura-worker.exe"
            worker.write_text("", encoding="utf-8")

            service = self.make_service(obscura_path=str(binary), user_agent="Custom UA")
            self.assertIsNone(service._create_worker_pool(3))


class ManagerWorkerStatusTests(unittest.TestCase):
    def make_manager(self, tmpdir: str, *, worker_sha256="include") -> ObscuraManager:
        import hashlib
        import json as _json

        obscura_dir = Path(tmpdir) / "obscura"
        obscura_dir.mkdir(parents=True, exist_ok=True)
        binary = obscura_dir / "obscura.exe"
        binary.write_bytes(b"main-binary")
        worker = obscura_dir / "obscura-worker.exe"
        worker.write_bytes(b"worker-binary")

        manifest = {
            "version": "v0.0.0-test",
            "install_time": "2026-01-01T00:00:00Z",
            "executable_sha256": hashlib.sha256(b"main-binary").hexdigest(),
            "platform": "windows-amd64",
        }
        if worker_sha256 == "include":
            manifest["worker_sha256"] = hashlib.sha256(b"worker-binary").hexdigest()
        elif worker_sha256 == "wrong":
            manifest["worker_sha256"] = "0" * 64
        (obscura_dir / "obscura_manifest.json").write_text(
            _json.dumps(manifest), encoding="utf-8"
        )
        return ObscuraManager(base_dir=Path(tmpdir))

    def test_worker_ok(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            status = self.make_manager(tmpdir).get_local_status()
            self.assertEqual(status["status"], "ok")
            self.assertEqual(status["message"], "正常运行")
            self.assertEqual(status["worker"]["status"], "ok")

    def test_worker_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = self.make_manager(tmpdir)
            (Path(tmpdir) / "obscura" / "obscura-worker.exe").unlink()

            status = manager.get_local_status()
            self.assertEqual(status["status"], "ok")
            self.assertEqual(status["worker"]["status"], "missing")
            self.assertIn("单进程模式", status["message"])

    def test_worker_unknown_for_legacy_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            status = self.make_manager(tmpdir, worker_sha256="legacy").get_local_status()
            self.assertEqual(status["status"], "ok")
            self.assertEqual(status["worker"]["status"], "unknown")
            self.assertIn("建议重新安装", status["message"])

    def test_worker_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            status = self.make_manager(tmpdir, worker_sha256="wrong").get_local_status()
            self.assertEqual(status["status"], "ok")
            self.assertEqual(status["worker"]["status"], "sha256_mismatch")


    def test_worker_only_residue_is_abnormal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            obscura_dir = Path(tmpdir) / "obscura"
            obscura_dir.mkdir(parents=True)
            (obscura_dir / "obscura-worker.exe").write_bytes(b"worker-binary")

            status = ObscuraManager(base_dir=Path(tmpdir)).get_local_status()
            self.assertEqual(status["status"], "missing_executable")
            self.assertIn("重新安装", status["message"])

    def test_empty_dir_is_not_installed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            status = ObscuraManager(base_dir=Path(tmpdir)).get_local_status()
            self.assertEqual(status["status"], "not_installed")


if __name__ == "__main__":
    unittest.main()
