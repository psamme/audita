"""Model access for the desks. Every call is one structured-output request, logged with its inputs and outputs and
cached by input hash, so any run replays without a network.

Modes: off (desks escalate instead of asking), live (call and log), replay (cache only; a miss behaves like off).
Backends: the Anthropic SDK when a key is present, else the local `claude` CLI in headless mode.
"""
import hashlib
import json
import os
import subprocess
import threading
from pathlib import Path

FAST = os.environ.get("ONECARD_FAST_MODEL", "claude-haiku-4-5-20251001")
STRONG = os.environ.get("ONECARD_STRONG_MODEL", "claude-opus-5")
PRICES = {"claude-opus-5": (5.0, 25.0), "claude-sonnet-5": (2.0, 10.0), "claude-haiku-4-5-20251001": (1.0, 5.0)}
ROOT = Path(__file__).resolve().parent.parent


def _load_env():
    for env in (ROOT / ".env", ROOT.parent / ".env"):
        if env.exists():
            for line in env.read_text().splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"'))


_load_env()


def _sdk(system, prompt, schema, model):
    import anthropic
    client = anthropic.Anthropic(max_retries=4)
    msg = client.messages.create(
        model=model, max_tokens=4000, messages=[{"role": "user", "content": prompt}],
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        output_config={"format": {"type": "json_schema", "schema": schema}})
    u = msg.usage
    pin, pout = PRICES.get(model, (5.0, 25.0))
    cost = (u.input_tokens * pin + u.output_tokens * pout) / 1e6
    return json.loads("".join(b.text for b in msg.content if b.type == "text")), round(cost, 6)


def _cli(system, prompt, schema, model):
    cmd = ["claude", "-p", "--output-format", "json", "--model", model, "--system-prompt", system, "--tools", "",
           "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config", "--json-schema", json.dumps(schema)]
    last = None
    for _ in range(3):
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=600)
        try:
            out = json.loads(p.stdout)
            if not out.get("is_error") and out.get("structured_output") is not None:
                return out["structured_output"], out.get("total_cost_usd", 0.0)
            last = out.get("result") or p.stdout[:300]
        except json.JSONDecodeError:
            last = (p.stdout or p.stderr)[:300]
    raise RuntimeError(f"claude CLI failed: {last}")


class Model:
    def __init__(self, mode: str = "off", cache: str | Path | None = None):
        self.mode, self.lock = mode, threading.Lock()
        self.cache_path = Path(cache or ROOT / "runs" / "model_calls.jsonl")
        self.cache, self.period, self.usage = {}, "", {}
        if self.cache_path.exists():
            for line in self.cache_path.read_text().splitlines():
                rec = json.loads(line)
                self.cache[rec["key"]] = rec

    @property
    def on(self) -> bool:
        return self.mode != "off"

    def ask(self, role: str, system: str, prompt: str, schema: dict, strong: bool = False) -> dict | None:
        if self.mode == "off":
            return None
        model = STRONG if strong else FAST
        key = hashlib.sha256(json.dumps([model, system, prompt, schema], sort_keys=True).encode()).hexdigest()
        rec = self.cache.get(key)
        if rec is None:
            if self.mode == "replay":
                return None
            backend = _sdk if (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")) else _cli
            try:
                output, cost = backend(system, prompt, schema, model)
            except Exception as e:  # a model outage must degrade to a human question, never to a guess
                print(f"  model call failed ({role}): {e}")
                return None
            rec = {"key": key, "role": role, "model": model, "system": system, "prompt": prompt, "output": output, "cost_usd": cost}
            with self.lock:
                self.cache[key] = rec
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.cache_path, "a") as f:
                    f.write(json.dumps(rec) + "\n")
        with self.lock:
            u = self.usage.setdefault(self.period, {"calls": 0, "cost_usd": 0.0})
            u["calls"] += 1
            u["cost_usd"] = round(u["cost_usd"] + rec.get("cost_usd", 0.0), 6)
        return rec["output"]
