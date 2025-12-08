"""
mcp_client.py

Usage:
    python mcp_client.py --mcp ws://localhost:8765 --query "How many students in grade 1 are still not paid complete fees?"

What this does:
1. Uses an open-source LLM (gpt4all by default) to turn an English query into a JSON 'plan' of MCP tool calls.
2. Connects to the MCP websocket server and executes the plan sequentially.
3. Returns aggregated results and prints them.

Swap LLM backend by editing the `LLM` class (see comments).
"""

import asyncio
import json
import websockets
import argparse
import uuid
import time
from typing import Any, Dict, List, Optional

# LLM import (gpt4all)
try:
    from gpt4all import GPT4All
    GPT4ALL_AVAILABLE = True
except Exception:
    GPT4ALL_AVAILABLE = False


# -------------------------
# Helper: small JSON schema validator for plan
# -------------------------
def validate_plan(plan: Dict[str, Any]) -> bool:
    """
    Expected plan format:
    {
      "plan": [
        {"tool": "students.by_grade", "args": {"grade": "1"}},
        {"tool": "students.unpaid", "args": {}},
        {"merge": "count_unpaid"}   # optional special directive
      ]
    }
    We'll accept list of dicts where each element must have either:
     - "tool": "<name>", "args": {...}
     - OR a simple directive such as {"merge": "<key>"}

    Return True if appears ok.
    """
    if not isinstance(plan, dict):
        return False
    steps = plan.get("plan")
    if not isinstance(steps, list):
        return False
    for s in steps:
        if not isinstance(s, dict):
            return False
        if "tool" in s:
            if not isinstance(s["tool"], str):
                return False
            if "args" in s and not isinstance(s["args"], dict):
                return False
        else:
            # allow simple directives like merge or filter
            allowed_directives = {"merge", "count", "filter"}
            if not any(k in s for k in allowed_directives):
                return False
    return True


# -------------------------
# LLM wrapper (simple)
# -------------------------
class LLMPlanner:
    """
    Simple wrapper around an LLM to convert NL -> JSON plan.
    Default uses gpt4all if available. To change, modify `generate_plan`.
    """

    def __init__(self, model_name: str = "gpt4all-lora-quantized"):
        self.model_name = model_name
        self.model = None
        if GPT4ALL_AVAILABLE:
            # load the gpt4all model (it will download on first use if needed)
            try:
                self.model = GPT4All(self.model_name)
            except Exception as e:
                print(f"[LLMPlanner] Warning: GPT4All model load failed: {e}")
                self.model = None

    def generate_plan(self, prompt_text: str, max_tokens: int = 512) -> Dict[str, Any]:
        """
        Ask the LLM to produce a pure JSON plan. The prompt enforces that the model
        replies with JSON only. If parsing fails, this function raises ValueError.

        Example expected output:
        {
          "plan": [
             {"tool": "students.by_grade", "args": {"grade": "1"}},
             {"tool": "students.unpaid", "args": {}},
             {"merge": "count"}
          ]
        }
        """
        system = (
            "You are a deterministic planner. Given the user's request, output a JSON object ONLY. "
            "Do NOT output any extra text. The JSON object must have a top-level 'plan' key, "
            "whose value is a list of steps. Each step is either:\n"
            "  - {\"tool\": \"<tool_fullname>\", \"args\": {<args dict>}}\n"
            "  - or a directive like {\"merge\": \"count\"} or {\"filter\": { ... }}\n\n"
            "Use the exact tool names available on the MCP server, e.g. 'students.by_grade', 'students.unpaid', 'students.fee_due'. "
            "If you are unsure of the exact tool names, produce the best guess using dot notation: <group>.<method>.\n\n"
            "Example output:\n"
            "{\"plan\": [{\"tool\": \"students.by_grade\", \"args\": {\"grade\": \"1\"}}, {\"tool\": \"students.unpaid\", \"args\": {}}, {\"merge\": \"count\"}]}\n\n"
            "Now, produce a plan for this user request:\n"
        )

        full_prompt = system + prompt_text.strip() + "\n\nJSON:"
        raw_out = None

        # prefer GPT4All path if available
        if self.model is not None:
            # streaming or simple generation; keep simple
            try:
                response = self.model.generate(full_prompt, max_tokens=max_tokens)
                # model.generate returns string for non-chat models
                raw_out = response.strip()
            except Exception as e:
                raise RuntimeError(f"LLM generation error: {e}")
        else:
            # fallback: try a very simple rule-based planner (best-effort)
            raw_out = self.simple_rule_plan(prompt_text)

        # Some LLMs may include backticks or code fences — strip common wrappers
        # Keep only first JSON object found
        json_text = self.extract_first_json(raw_out)
        if not json_text:
            raise ValueError(f"LLM did not produce JSON plan. Raw output:\n{raw_out}")

        try:
            plan = json.loads(json_text)
        except Exception as e:
            raise ValueError(f"Failed to parse JSON from LLM output: {e}\nJSON text:\n{json_text}")

        if not validate_plan(plan):
            raise ValueError(f"Plan validation failed. Plan: {plan}")

        return plan

    def extract_first_json(self, text: str) -> Optional[str]:
        # naive extraction: find first { ... } matching braces
        # returns the text from first opening '{' to its matching closing '}'
        if text is None:
            return None
        text = text.strip()
        # remove ```json fences
        if text.startswith("```"):
            # strip fence blocks
            for fence in ["```json", "```"]:
                if text.startswith(fence):
                    text = text[len(fence):].strip()
                    if text.endswith("```"):
                        text = text[:-3].strip()
                    break
        # find first '{'
        start = text.find("{")
        if start == -1:
            return None
        # match braces
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i+1]
        return None

    def simple_rule_plan(self, query: str) -> str:
        """
        Fallback simple planner for trivial queries.
        This is not robust; use only if LLM not available.
        """
        q = query.lower()
        if "grade" in q or "class" in q:
            # try to extract a grade number
            import re
            m = re.search(r"grade\s*([0-9]+)", q)
            grade = m.group(1) if m else ""
            # Many school specs: to count unpaid students in grade X: call by_grade -> unpaid -> merge count
            plan = {
                "plan": [
                    {"tool": "students.by_grade", "args": {"grade": grade}},
                    {"tool": "students.unpaid", "args": {}},
                    {"merge": "count"}
                ]
            }
            return json.dumps(plan)
        # generic fallback: ask for list of unpaid
        plan = {"plan": [{"tool": "students.unpaid", "args": {}}, {"merge": "count"}]}
        return json.dumps(plan)


# -------------------------
# MCP WebSocket client helpers
# -------------------------
class MCPClient:
    def __init__(self, ws_uri: str, timeout: int = 10):
        self.ws_uri = ws_uri
        self.timeout = timeout
        self._conn = None
        self._resp_futures = {}  # id -> future

    async def connect(self):
        self._conn = await websockets.connect(self.ws_uri)
        # start receiver task
        asyncio.create_task(self._receiver())

    async def close(self):
        if self._conn:
            await self._conn.close()

    async def _receiver(self):
        # receive messages and dispatch to awaiting futures
        try:
            async for raw in self._conn:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                msg_id = msg.get("id")
                if msg_id and msg_id in self._resp_futures:
                    fut = self._resp_futures.pop(msg_id)
                    if "result" in msg:
                        fut.set_result(msg["result"])
                    else:
                        fut.set_exception(Exception(msg.get("error") or "Unknown RPC error"))
        except Exception:
            pass

    async def _rpc(self, method: str, params: dict) -> Any:
        if not self._conn:
            raise RuntimeError("Not connected")
        msg_id = str(uuid.uuid4())
        payload = {"id": msg_id, "method": method, "params": params}
        fut = asyncio.get_running_loop().create_future()
        self._resp_futures[msg_id] = fut
        await self._conn.send(json.dumps(payload))
        # wait for response or timeout
        try:
            return await asyncio.wait_for(fut, timeout=self.timeout)
        except asyncio.TimeoutError:
            if msg_id in self._resp_futures:
                self._resp_futures.pop(msg_id, None)
            raise

    async def list_tools(self) -> Any:
        return await self._rpc("list_tools", {})

    async def tool_info(self, name: str) -> Any:
        return await self._rpc("tool_info", {"name": name})

    async def call_tool(self, name: str, args: dict) -> Any:
        return await self._rpc("call_tool", {"name": name, "args": args})


# -------------------------
# Plan Executor
# -------------------------
class PlanExecutor:
    def __init__(self, mcp_client: MCPClient):
        self.mcp = mcp_client

    async def execute(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        steps = plan.get("plan", [])
        context = {"last": None}
        results = []
        for step in steps:
            if "tool" in step:
                tool_name = step["tool"]
                args = step.get("args", {}) or {}
                # If args refer to placeholders like {grade_from_prev}, you could replace them here
                # For now we support direct args only.
                try:
                    res = await self.mcp.call_tool(tool_name, args)
                except Exception as e:
                    return {"error": f"Tool call '{tool_name}' failed: {e}"}
                context["last"] = res
                results.append({"tool": tool_name, "result": res})
            else:
                # handle directive e.g. merge/count
                if "merge" in step and step["merge"] == "count":
                    # If last result is a list, count; else try to sum counts from previous results
                    last = context.get("last")
                    if isinstance(last, list):
                        cnt = len(last)
                        results.append({"merge": "count", "value": cnt})
                        context["last"] = cnt
                    else:
                        # attempt to derive count from results array (sum list lengths)
                        total = 0
                        for r in results:
                            v = r.get("result")
                            if isinstance(v, list):
                                total += len(v)
                        results.append({"merge": "count", "value": total})
                        context["last"] = total
                elif "merge" in step and step["merge"] == "unique":
                    # example: flatten lists and get unique by id
                    all_items = []
                    for r in results:
                        v = r.get("result")
                        if isinstance(v, list):
                            all_items.extend(v)
                    # assume each item has 'id'
                    uniq = {item.get("id", idx): item for idx, item in enumerate(all_items)}
                    vals = list(uniq.values())
                    results.append({"merge": "unique", "value": vals})
                    context["last"] = vals
                else:
                    # unsupported directive - ignore or return error
                    results.append({"directive": step, "error": "unsupported directive"})
        return {"results": results, "final": context.get("last")}


# -------------------------
# CLI / orchestrator
# -------------------------
async def run_query(ws_uri: str, query: str, model_name: str = "gpt4all-lora-quantized"):
    # 1. LLM plan
    planner = LLMPlanner(model_name=model_name)
    print("[client] Generating plan from LLM...")
    plan = planner.generate_plan(query)
    print("[client] Plan:", json.dumps(plan, indent=2))

    # 2. Connect MCP
    mcp = MCPClient(ws_uri)
    await mcp.connect()
    print("[client] Connected to MCP:", ws_uri)

    # optional: list tools (for debugging)
    try:
        tools = await mcp.list_tools()
        print("[client] Tools available (sample):", list(tools.keys())[:10])
    except Exception as e:
        print("[client] Could not list tools:", e)

    # 3. Execute plan
    executor = PlanExecutor(mcp)
    exec_result = await executor.execute(plan)
    print("[client] Execution result:", json.dumps(exec_result, indent=2))

    await mcp.close()
    return exec_result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp", required=True, help="MCP websocket URL (e.g. ws://localhost:8765)")
    parser.add_argument("--query", required=True, help="Natural language query")
    parser.add_argument("--model", default="gpt4all-lora-quantized", help="LLM model name (gpt4all)")
    args = parser.parse_args()

    result = asyncio.run(run_query(args.mcp, args.query, model_name=args.model))
    print("FINAL:", json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
