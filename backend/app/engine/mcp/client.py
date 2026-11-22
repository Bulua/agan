from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import httpx

MCP_PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "agan-ctf", "version": "0.1.0"}


class MCPClientError(RuntimeError):
    """Raised when an MCP server fails to connect, initialize, or execute a JSON-RPC request."""


@dataclass
class MCPServerRuntimeConfig:
    name: str
    transport: str  # "stdio" | "streamable_http" | "sse"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    timeout_sec: int = 30


def _extract_jsonrpc_from_sse_text(raw_text: str, expected_id: int | None = None) -> dict[str, Any]:
    """Parse SSE frames from a Streamable HTTP response and return the matching JSON-RPC message."""
    last_obj: dict[str, Any] | None = None
    data_lines: list[str] = []

    def flush() -> dict[str, Any] | None:
        nonlocal data_lines
        if not data_lines:
            return None
        payload = "\n".join(data_lines).strip()
        data_lines = []
        if not payload:
            return None
        try:
            obj = json.loads(payload)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            return None
        return None

    for raw_line in raw_text.splitlines():
        line = raw_line.rstrip("\r")
        if line == "":
            obj = flush()
            if obj is not None:
                last_obj = obj
                if expected_id is not None and obj.get("id") == expected_id:
                    return obj
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())

    tail = flush()
    if tail is not None:
        last_obj = tail
        if expected_id is not None and tail.get("id") == expected_id:
            return tail

    if last_obj is not None:
        return last_obj
    raise MCPClientError("MCP HTTP stream did not contain a valid JSON-RPC response.")


class BaseMCPClient(ABC):
    """Abstract async JSON-RPC 2.0 MCP client supporting initialize, tools/list, and tools/call."""

    def __init__(self, config: MCPServerRuntimeConfig) -> None:
        self.config = config
        self._req_id = 0

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def __aenter__(self) -> BaseMCPClient:
        await self.connect()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()

    @abstractmethod
    async def connect(self) -> None:
        """Open transport connection."""

    @abstractmethod
    async def close(self) -> None:
        """Cleanly close transport connection."""

    @abstractmethod
    async def _send_request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send JSON-RPC request and return `result` field."""

    @abstractmethod
    async def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send JSON-RPC notification (no id, no response expected)."""

    async def initialize(self) -> dict[str, Any]:
        res = await self._send_request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        )
        await self._send_notification("notifications/initialized")
        return res if isinstance(res, dict) else {}

    async def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(20):
            params: dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor
            res = await self._send_request("tools/list", params)
            if not isinstance(res, dict):
                break
            batch = res.get("tools") or []
            for item in batch:
                if isinstance(item, dict) and item.get("name"):
                    schema = item.get("inputSchema") or item.get("input_schema") or {
                        "type": "object",
                        "properties": {},
                    }
                    tools.append(
                        {
                            "name": str(item["name"]),
                            "description": str(item.get("description") or ""),
                            "inputSchema": schema,
                        }
                    )
            cursor = res.get("nextCursor")
            if not cursor:
                break
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        res = await self._send_request(
            "tools/call",
            {
                "name": name,
                "arguments": arguments or {},
            },
        )
        return res if isinstance(res, dict) else {"content": [{"type": "text", "text": str(res)}]}


class StdioMCPClient(BaseMCPClient):
    """MCP client communicating over line-delimited JSON-RPC 2.0 via subprocess stdin/stdout."""

    def __init__(self, config: MCPServerRuntimeConfig) -> None:
        super().__init__(config)
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_chunks: list[str] = []
        self._stderr_task: asyncio.Task[None] | None = None

    async def _drain_stderr(self) -> None:
        if self._proc is None or self._proc.stderr is None:
            return
        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    self._stderr_chunks.append(text)
                    if len(self._stderr_chunks) > 40:
                        self._stderr_chunks.pop(0)
        except Exception:
            pass

    def _stderr_hint(self) -> str:
        if not self._stderr_chunks:
            return ""
        return " | stderr: " + " ; ".join(self._stderr_chunks[-5:])

    async def connect(self) -> None:
        cmd = (self.config.command or "").strip()
        if not cmd:
            raise MCPClientError("Stdio MCP server requires a non-empty `command`.")

        resolved_cmd = shutil.which(cmd) or cmd
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in (self.config.env or {}).items()})

        try:
            if sys.platform == "win32" and resolved_cmd.lower().endswith((".cmd", ".bat")):
                self._proc = await asyncio.create_subprocess_exec(
                    "cmd.exe",
                    "/c",
                    resolved_cmd,
                    *[str(a) for a in (self.config.args or [])],
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
            else:
                self._proc = await asyncio.create_subprocess_exec(
                    resolved_cmd,
                    *[str(a) for a in (self.config.args or [])],
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
        except FileNotFoundError as exc:
            raise MCPClientError(f"Command not found: '{cmd}'. Ensure it is installed in PATH.") from exc
        except Exception as exc:
            raise MCPClientError(f"Failed to start stdio MCP process '{cmd}': {exc}") from exc

        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is not None:
            try:
                if proc.stdin and not proc.stdin.is_closing():
                    proc.stdin.close()
            except Exception:
                pass
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except Exception:
                    try:
                        proc.kill()
                        await asyncio.wait_for(proc.wait(), timeout=2.0)
                    except Exception:
                        pass
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except BaseException:
                pass
            self._stderr_task = None

    async def _write_json(self, msg: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise MCPClientError("Stdio MCP process is not running.")
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        self._proc.stdin.write(data)
        await self._proc.stdin.drain()

    async def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        await self._write_json(msg)

    async def _send_request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if self._proc is None or self._proc.stdout is None:
            raise MCPClientError("Stdio MCP process is not running.")
        req_id = self._next_id()
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        await self._write_json(msg)

        async def _wait_reply() -> Any:
            assert self._proc is not None and self._proc.stdout is not None
            while True:
                raw_line = await self._proc.stdout.readline()
                if not raw_line:
                    rc = self._proc.returncode
                    raise MCPClientError(
                        f"Stdio MCP server exited unexpectedly (exit_code={rc}){self._stderr_hint()}"
                    )
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    # Skip non-JSON stdout banner lines emitted by some servers
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get("id") != req_id:
                    # Skip server notifications or unrelated messages
                    continue
                if "error" in obj and obj["error"] is not None:
                    err = obj["error"]
                    err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                    raise MCPClientError(f"MCP error ({method}): {err_msg}")
                return obj.get("result")

        try:
            return await asyncio.wait_for(_wait_reply(), timeout=float(self.config.timeout_sec))
        except TimeoutError as exc:
            raise MCPClientError(
                f"Timed out after {self.config.timeout_sec}s waiting for '{method}'{self._stderr_hint()}"
            ) from exc


class StreamableHttpMCPClient(BaseMCPClient):
    """MCP client for modern Streamable HTTP endpoints (`POST` JSON-RPC -> JSON or SSE)."""

    def __init__(self, config: MCPServerRuntimeConfig) -> None:
        super().__init__(config)
        self._http: httpx.AsyncClient | None = None
        self._session_id: str | None = None

    async def connect(self) -> None:
        if not (self.config.url or "").strip():
            raise MCPClientError("Streamable HTTP MCP server requires a non-empty `url`.")
        self._http = httpx.AsyncClient(
            timeout=float(self.config.timeout_sec),
            verify=False,
            follow_redirects=True,
        )

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        headers.update({str(k): str(v) for k, v in (self.config.headers or {}).items()})
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    async def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self._http is None:
            raise MCPClientError("HTTP client not connected.")
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        try:
            resp = await self._http.post(self.config.url, json=msg, headers=self._build_headers())
            sid = resp.headers.get("mcp-session-id")
            if sid:
                self._session_id = sid
        except Exception:
            # Notifications may return 202/204 or be ignored by stateless gateways
            pass

    async def _send_request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if self._http is None:
            raise MCPClientError("HTTP client not connected.")
        req_id = self._next_id()
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params

        try:
            resp = await self._http.post(self.config.url, json=msg, headers=self._build_headers())
        except Exception as exc:
            raise MCPClientError(f"HTTP request to MCP server failed: {exc}") from exc

        sid = resp.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid

        if resp.status_code >= 400:
            preview = resp.text[:300]
            raise MCPClientError(f"MCP HTTP {resp.status_code}: {preview}")

        ctype = (resp.headers.get("content-type") or "").lower()
        if "text/event-stream" in ctype:
            obj = _extract_jsonrpc_from_sse_text(resp.text, expected_id=req_id)
        else:
            try:
                obj = resp.json()
            except Exception as exc:
                raise MCPClientError(f"Invalid JSON from MCP server: {resp.text[:200]}") from exc

        if not isinstance(obj, dict):
            raise MCPClientError("Unexpected non-object JSON-RPC payload from MCP server.")
        if "error" in obj and obj["error"] is not None:
            err = obj["error"]
            err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise MCPClientError(f"MCP error ({method}): {err_msg}")
        return obj.get("result")


class SseMCPClient(BaseMCPClient):
    """MCP client for legacy HTTP+SSE transport (GET SSE stream + POST to advertised endpoint)."""

    def __init__(self, config: MCPServerRuntimeConfig) -> None:
        super().__init__(config)
        self._http: httpx.AsyncClient | None = None
        self._listener_task: asyncio.Task[None] | None = None
        self._endpoint_ready = asyncio.Event()
        self._post_url: str | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._stream_error: str | None = None

    async def connect(self) -> None:
        if not (self.config.url or "").strip():
            raise MCPClientError("SSE MCP server requires a non-empty `url`.")
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(float(self.config.timeout_sec), read=None),
            verify=False,
            follow_redirects=True,
        )
        self._listener_task = asyncio.create_task(self._listen_sse())
        try:
            await asyncio.wait_for(
                self._endpoint_ready.wait(), timeout=min(float(self.config.timeout_sec), 15.0)
            )
        except TimeoutError as exc:
            raise MCPClientError(
                f"Timed out waiting for MCP SSE `endpoint` event from {self.config.url}"
            ) from exc
        if self._stream_error:
            raise MCPClientError(self._stream_error)
        if not self._post_url:
            raise MCPClientError("SSE stream closed before advertising a message endpoint.")

    async def _listen_sse(self) -> None:
        assert self._http is not None
        headers = {"Accept": "text/event-stream"}
        headers.update({str(k): str(v) for k, v in (self.config.headers or {}).items()})
        try:
            async with self._http.stream("GET", self.config.url, headers=headers) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", errors="replace")[:240]
                    self._stream_error = f"MCP SSE HTTP {resp.status_code}: {body}"
                    self._endpoint_ready.set()
                    return

                event_name = "message"
                data_lines: list[str] = []

                async for raw_line in resp.aiter_lines():
                    line = raw_line.rstrip("\r")
                    if line == "":
                        if data_lines:
                            payload = "\n".join(data_lines).strip()
                            self._handle_sse_event(event_name, payload)
                        event_name = "message"
                        data_lines = []
                    elif line.startswith("event:"):
                        event_name = line[6:].strip() or "message"
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._stream_error = f"MCP SSE stream error: {exc}"
            self._endpoint_ready.set()
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(MCPClientError(self._stream_error))

    def _handle_sse_event(self, event_name: str, payload: str) -> None:
        if event_name == "endpoint":
            self._post_url = urljoin(self.config.url, payload)
            self._endpoint_ready.set()
            return
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            return
        if isinstance(obj, dict) and "id" in obj:
            req_id = obj.get("id")
            fut = self._pending.get(req_id)  # type: ignore[arg-type]
            if fut is not None and not fut.done():
                fut.set_result(obj)

    async def close(self) -> None:
        if self._listener_task is not None:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except BaseException:
                pass
            self._listener_task = None
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self._http is None or not self._post_url:
            return
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        headers = {"Content-Type": "application/json"}
        headers.update({str(k): str(v) for k, v in (self.config.headers or {}).items()})
        try:
            await self._http.post(
                self._post_url,
                json=msg,
                headers=headers,
                timeout=float(self.config.timeout_sec),
            )
        except Exception:
            pass

    async def _send_request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if self._http is None or not self._post_url:
            raise MCPClientError("SSE MCP client is not connected.")
        req_id = self._next_id()
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = fut

        headers = {"Content-Type": "application/json"}
        headers.update({str(k): str(v) for k, v in (self.config.headers or {}).items()})

        try:
            resp = await self._http.post(
                self._post_url,
                json=msg,
                headers=headers,
                timeout=float(self.config.timeout_sec),
            )
            if resp.status_code >= 400:
                raise MCPClientError(f"MCP SSE POST HTTP {resp.status_code}: {resp.text[:240]}")

            # Some hybrid servers reply directly in POST body instead of SSE channel
            if resp.status_code == 200 and resp.text.strip():
                try:
                    direct = resp.json()
                    if isinstance(direct, dict) and direct.get("id") == req_id:
                        if not fut.done():
                            fut.set_result(direct)
                except Exception:
                    pass

            obj = await asyncio.wait_for(fut, timeout=float(self.config.timeout_sec))
        except TimeoutError as exc:
            raise MCPClientError(
                f"Timed out after {self.config.timeout_sec}s waiting for MCP SSE reply '{method}'"
            ) from exc
        finally:
            self._pending.pop(req_id, None)

        if "error" in obj and obj["error"] is not None:
            err = obj["error"]
            err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise MCPClientError(f"MCP error ({method}): {err_msg}")
        return obj.get("result")


def create_mcp_client(config: MCPServerRuntimeConfig) -> BaseMCPClient:
    t = (config.transport or "stdio").strip().lower()
    if t == "stdio":
        return StdioMCPClient(config)
    if t == "streamable_http":
        return StreamableHttpMCPClient(config)
    if t == "sse":
        return SseMCPClient(config)
    raise MCPClientError(f"Unsupported MCP transport: '{config.transport}'.")
