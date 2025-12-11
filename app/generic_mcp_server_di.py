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
from websockets.protocol import State

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
# DB SESSION
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
BACKEND_URL: Optional[str] = None


# -----------------------------------------------------
# WS STATE CHECK
# -----------------------------------------------------
def ws_is_open(ws):
    return ws is not None and ws.state == State.OPEN


# -----------------------------------------------------
# BACKEND READER LOOP (THE ONLY recv())
# -----------------------------------------------------
async def backend_reader_loop():
    """
    Only place in the entire server that calls ws.recv().
    Reads backend responses and resolves matching futures.
    """
    global WS_BACKEND, WS_PENDING

    LOG.info("Backend reader loop started")

    while True:
        ws = WS_BACKEND
        if ws is None or not ws_is_open(ws):
            await asyncio.sleep(1)
            continue

        try:
            raw = await ws.recv()  # THE ONLY .recv()
        except ConnectionClosed:
            LOG.warning("Backend WS closed. Reader will retry.")
            await asyncio.sleep(2)
            continue
        except Exception as e:
            LOG.error(f"Backend reader error: {e}")
            await asyncio.sleep(2)
            continue

        # Handle incoming message
        try:
            msg = json.loads(raw)
        except Exception:
            LOG.warning("Non-JSON backend message ignored.")
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


# -----------------------------------------------------
# SINGLE STARTUP CONNECTOR
# -----------------------------------------------------
async def backend_ws_bootstrap():
    """
    Connects ONCE at server start, launches the single reader task.
    Reconnect logic is handled lazily in RPC calls.
    """
    global WS_BACKEND, WS_READER_TASK, BACKEND_URL

    while True:
        try:
            LOG.info(f"[BOOT] Connecting to backend WebSocket: {BACKEND_URL}")
            WS_BACKEND = await websockets.connect(BACKEND_URL)
            WS_READER_TASK = asyncio.create_task(backend_reader_loop())
            LOG.info("[BOOT] Backend connected")
            return
        except Exception as e:
            LOG.error(f"[BOOT] Backend connect failed: {e}, retrying in 3s")
            await asyncio.sleep(3)


# -----------------------------------------------------
# SEND RPC (no recv here!)
# -----------------------------------------------------
async def send_backend_rpc(method: str, params: dict, timeout: int = 10):
    global WS_BACKEND

    # Lazy reconnect if needed
    if not ws_is_open(WS_BACKEND):
        LOG.warning("Backend WS not connected. Attempting reconnect...")
        await backend_ws_bootstrap()

    msg_id = uuid.uuid4().hex
    fut = asyncio.get_running_loop().create_future()
    WS_PENDING[msg_id] = fut

    msg = {"id": msg_id, "method": method, "params": params}

    try:
        await WS_BACKEND.send(json.dumps(msg))
    except Exception as e:
        WS_PENDING.pop(msg_id, None)
        raise RuntimeError(f"WS send failed: {e}")

    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except Exception:
        WS_PENDING.pop(msg_id, None)
        raise


# -----------------------------------------------------
# TOOL EXECUTION
# -----------------------------------------------------
async def execute_python_function(spec, args, ctx):
    fn = load_python_callable(spec["func"])
    try:
        if inspect.iscoroutinefunction(fn):
            return await fn(**args)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: fn(**args))
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
    t = spec.get("type")
    if t == "websocket":
        timeout = spec.get("timeout", 10)
        return await send_backend_rpc(name, args, timeout)
    if t == "python_function":
        return await execute_python_function(spec, args, {})
    if t == "script":
        return await execute_script(spec, args, {})
    raise ValueError(f"Unknown tool type: {t}")


# -----------------------------------------------------
# TOOL REGISTRY
# -----------------------------------------------------
class ToolRegistry:
    def __init__(self, spec):
        self.spec = spec
        self.tools = spec.get("tools", {})

    def list_tools(self):
        return self.spec

    def get_tool(self, name):
        return self.tools.get(name)

    def as_mcp_tools(self):
        if not MCP_AVAILABLE:
            return None
        res = []
        for name, spec in self.tools.items():
            params = spec.get("params", {})
            res.append(
                mcp_schema.Tool(
                    name=name,
                    description=spec.get("description", ""),
                    parameters=[mcp_schema.ToolParameter(name=p, description="") for p in params.keys()]
                )
            )
        return res


# -----------------------------------------------------
# RPC HELPERS
# -----------------------------------------------------
def rpc_result(msg_id, result):
    return json.dumps({"id": msg_id, "result": result}, default=str)

def rpc_error(msg_id, code, message):
    return json.dumps({"id": msg_id, "error": {"code": code, "message": message}})


# -----------------------------------------------------
# MCP SERVER WS HANDLER
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

        # ---------------- list_tools ----------------
        if method == "list_tools":
            tools = registry.as_mcp_tools() or registry.list_tools()
            await websocket.send(rpc_result(msg_id, tools))
            continue

        # ---------------- call_tool ----------------
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
# SERVER START
# -----------------------------------------------------
def serve(spec: dict, host="0.0.0.0", port=8765):
    global BACKEND_URL

    BACKEND_URL = spec["websocket"]["url"]
    registry = ToolRegistry(spec)

    async def run():
        await backend_ws_bootstrap()  # connect once
        LOG.info(f"MCP Server running on ws://{host}:{port}")
        async with websockets.serve(lambda ws: ws_handler(ws, registry, spec), host, port):
            await asyncio.Future()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        LOG.info("Shutting down MCP server")
