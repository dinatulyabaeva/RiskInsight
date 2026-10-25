"""MCP stdio server, protocol revision 2025-06-18, tools/resources subset.

This is a small implementation of the documented JSON-RPC wire protocol,
not an HTTP route named 'MCP'. It runs in a separate OS process. Only supported
capabilities are advertised. SDK interoperability smoke script is provided.
Stdout is reserved for protocol messages; all diagnostics go to stderr.
"""
import argparse
import json
import logging
import sys
from pathlib import Path
from pydantic import ValidationError
from .analytics import Analytics
from .tool_registry import list_tools, execute_tool, METHODOLOGY, TOOLS

PROTOCOL_VERSION = "2025-06-18"
RESOURCE_URI = "riskinsight://methodology"
MAX_LINE = 1_048_576
logger = logging.getLogger("riskinsight.mcp")


def error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


class ProtocolServer:
    def __init__(self, database):
        self.analytics = Analytics(database)
        self.negotiated = False
        self.ready = False

    def handle(self, message):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid Request")
        has_id = "id" in message
        rid = message.get("id")
        method = message["method"]
        params = message.get("params", {})
        if not has_id:
            if method == "notifications/initialized" and self.negotiated:
                self.ready = True
            # Valid unknown notifications are ignored; no response is ever sent.
            return None
        if not isinstance(rid, (int, str)) or isinstance(rid, bool):
            return error(None, -32600, "Request id must be a string or integer")
        if not isinstance(params, dict):
            return error(rid, -32602, "Params must be an object")
        try:
            if method == "initialize":
                if self.negotiated:
                    return error(rid, -32600, "Already initialized")
                if not isinstance(params.get("protocolVersion"), str) or not isinstance(params.get("capabilities"), dict) or not isinstance(params.get("clientInfo"), dict):
                    return error(rid, -32602, "Invalid initialization parameters")
                self.negotiated = True
                result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}, "resources": {"subscribe": False, "listChanged": False}},
                          "serverInfo": {"name": "riskinsight-analytics", "version": "1.0.0"},
                          "instructions": "Read-only synthetic portfolio analytics. Call get_dataset_context first. Monetary amounts are RUB, ratios are fractions."}
            elif method == "ping":
                result = {}
            elif not self.ready:
                return error(rid, -32000, "Initialize the MCP session first")
            elif method == "tools/list":
                if params.get("cursor"):
                    return error(rid, -32602, "This server has one page; omit cursor")
                result = {"tools": list_tools()}
            elif method == "tools/call":
                name = params.get("name")
                args = params.get("arguments", {})
                if not isinstance(name, str) or name not in TOOLS or not isinstance(args, dict):
                    return error(rid, -32602, "Unknown tool or invalid arguments object")
                try:
                    data = execute_tool(self.analytics, name, args)
                    result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, allow_nan=False)}], "structuredContent": data, "isError": False}
                except (ValidationError, ValueError) as exc:
                    # Validation errors are useful to the model; strip pydantic input echoes.
                    text = json.dumps(exc.errors(include_url=False, include_input=False), ensure_ascii=False, default=str) if isinstance(exc, ValidationError) else str(exc)
                    result = {"content": [{"type": "text", "text": text[:2000]}], "isError": True}
            elif method == "resources/list":
                result = {"resources": [{"uri": RESOURCE_URI, "name": "Методология RiskInsight", "mimeType": "text/plain"}]}
            elif method == "resources/read":
                if params.get("uri") != RESOURCE_URI:
                    return error(rid, -32602, "Unknown resource")
                result = {"contents": [{"uri": RESOURCE_URI, "mimeType": "text/plain", "text": METHODOLOGY}]}
            elif method == "resources/templates/list":
                result = {"resourceTemplates": []}
            else:
                return error(rid, -32601, "Method not found")
            return {"jsonrpc": "2.0", "id": rid, "result": result}
        except Exception:
            logger.exception("MCP tool failure")
            return error(rid, -32603, "Internal analytics error; see server log")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    server = ProtocolServer(args.database)
    while True:
        raw = sys.stdin.buffer.readline(MAX_LINE + 1)
        if not raw:
            break
        if len(raw) > MAX_LINE:
            print(json.dumps(error(None, -32600, "Message too large")), flush=True)
            break
        try:
            message = json.loads(raw)
            result = server.handle(message)
        except (ValueError, UnicodeDecodeError):
            result = error(None, -32700, "Parse error")
        if result is not None:
            print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)

if __name__ == "__main__":
    main()
