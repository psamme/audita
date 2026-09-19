"""Model access with per-call usage logging.

Primary backend is the Anthropic SDK (ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile).
When no credential is present the same interface is served by the local `claude` CLI in headless mode, so the
project still runs on a laptop that is only logged in to Claude Code. Both report real token usage and cost.
"""
import json
import os
import subprocess
import threading
from pathlib import Path

MODEL = os.environ.get("SHADOW_MODEL", "claude-opus-5")
EFFORT = os.environ.get("SHADOW_EFFORT", "medium")
PRICES = {  # USD per million tokens: input, output
    "claude-opus-5": (5.0, 25.0), "claude-sonnet-5": (2.0, 10.0), "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5-1": (10.0, 50.0),
}


def _load_env():
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


_load_env()


def backend() -> str:
    forced = os.environ.get("SHADOW_BACKEND")
    if forced:
        return forced
    has_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") \
        or (Path.home() / ".config/anthropic").exists()
    return "sdk" if has_key else "cli"


def cost(model: str, usage: dict) -> float:
    pin, pout = PRICES.get(model, PRICES["claude-opus-5"])
    return round((usage.get("input_tokens", 0) * pin + usage.get("cache_creation_tokens", 0) * pin * 1.25
                  + usage.get("cache_read_tokens", 0) * pin * 0.1 + usage.get("output_tokens", 0) * pout) / 1e6, 6)


class Usage:
    """Thread-safe accumulator; one per item so cost can be attributed."""

    def __init__(self):
        self.lock = threading.Lock()
        self.d = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0,
                  "cost_usd": 0.0, "llm_calls": 0}

    def add(self, u: dict):
        with self.lock:
            for k in self.d:
                self.d[k] = round(self.d[k] + u.get(k, 0), 6)

    def as_dict(self) -> dict:
        return dict(self.d)


class Reply:
    def __init__(self, text: str, tool_calls: list[dict], raw_content, stop_reason: str, usage: dict):
        self.text, self.tool_calls, self.raw_content = text, tool_calls, raw_content
        self.stop_reason, self.usage = stop_reason, usage


# --- SDK backend ---------------------------------------------------------------------------
_client = None


def _sdk():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(max_retries=4)
    return _client


def _sdk_call(system: str, messages: list, tools: list | None, schema: dict | None, max_tokens: int, model: str) -> Reply:
    kwargs = dict(model=model, max_tokens=max_tokens, messages=messages,
                  system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                  thinking={"type": "adaptive"}, output_config={"effort": EFFORT})
    if tools:
        kwargs["tools"] = tools
    if schema:
        kwargs["output_config"]["format"] = {"type": "json_schema", "schema": schema}
    with _sdk().messages.stream(**kwargs) as stream:
        msg = stream.get_final_message()
    u = msg.usage
    usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
             "cache_read_tokens": u.cache_read_input_tokens or 0,
             "cache_creation_tokens": u.cache_creation_input_tokens or 0, "llm_calls": 1}
    usage["cost_usd"] = cost(model, usage)
    text = "".join(b.text for b in msg.content if b.type == "text")
    calls = [{"id": b.id, "name": b.name, "input": b.input} for b in msg.content if b.type == "tool_use"]
    return Reply(text, calls, msg.content, msg.stop_reason, usage)


# --- CLI backend ---------------------------------------------------------------------------
TURN_SCHEMA = {
    "type": "object",
    "properties": {"tool_calls": {"type": "array", "items": {
        "type": "object", "properties": {"name": {"type": "string"}, "input": {"type": "object"}},
        "required": ["name", "input"]}}},
    "required": ["tool_calls"],
}


def _render(messages: list) -> str:
    out = []
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append(f"[{m['role']}]\n{content}")
            continue
        for b in content:
            b = b if isinstance(b, dict) else b.__dict__
            if b.get("type") == "text":
                out.append(f"[{m['role']}]\n{b['text']}")
            elif b.get("type") == "tool_use":
                out.append(f"[assistant called {b['name']}]\n{json.dumps(b['input'])}")
            elif b.get("type") == "tool_result":
                out.append(f"[tool result]\n{b['content']}")
    return "\n\n".join(out)


def _cli_call(system: str, messages: list, tools: list | None, schema: dict | None, max_tokens: int, model: str) -> Reply:
    if tools:
        spec = json.dumps([{"name": t["name"], "description": t["description"], "input": t["input_schema"]} for t in tools])
        system += ("\n\nYou act only by calling tools. Reply with JSON {\"tool_calls\": [{\"name\": ..., \"input\": {...}}]}. "
                   "Several independent calls may go in one reply. Tools:\n" + spec)
    cmd = ["claude", "-p", "--output-format", "json", "--model", model, "--system-prompt", system, "--tools", "",
           "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config",
           "--json-schema", json.dumps(TURN_SCHEMA if tools else schema)]
    last = None
    for _ in range(3):
        p = subprocess.run(cmd, input=_render(messages), capture_output=True, text=True, timeout=600)
        try:
            out = json.loads(p.stdout)
            if not out.get("is_error") and out.get("structured_output") is not None:
                break
            last = out.get("result") or p.stdout[:300]
        except json.JSONDecodeError:
            last = (p.stdout or p.stderr)[:300]
    else:
        raise RuntimeError(f"claude CLI failed: {last}")
    u = out.get("usage", {})
    usage = {"input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0),
             "cache_read_tokens": u.get("cache_read_input_tokens", 0),
             "cache_creation_tokens": u.get("cache_creation_input_tokens", 0),
             "cost_usd": out.get("total_cost_usd", 0.0), "llm_calls": 1}
    so = out["structured_output"]
    if tools:
        calls = [{"id": f"cli_{i}", "name": c["name"], "input": c.get("input") or {}} for i, c in enumerate(so["tool_calls"])]
        raw = [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["input"]} for c in calls]
        return Reply("", calls, raw, "tool_use" if calls else "end_turn", usage)
    return Reply(json.dumps(so), [], [{"type": "text", "text": json.dumps(so)}], "end_turn", usage)


def call(system: str, messages: list, tools: list | None = None, schema: dict | None = None,
         max_tokens: int = 16000, model: str | None = None, usage: Usage | None = None) -> Reply:
    """One model turn. With `schema`, reply.text is JSON matching it. With `tools`, read reply.tool_calls."""
    fn = _sdk_call if backend() == "sdk" else _cli_call
    for attempt in range(3):
        reply = fn(system, messages, tools, schema, max_tokens, model or MODEL)
        if usage:
            usage.add(reply.usage)
        if not schema:
            return reply
        try:                       # callers json.loads(reply.text); make sure that cannot blow up on them
            json.loads(reply.text)
            return reply
        except json.JSONDecodeError:
            if attempt == 2:
                raise RuntimeError(f"model did not return valid JSON for the requested schema (stop_reason={reply.stop_reason})")
    return reply
