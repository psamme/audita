"""OpenAI Responses API access with per-call usage logging.

Set OPENAI_API_KEY in .env. Legacy Anthropic SDK/CLI backends require an
explicit SHADOW_BACKEND override; missing OpenAI credentials never switch providers.
"""
import json
import os
import subprocess
import threading
from pathlib import Path
from jsonschema import validate, ValidationError

PRICES = {  # USD per million tokens: input, output
    "gpt-5.2": (1.75, 14.0),
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
MODEL = os.environ.get("SHADOW_MODEL", "gpt-5.2" if os.environ.get("SHADOW_BACKEND", "openai") == "openai" else "claude-opus-5")
EFFORT = os.environ.get("SHADOW_EFFORT", "medium")


def backend() -> str:
    return os.environ.get("SHADOW_BACKEND", "openai")


def cost(model: str, usage: dict) -> float:
    pin, pout = PRICES[model]
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


# --- OpenAI backend ------------------------------------------------------------------------
_openai_client = None


def _openai():
    global _openai_client
    if _openai_client is None:
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("Add OPENAI_API_KEY to .env and restart Audita to enable model calls")
        from openai import OpenAI
        _openai_client = OpenAI(max_retries=2, timeout=180)
    return _openai_client


def _openai_input(messages):
    items = []
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            items.append({"role": message["role"], "content": content})
            continue
        for block in content:
            if block["type"] == "openai_response_item":
                items.append(block["item"])
            elif block["type"] == "text":
                items.append({"role": message["role"], "content": block["text"]})
            elif block["type"] == "tool_use":
                items.append({"type": "function_call", "call_id": block["id"],
                              "name": block["name"], "arguments": json.dumps(block["input"])})
            elif block["type"] == "tool_result":
                result = block["content"]
                items.append({"type": "function_call_output", "call_id": block["tool_use_id"],
                              "output": result if isinstance(result, str) else json.dumps(result)})
            else:
                raise ValueError("Unsupported conversation block")
    return items


def _openai_call(system, messages, tools, schema, max_tokens, model):
    # Existing patch schemas contain open dictionaries. Preserve their semantics
    # using JSON mode plus local schema validation instead of silently narrowing them.
    if model not in PRICES:
        raise ValueError(f"Add verified pricing for model {model} before using it")
    kwargs = dict(model=model, instructions=system, input=_openai_input(messages),
                  max_output_tokens=max_tokens, reasoning={"effort": EFFORT},
                  store=False, include=["reasoning.encrypted_content"])
    if tools:
        kwargs["tools"] = [{"type": "function", "name": t["name"],
                            "description": t["description"], "parameters": t["input_schema"],
                            "strict": False} for t in tools]
    if schema:
        kwargs["text"] = {"format": {"type": "json_object"}}
        kwargs["instructions"] += "\nReturn only JSON matching this schema:\n" + json.dumps(schema)
    response = _openai().responses.create(**kwargs)
    u = response.usage
    cached = getattr(u.input_tokens_details, "cached_tokens", 0) or 0
    usage = {"input_tokens": u.input_tokens - cached, "output_tokens": u.output_tokens,
             "cache_read_tokens": cached, "cache_creation_tokens": 0, "llm_calls": 1}
    usage["cost_usd"] = cost(model, usage)
    raw = [{"type": "openai_response_item", "item": item.model_dump(exclude_none=True)}
           for item in response.output]
    refused = any(part.type == "refusal" for item in response.output if item.type == "message"
                  for part in item.content)
    stop = "refusal" if refused else ("end_turn" if response.status == "completed" else "incomplete")
    calls = []
    if stop == "end_turn":
        specs = {t["name"]: t["input_schema"] for t in tools or []}
        for item in response.output:
            if item.type == "function_call":
                if item.name not in specs:
                    raise RuntimeError("Model requested an unknown tool; no tool was executed")
                arguments = json.loads(item.arguments)
                validate(arguments, specs[item.name])
                calls.append({"id": item.call_id, "name": item.name, "input": arguments})
        if calls:
            stop = "tool_use"
    return Reply(response.output_text, calls, raw, stop, usage)


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
    providers = {"openai": _openai_call, "sdk": _sdk_call, "cli": _cli_call}
    if backend() not in providers:
        raise ValueError("SHADOW_BACKEND must be openai, sdk, or cli")
    fn = providers[backend()]
    for attempt in range(3):
        reply = fn(system, messages, tools, schema, max_tokens, model or MODEL)
        if usage:
            usage.add(reply.usage)
        if reply.stop_reason in {"incomplete", "failed"}:
            raise RuntimeError("Model response was incomplete; no decision was accepted")
        if not schema:
            return reply
        if reply.stop_reason == "refusal":
            raise RuntimeError("Model refused the request; no decision was accepted")
        try:                       # callers json.loads(reply.text); make sure that cannot blow up on them
            data = json.loads(reply.text)
            if backend() == "openai":
                validate(data, schema)
            return reply
        except (json.JSONDecodeError, ValidationError):
            if attempt == 2:
                raise RuntimeError(f"model did not return valid JSON for the requested schema (stop_reason={reply.stop_reason})")
    return reply
