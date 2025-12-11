import asyncio
import json
import websockets
import argparse
import uuid
from typing import Any, Dict, Optional, List
import ollama


# ============================================================
#  PLAN VALIDATION
# ============================================================
def validate_plan(plan: Dict[str, Any]) -> bool:
    if not isinstance(plan, dict):
        return False

    steps = plan.get("plan")
    if not isinstance(steps, list):
        return False

    allowed_merge_ops = {"count", "union", "intersect", "difference"}

    for s in steps:
        if not isinstance(s, dict):
            return False

        # TOOL STEP
        if "tool" in s:
            if not isinstance(s["tool"], str):
                return False
            if "args" in s and not isinstance(s["args"], dict):
                return False
            continue

        # MERGE STEP
        if "merge" in s:
            if s["merge"] not in allowed_merge_ops:
                return False
            continue

        return False

    return True


# ============================================================
#  LLM PLANNER
# ============================================================
class LLMPlanner:

    def __init__(self, model_name="gemma2:9b"):
        self.model_name = model_name

    def _user_wants_count(self, query: str) -> bool:
        q = query.lower()
        # Basic set of phrases that indicate a count/number request
        count_triggers = [
            "how many",
            "what is the count",
            "count of",
            "give me the count",
            "i need the count",
            "i need the number",
            "the number of",
            "how many are",
            "how many students",
            "how many teachers",
            "just the count",
            "only the count",
            "not the list",
            "only the number",
            "give only the count",
            "give only the number",
        ]
        return any(trigger in q for trigger in count_triggers)

    def generate_plan(
        self,
        query: str,
        tools: Dict[str, Any],
        max_tokens: int = 512
    ) -> Dict[str, Any]:

        tools_json = json.dumps(tools, indent=2)

        system_prompt = f"""
You are an MCP planning engine.
Your task: convert natural language into a list of MCP tool calls.

### RULES ###

1. Output ONLY a JSON object.
2. Use EXACT tool names from this list (do NOT invent tools):
{tools_json}

3. Available merge operations (use exactly these):
   - {{ "merge": "intersect" }}  -> intersection by "id"
   - {{ "merge": "union" }}
   - {{ "merge": "difference" }}
   - {{ "merge": "count" }}

4. Output format:
{{
  "plan": [
     {{ "tool": "<toolname>", "args": {{...}} }},
     {{ "merge": "intersect" }},
     {{ "merge": "count" }}
  ]
}}

5. Important: If the user's request asks for a number, a count, or explicitly says "not the list" / "just the count", your plan MUST end with {{ "merge": "count" }}.
6. If the plan contains more than one tool that returns a list,
   YOU MUST insert exactly one merge step between them.

   Determine the correct merge operator based on the user's language:
   - Use "intersect" for AND conditions (students who are X AND Y).
   - Use "union" for OR conditions (students who are X OR Y).
   - Use "difference" for NOT conditions (students who are X but NOT Y).

   This merge step MUST appear immediately after the second tool call.
    Example: To find students in grade 3 who have paid full fees:
    {{
      "plan": [
          {{ "tool": "students.by_grade", "args": {{ "grade": 3 }} }},
          {{ "tool": "students.fullpaid", "args": {{}} }},
          {{ "merge": "intersect" }}
      ]
    }}
   Never skip this.
7. Do NOT output any explanation or extra text — ONLY produce valid JSON.
8. IMPORTANT: For any grade value, output only the number, e.g. "1", "2", "3", NOT "grade 1".

Now produce a plan for the user's query (be concise and deterministic).
"""

        try:
            response = ollama.chat(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query},
                ],
                options={"num_predict": max_tokens}
            )
        except Exception as e:
            raise RuntimeError(f"Ollama error: {e}")

        raw = response["message"]["content"].strip()
        json_text = self.extract_json(raw)

        if not json_text:
            raise ValueError(f"Planner did not output JSON:\n{raw}")

        plan = json.loads(json_text)

        # Validate schema first
        if not validate_plan(plan):
            raise ValueError(f"LLM plan schema invalid: {plan}")

        # Enforce count at end if user asked for a count
        if self._user_wants_count(query):
            steps = plan.get("plan", [])
            # If last step is not merge=count, append it
            if not (isinstance(steps, list) and len(steps) > 0 and isinstance(steps[-1], dict) and steps[-1].get("merge") == "count"):
                steps.append({"merge": "count"})
                plan["plan"] = steps

        return plan

    def extract_json(self, text: str) -> Optional[str]:
        if text.startswith("```"):
            if text.startswith("```json"):
                text = text[7:].strip()
            else:
                text = text[3:].strip()
            if text.endswith("```"):
                text = text[:-3]

        start = text.find("{")
        if start == -1:
            return None

        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i+1]
        return None


# ============================================================
#  MCP CLIENT (WebSocket)
# ============================================================
class MCPClient:

    def __init__(self, ws_uri: str, timeout=10):
        self.ws_uri = ws_uri
        self.timeout = timeout
        self._conn = None
        self._pending = {}

    async def connect(self):
        self._conn = await websockets.connect(self.ws_uri)
        asyncio.create_task(self._receiver())

    async def close(self):
        if self._conn:
            await self._conn.close()

    async def _receiver(self):
        try:
            async for raw in self._conn:
                try:
                    msg = json.loads(raw)
                except:
                    continue

                msg_id = msg.get("id")
                if msg_id in self._pending:
                    fut = self._pending.pop(msg_id)
                    if "result" in msg:
                        fut.set_result(msg["result"])
                    else:
                        fut.set_exception(Exception(msg.get("error")))
        except:
            pass

    async def _rpc(self, method: str, params: dict):
        msg_id = str(uuid.uuid4())
        fut = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut

        payload = {"id": msg_id, "method": method, "params": params}
        await self._conn.send(json.dumps(payload))

        return await asyncio.wait_for(fut, timeout=self.timeout)

    async def list_tools(self):
        return await self._rpc("list_tools", {})

    async def call_tool(self, name: str, args: dict):
        return await self._rpc("call_tool", {"name": name, "args": args})


# ============================================================
#  PLAN EXECUTOR (WITH CORRECT MERGE LOGIC)
# ============================================================
class PlanExecutor:

    def __init__(self, mcp: MCPClient):
        self.mcp = mcp

    def _index_by_id(self, lst):
        return {item["id"]: item for item in lst if isinstance(item, dict) and "id" in item}

    # ---- merge operations ----
    def merge_intersect(self, results: List[List[dict]]):
        if len(results) < 2:
            return results[-1] if results else []

        maps = [self._index_by_id(r) for r in results]

        common_ids = set(maps[0].keys())
        for m in maps[1:]:
            common_ids &= set(m.keys())

        # return items from the first map in deterministic order
        return [maps[0][i] for i in common_ids]

    def merge_union(self, results: List[List[dict]]):
        merged = {}
        for r in results:
            for item in r:
                if "id" in item:
                    merged[item["id"]] = item
        return list(merged.values())

    def merge_difference(self, a, b):
        amap = self._index_by_id(a)
        bmap = self._index_by_id(b)
        diff = [v for k, v in amap.items() if k not in bmap]
        return diff

    async def execute(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        steps = plan.get("plan", [])
        tool_results: List[List[Dict[str, Any]]] = []      # stores results of each TOOL call (lists)
        context_last: Any = None

        # pending merges that couldn't be applied yet because not enough tool results existed
        pending_merges: List[str] = []

        def _apply_merge_op(op: str):
            nonlocal tool_results, context_last
            if op == "intersect":
                merged = self.merge_intersect(tool_results)
                tool_results = [merged]
                context_last = merged
            elif op == "union":
                merged = self.merge_union(tool_results)
                tool_results = [merged]
                context_last = merged
            elif op == "difference":
                if len(tool_results) < 2:
                    # cannot apply difference yet
                    return False
                merged = self.merge_difference(tool_results[0], tool_results[1])
                tool_results = [merged]
                context_last = merged
            else:
                raise ValueError(f"Unknown deferred merge op {op}")
            return True

        for step in steps:

            # --------------------------------------------
            # TOOL CALL
            # --------------------------------------------
            if "tool" in step:
                tool = step["tool"]
                args = step.get("args", {})
                res = await self.mcp.call_tool(tool, args)

                # Accept empty list as valid
                if not isinstance(res, list):
                    raise ValueError(f"Tool {tool} must return a list, got: {res}")

                tool_results.append(res)
                context_last = res

                # After adding a new tool result, try to apply any pending merges
                # as long as they can be applied (i.e., enough results exist).
                applied_any = True
                while pending_merges and applied_any:
                    applied_any = False
                    # Check first pending merge if it's now applicable
                    next_op = pending_merges[0]
                    # For intersect/union we need at least 2 prior tool results
                    if next_op in ("intersect", "union", "difference"):
                        if len(tool_results) >= 2:
                            pending_merges.pop(0)
                            _apply_merge_op(next_op)
                            applied_any = True
                    else:
                        # shouldn't be other ops here
                        pending_merges.pop(0)
                        applied_any = False

                continue

            # --------------------------------------------
            # MERGE OPERATIONS
            # --------------------------------------------
            if "merge" in step:
                op = step["merge"]

                # If op requires at least 2 prior tool results but we don't have them yet,
                # defer the merge until more tool results have been collected.
                if op in ("intersect", "union", "difference"):
                    if len(tool_results) < 2:
                        # Defer it
                        pending_merges.append(op)
                        # Do not change context_last now; it will be updated when applied.
                        continue
                    else:
                        # apply immediately
                        merged = None
                        if op == "intersect":
                            merged = self.merge_intersect(tool_results)
                        elif op == "union":
                            merged = self.merge_union(tool_results)
                        elif op == "difference":
                            merged = self.merge_difference(tool_results[0], tool_results[1])
                        tool_results = [merged]
                        context_last = merged
                        continue

                elif op == "count":
                    # Before counting, try to apply any pending merges (if possible)
                    while pending_merges:
                        next_op = pending_merges[0]
                        if next_op in ("intersect", "union", "difference") and len(tool_results) < 2:
                            # cannot apply now; break out and count current available result
                            break
                        # apply
                        pending_merges.pop(0)
                        _apply_merge_op(next_op)

                    # Now perform the count on the most recent logical result:
                    if isinstance(context_last, list):
                        context_last = len(context_last)
                    else:
                        if tool_results and isinstance(tool_results[-1], list):
                            context_last = len(tool_results[-1])
                        else:
                            context_last = 0
                    # After count, we keep tool_results as-is (context_last is an int)
                    continue

                else:
                    raise ValueError(f"Unknown merge op {op}")

        # If plan ended but some pending merges remain and are applicable, try to apply them
        while pending_merges:
            next_op = pending_merges[0]
            if next_op in ("intersect", "union", "difference") and len(tool_results) < 2:
                # cannot apply further
                break
            pending_merges.pop(0)
            _apply_merge_op(next_op)

        return {
            "final": context_last
        }


# ============================================================
#  ORCHESTRATOR
# ============================================================
async def run_query(ws_uri: str, query: str, model_name: str):

    mcp = MCPClient(ws_uri)
    await mcp.connect()
    print("[client] Connected to MCP server.")

    tools_list = await mcp.list_tools()
    raw_tools = tools_list.get("tools", {})  # dict of tool_name → tool_info
    tools = {
        tool_name: {"description": tool_info.get("description", "")}
        for tool_name, tool_info in raw_tools.items()
    }
    print("[client] Tools discovered:", json.dumps(tools, indent=2))


    planner = LLMPlanner(model_name=model_name)
    plan = planner.generate_plan(query, tools)
    print("[client] LLM Plan:", json.dumps(plan, indent=2))

    executor = PlanExecutor(mcp)
    result = await executor.execute(plan)

    await mcp.close()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--model", default="gemma2:9b")
    args = parser.parse_args()

    out = asyncio.run(run_query(args.mcp, args.query, args.model))
    print("\nFINAL:", json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
