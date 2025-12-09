# app/generic_mcp_server_mcp.py

import asyncio
import importlib
import inspect
import json
import logging
import subprocess
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Optional

import websockets
from websockets.exceptions import ConnectionClosed

# Optional MCP
try:
    from mcp import types as mcp_types
    from mcp import schema as mcp_schema
    MCP_AVAILABLE = True
except Exception:
    mcp_types = None
    mcp_schema = None
    MCP_AVAILABLE = False

from app.ws_api import get_db

LOG = logging.getLogger("generic_mcp_server_mcp")
logging.basicConfig(level=logging.INFO)

# -----------------------------------------------------
# DB SESSION SCOPE
# -----------------------------------------------------
@contextmanager
def db_session_scope():
    with get_db() as db:
        yield db

# -----------------------------------------------------
# PYTHON IMPORT HELPER
# -----------------------------------------------------
def load_python_callable(path: str):
    module_name, fn_name = path.rsplit(".", 1)
    module = importlib.import_module(f"app.{module_name}")
    return getattr(module, fn_name)

# -----------------------------------------------------
# GLOBAL BACKEND WS (PERSISTENT)
# -----------------------------------------------------
WS_BACKEND: Optional[websockets.WebSocketClientProtocol] = None
WS_READER_TASK: Optional[asyncio.Task] = None
WS_PENDING: Dict[str, asyncio.Future] = {}
WS_LOCK = asyncio.Lock()
BACKEND_URL: Optional[str] = None

# -----------------------------------------------------
# HELPER: CHECK IF WS IS OPEN (websockets v12+)
# -----------------------------------------------------
def ws_is_open(ws):
    return ws is not None and ws.state == websockets.protocol.State.OPEN

# -----------------------------------------------------
# BACKEND READER LOOP
# -----------------------------------------------------
async def backend_reader_loop():
    """
    Persistent backend reader loop.
    Handles incoming messages from WS_BACKEND and sets futures in WS_PENDING.
    Reconnects automatically if backend closes.
    """
    global WS_BACKEND, WS_PENDING

    LOG.info("Backend reader loop started")

    while True:  # keep the reader alive
        ws = WS_BACKEND
        if ws is None:
            LOG.warning("WS_BACKEND is None, waiting for backend to connect...")
            await asyncio.sleep(1)
            continue

        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    LOG.warning("Non-JSON backend message discarded")
                    continue

                msg_id = msg.get("id")
                if not msg_id:
                    LOG.warning("Backend message missing id: %s", msg)
                    continue

                fut = WS_PENDING.pop(msg_id, None)
                if not fut:
                    LOG.warning("Unexpected backend response id=%s", msg_id)
                    continue

                if "error" in msg:
                    fut.set_exception(RuntimeError(msg["error"]))
                else:
                    fut.set_result(msg.get("result"))

        except ConnectionClosed:
            LOG.warning("Backend WS closed. Will attempt to reconnect...")
        except Exception as e:
            LOG.exception("Backend reader crashed: %s", e)
        finally:
            # Fail all pending RPC futures but keep the loop alive
            for mid, fut in list(WS_PENDING.items()):
                if not fut.done():
                    fut.set_exception(RuntimeError("Backend connection lost"))
                WS_PENDING.pop(mid, None)

            LOG.warning("Backend reader iteration finished. Retrying in 2s...")
            await asyncio.sleep(2)  # give time before reconnect attempt


# -----------------------------------------------------
# SINGLE BOOTSTRAP CONNECTOR — RUNS AT SERVER START
# -----------------------------------------------------
async def backend_ws_bootstrap():
    """
    Connect once at server startup and keep WS + reader running.
    Auto-reconnects on drop.
    """
    global WS_BACKEND, WS_READER_TASK, BACKEND_URL
    assert BACKEND_URL, "BACKEND_URL not set"

    while True:
        try:
            LOG.info(f"[BOOT] Connecting to backend WebSocket: {BACKEND_URL}")
            WS_BACKEND = await websockets.connect(BACKEND_URL)
            WS_READER_TASK = asyncio.create_task(backend_reader_loop())
            LOG.info("[BOOT] Backend connected successfully")
            return
        except Exception as e:
            LOG.error(f"[BOOT] Backend connect failed: {e}; retrying in 3s…")
            await asyncio.sleep(3)

# -----------------------------------------------------
# SEND RPC TO BACKEND WS
# -----------------------------------------------------
async def send_backend_rpc(method: str, params: dict, timeout: int = 10):
    global WS_BACKEND
    # Auto-reconnect if backend WS is closed
    if not ws_is_open(WS_BACKEND):
        LOG.warning("Backend WS not connected. Attempting to reconnect...")
        await backend_ws_bootstrap()
        if not ws_is_open(WS_BACKEND):
            raise RuntimeError("Backend WebSocket reconnect failed")

    msg_id = uuid.uuid4().hex
    fut = asyncio.get_running_loop().create_future()
    WS_PENDING[msg_id] = fut
    rpc_msg = {"id": msg_id, "method": method, "params": params}

    try:
        await WS_BACKEND.send(json.dumps(rpc_msg))
    except Exception as e:
        WS_PENDING.pop(msg_id, None)
        raise RuntimeError(f"Send failed: {e}")

    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except Exception:
        WS_PENDING.pop(msg_id, None)
        raise

# -----------------------------------------------------
# EXECUTE TOOL
# -----------------------------------------------------
async def execute_python_function(spec, args, ctx):
    fn = load_python_callable(spec["func"])
    kwargs = args.copy()
    try:
        if inspect.iscoroutinefunction(fn):
            return await fn(**kwargs)
        else:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, lambda: fn(**kwargs))
    except Exception as e:
        raise RuntimeError(f"python error: {e}")

async def execute_script(spec, args, ctx):
    path = spec["path"]
    param_list = [str(args.get(p, "")) for p in spec.get("params", [])]
    proc = subprocess.run(["python", path, *param_list], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    try:
        return json.loads(proc.stdout)
    except:
        return proc.stdout

async def execute_tool(name, spec, args, root_spec):
    tool_type = spec.get("type")
    if tool_type == "websocket":
        timeout = spec.get("timeout", 10)
        return await send_backend_rpc(name, args, timeout)
    if tool_type == "python_function":
        return await execute_python_function(spec, args, {})
    if tool_type == "script":
        return await execute_script(spec, args, {})
    raise ValueError(f"Unknown tool type {tool_type}")

# -----------------------------------------------------
# TOOL REGISTRY
# -----------------------------------------------------
class ToolRegistry:
    def __init__(self, spec):
        self.spec = spec
        self.tools = spec.get("tools", {})

    def list_tools(self):
        return self.tools

    def get_tool(self, name):
        return self.tools.get(name)

    def as_mcp_tools(self):
        if not MCP_AVAILABLE:
            return None
        result = []
        for name, spec in self.tools.items():
            params = spec.get("params", {})
            result.append(
                mcp_schema.Tool(
                    name=name,
                    description=spec.get("description", ""),
                    parameters=[mcp_schema.ToolParameter(name=p, description="") for p in params.keys()]
                )
            )
        return result

# -----------------------------------------------------
# JSON-RPC HELPERS
# -----------------------------------------------------
def rpc_result(msg_id, result):
    return json.dumps({"id": msg_id, "result": result}, default=str)

def rpc_error(msg_id, code, message):
    return json.dumps({"id": msg_id, "error": {"code": code, "message": message}})

# -----------------------------------------------------
# MCP WS HANDLER
# -----------------------------------------------------
async def ws_handler(websocket, registry, spec):
    LOG.info("Client connected")
    async for raw in websocket:
        try:
            msg = json.loads(raw)
        except:
            await websocket.send(rpc_error(None, -32700, "Invalid JSON"))
            continue

        msg_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params", {})

        if method == "list_tools":
            tools = registry.as_mcp_tools() or registry.list_tools()
            await websocket.send(rpc_result(msg_id, tools))
            continue

        if method == "call_tool":
            tname = params.get("name")
            args = params.get("args", {})
            spec_obj = registry.get_tool(tname)
            if not spec_obj:
                await websocket.send(rpc_error(msg_id, -32601, "Tool not found"))
                continue
            try:
                result = await execute_tool(tname, spec_obj, args, spec)
                await websocket.send(rpc_result(msg_id, result))
            except Exception as e:
                await websocket.send(rpc_error(msg_id, -32000, str(e)))
            continue

        await websocket.send(rpc_error(msg_id, -32601, "Unknown method"))

# -----------------------------------------------------
# SERVER START — BOOTSTRAPS BACKEND WS FIRST
# -----------------------------------------------------
def serve(spec: dict, host="0.0.0.0", port=8765):
    global BACKEND_URL
    BACKEND_URL = spec["websocket"]["url"]

    registry = ToolRegistry(spec)

    async def run():
        # 1️⃣ Connect to backend ONCE
        await backend_ws_bootstrap()
        # 2️⃣ Start MCP server
        LOG.info(f"MCP Server: ws://{host}:{port}")
        async with websockets.serve(lambda ws: ws_handler(ws, registry, spec), host, port):
            await asyncio.Future()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        LOG.info("Shutting down MCP server")
