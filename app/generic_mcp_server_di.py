# app/generic_mcp_server_mcp.py
import asyncio
import importlib
import inspect
import json
import logging
import subprocess
from contextlib import contextmanager
from typing import Any, Dict, Optional

import httpx
import websockets
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError

# try to import MCP helpers if available
try:
    from mcp import types as mcp_types
    from mcp import schema as mcp_schema
    MCP_AVAILABLE = True
except Exception:
    mcp_types = None
    mcp_schema = None
    MCP_AVAILABLE = False

# app helpers
from app.ws_api import get_db  # DB context and serializers live in app.ws_api

LOG = logging.getLogger("generic_mcp_server_mcp")
logging.basicConfig(level=logging.INFO)

# ---------------- DB context helper (async-safe) ----------------
@contextmanager
def db_session_scope():
    with get_db() as db:
        yield db

async def async_db_session_scope():
    """Run synchronous DB session in a thread to avoid blocking event loop."""
    return await asyncio.to_thread(lambda: db_session_scope())

# ---------------- dynamic loaders ----------------
def load_python_callable(path: str):
    module_name, fn_name = path.rsplit(".", 1)
    module = importlib.import_module(f"app.{module_name}")
    return getattr(module, fn_name)

def load_schema(schema_name: Optional[str]):
    if not schema_name:
        return None
    module = importlib.import_module("app.schemas")
    return getattr(module, schema_name)

def load_serializer(serializer_name: Optional[str]):
    if not serializer_name:
        return None
    module = importlib.import_module("app.ws_api")
    return getattr(module, serializer_name)

# ---------------- executors ----------------
async def execute_python_function(spec: Dict[str, Any], args: Dict[str, Any], ctx: Dict[str, Any]):
    fn = load_python_callable(spec["func"])
    serializer = load_serializer(spec.get("serializer"))
    schema_name = spec.get("schema")
    schema_cls = load_schema(schema_name) if schema_name else None

    kwargs: Dict[str, Any] = {}

    if schema_cls:
        try:
            data_obj = schema_cls(**(args or {}))
        except Exception as e:
            raise ValueError(f"schema construction failed: {e}")
        sig = inspect.signature(fn)
        if "data" in sig.parameters:
            kwargs["data"] = data_obj
        else:
            for pname in sig.parameters:
                if pname in ("self", "cls"):
                    continue
                kwargs[pname] = data_obj
                break
    else:
        kwargs.update(args or {})

    sig = inspect.signature(fn)
    for name, p in sig.parameters.items():
        if name not in kwargs and name in ctx:
            kwargs[name] = ctx[name]

    try:
        if inspect.iscoroutinefunction(fn):
            result = await fn(**kwargs)
        else:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, lambda: fn(**kwargs))
    except Exception as e:
        raise RuntimeError(f"python function error: {e}")

    if serializer:
        if isinstance(result, list):
            return [serializer(r) for r in result]
        return serializer(result)
    return result

async def execute_external_api(spec: Dict[str, Any], args: Dict[str, Any], ctx: Dict[str, Any]):
    method = spec.get("method", "GET").upper()
    endpoint = spec["endpoint"]
    timeout = spec.get("timeout", 10)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if method == "GET":
            resp = await client.get(endpoint, params=args)
        else:
            resp = await client.request(method, endpoint, json=args)
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return resp.text

async def execute_script(spec: Dict[str, Any], args: Dict[str, Any], ctx: Dict[str, Any]):
    path = spec["path"]
    params_list = [str(args.get(p, "")) for p in spec.get("params", [])]
    proc = subprocess.run(
        ["python", path, *params_list],
        capture_output=True,
        text=True,
        timeout=spec.get("timeout", 60)
    )
    if proc.returncode != 0:
        raise RuntimeError(f"script failed: {proc.stderr.strip()}")
    out = proc.stdout.strip()
    try:
        return json.loads(out) if out else None
    except Exception:
        return out

async def execute_tool(spec: Dict[str, Any], args: Dict[str, Any], ctx: Dict[str, Any]):
    tool_type = spec.get("type", "python_function")
    if tool_type == "python_function":
        def sync_call():
            with db_session_scope() as db:
                local_ctx = dict(ctx)
                local_ctx["db"] = db
                loop = asyncio.new_event_loop()
                return loop.run_until_complete(execute_python_function(spec, args or {}, local_ctx))
        return await asyncio.to_thread(sync_call)
    elif tool_type == "external_api":
        return await execute_external_api(spec, args or {}, ctx)
    elif tool_type == "script":
        return await execute_script(spec, args or {}, ctx)
    else:
        raise ValueError(f"unknown tool type: {tool_type}")

# ---------------- Tool registry ----------------
class ToolRegistry:
    def __init__(self, spec: Dict[str, Any]):
        self.spec = spec
        self.tools: Dict[str, Dict[str, Any]] = {}
        self._build()

    def _build(self):
        tools_section = self.spec.get("tools", {})
        for tool_name, group in tools_section.items():

            # skip non-dict groups
            if not isinstance(group, dict):
                continue

            for method_name, method_spec in group.items():
                # skip group-level description
                if method_name == "description":
                    continue

                fullname = f"{tool_name}.{method_name}"
                safe_spec = self.normalize_method_spec(method_spec)
                self.tools[fullname] = safe_spec

    def normalize_method_spec(self, raw):
        if not isinstance(raw, dict):
            if isinstance(raw, str):
                return {"description": raw}
            return {}

        allowed = {
            "type", "func", "params", "schema", "serializer",
            "method", "endpoint", "timeout", "path", "description", "id_param"
        }
        out = {}
        for k, v in raw.items():
            if k in allowed:
                out[k] = v

        if "params" not in out:
            out["params"] = raw.get("params", [])

        return out

    def list_tools(self):
        out = {}
        for fullname, spec in self.tools.items():
            out[fullname] = {
                "type": spec.get("type", "python_function"),
                "func": spec.get("func"),
                "params": spec.get("params", []),
                "schema": spec.get("schema"),
                "serializer": spec.get("serializer"),
                "description": spec.get("description", "")
            }
        return out

    def get_tool(self, fullname: str) -> Optional[Dict[str, Any]]:
        return self.tools.get(fullname)

    def as_mcp_tools(self):
        if not MCP_AVAILABLE or mcp_schema is None:
            return None
        mcp_tools = []
        for fullname, spec in self.tools.items():
            try:
                t = mcp_schema.Tool(
                    name=fullname,
                    description=spec.get("description", ""),
                    parameters=[
                        mcp_schema.ToolParameter(name=p, description="") for p in spec.get("params", [])
                    ],
                    metadata={"type": spec.get("type", "python_function"), "func": spec.get("func")}
                )
                mcp_tools.append(t)
            except Exception:
                continue
        return mcp_tools

# ---------------- JSON-RPC helpers ----------------
def rpc_result(msg_id, result):
    return json.dumps({"id": msg_id, "result": result}, default=str)

def rpc_error(msg_id, code, message):
    return json.dumps({"id": msg_id, "error": {"code": code, "message": message}})

# ---------------- WebSocket handler ----------------
async def ws_handler(websocket, registry: ToolRegistry, server_config: Dict[str, Any]):
    LOG.info("client connected")
    try:
        async for raw in websocket:
            try:
                message = json.loads(raw)
            except Exception:
                await websocket.send(rpc_error(None, -32700, "Parse error: invalid JSON"))
                continue

            msg_id = message.get("id")
            method = message.get("method")
            params = message.get("params", {})

            if not method:
                await websocket.send(rpc_error(msg_id, -32600, "Missing 'method'"))
                continue

            try:
                # MCP-standard methods
                if method == "list_tools":
                    mcp_tools = registry.as_mcp_tools()
                    result = mcp_tools if mcp_tools is not None else registry.list_tools()
                    await websocket.send(rpc_result(msg_id, result))
                    continue

                if method == "tool_info":
                    name = params.get("name")
                    if not name:
                        await websocket.send(rpc_error(msg_id, -32602, "Missing 'name' param for tool_info"))
                        continue
                    spec = registry.get_tool(name)
                    if not spec:
                        await websocket.send(rpc_error(msg_id, -32601, f"Tool '{name}' not found"))
                        continue
                    await websocket.send(rpc_result(msg_id, spec))
                    continue

                if method == "call_tool":
                    name = params.get("name")
                    args = params.get("args", {})
                    if not name:
                        await websocket.send(rpc_error(msg_id, -32602, "Missing 'name' param for call_tool"))
                        continue
                    tool_spec = registry.get_tool(name)
                    if not tool_spec:
                        await websocket.send(rpc_error(msg_id, -32601, f"Tool '{name}' not found"))
                        continue

                    ctx = {"logger": LOG, "config": server_config}
                    try:
                        result = await execute_tool(tool_spec, args, ctx)
                        await websocket.send(rpc_result(msg_id, result))
                    except Exception as e:
                        LOG.exception("tool execution error")
                        await websocket.send(rpc_error(msg_id, -32000, f"Tool execution error: {e}"))
                    continue

                await websocket.send(rpc_error(msg_id, -32601, f"Unknown method '{method}'"))
            except ConnectionClosedOK:
                break
            except Exception as e:
                LOG.exception("unhandled")
                await websocket.send(rpc_error(msg_id, -32000, f"Server error: {e}"))

    except (ConnectionClosedOK, ConnectionClosedError):
        LOG.info("client disconnected")
    except Exception:
        LOG.exception("ws handler top-level error")

# ---------------- server start helper ----------------
def serve(spec: dict, host="0.0.0.0", port=8765):
    registry = ToolRegistry(spec)
    server_config = spec.get("config", {})

    async def handler(websocket):  # only websocket argument, no path
        await ws_handler(websocket, registry, server_config)

    async def run_server():
        LOG.info(f"Starting MCP-style server on ws://{host}:{port} (MCP package available: {MCP_AVAILABLE})")
        async with websockets.serve(handler, host, port):
            LOG.info("Server started successfully")
            await asyncio.Future()  # run forever

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        LOG.info("Shutting down server")
