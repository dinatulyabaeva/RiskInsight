"""Async stdio MCP client with handshake, bounded messages, timeout and cleanup."""
import asyncio
import json
import logging
import os
from pathlib import Path
import sys
import time
from .mcp_server import PROTOCOL_VERSION

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger("riskinsight.mcp_client")

class MCPError(RuntimeError):
    pass

class MCPClient:
    def __init__(self, database: Path, timeout=20):
        self.database = Path(database)
        self.timeout = timeout
        self.process = None
        self.next_id = 0
        self.events = []
        self.lock = asyncio.Lock()

    async def __aenter__(self):
        # The child receives no LLM key, access token, or other inherited credentials.
        env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "LANG") if k in os.environ}
        env["PYTHONIOENCODING"] = "utf-8"
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.mcp_server", "--database", str(self.database), cwd=ROOT,
            env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=1_048_576,
        )
        try:
            result = await self.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "riskinsight-web", "version": "1.0.0"}})
            if result.get("protocolVersion") != PROTOCOL_VERSION or "tools" not in result.get("capabilities", {}):
                raise MCPError("Несовместимая версия или capabilities MCP")
            self.server_info = result["serverInfo"]
            await self.notify("notifications/initialized")
            return self
        except BaseException:
            await self.close()
            raise

    async def notify(self, method, params=None):
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
        await self.process.stdin.drain()
        self.events.append({"direction": "out", "method": method, "notification": True})

    async def request(self, method, params=None):
        async with self.lock:
            self.next_id += 1
            rid = self.next_id
            payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
            self.events.append({"direction": "out", "method": method, "id": rid})
            self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
            await self.process.stdin.drain()
            try:
                raw = await asyncio.wait_for(self.process.stdout.readline(), self.timeout)
            except TimeoutError:
                await self.notify("notifications/cancelled", {"requestId": rid, "reason": "Timeout"})
                raise MCPError("MCP timeout") from None
            if not raw:
                raise MCPError("MCP server disconnected")
            try:
                msg = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise MCPError("Invalid MCP JSON response") from None
            if msg.get("jsonrpc") != "2.0" or msg.get("id") != rid:
                raise MCPError("MCP response id mismatch")
            self.events.append({"direction": "in", "id": rid, "error": "error" in msg})
            if "error" in msg:
                raise MCPError(msg["error"].get("message", "MCP error"))
            return msg["result"]

    async def list_tools(self):
        return (await self.request("tools/list"))["tools"]

    async def call_tool(self, name, arguments):
        result = await self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise MCPError("; ".join(c.get("text", "") for c in result.get("content", [])))
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        try:
            return json.loads(result["content"][0]["text"])
        except (KeyError, IndexError, ValueError):
            raise MCPError("MCP tool returned no structured data") from None

    async def close(self):
        if self.process is None:
            return
        if self.process.stdin:
            self.process.stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(), 2)
        except TimeoutError:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 2)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        self.process = None

    async def __aexit__(self, *_):
        await self.close()


class EvidenceSession:
    """Only MCP can produce evidence; no direct imports of analytics in the agent."""
    def __init__(self, client: MCPClient):
        self.client = client
        self.trace = []

    async def call(self, name, arguments=None):
        arguments = arguments or {}
        reference = f"T{len(self.trace) + 1}"
        t0 = time.perf_counter()
        entry = {"ref": reference, "tool": name, "arguments": arguments}
        try:
            data = await self.client.call_tool(name, arguments)
            entry.update({"ok": True, "result": data})
        except MCPError as exc:
            entry.update({"ok": False, "error": str(exc)[:2000]})
            data = {"error": entry["error"]}
        entry["duration_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        self.trace.append(entry)
        return {"source_ref": reference, **data}
