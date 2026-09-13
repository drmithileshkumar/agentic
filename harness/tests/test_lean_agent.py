"""Mock tests for LeanAgentHarness construction -- provider-aware max_tokens.

Regression for the 20260913_011157 Groq run, which died at proof 14 of 50:

    Error code: 413 - Request too large for model `openai/gpt-oss-20b` ...
    tokens per minute (TPM): Limit 8000, Requested 8381

A flat MAX_TOKENS = 8192 is right for NVIDIA NIM (no per-request cap, and
Nemotron needs the room for its reasoning trace) and structurally impossible on
Groq's free tier, which counts max_tokens against an 8000 TPM ceiling before the
request even runs. 41 of 42 LLM failures in that run were this, and no amount of
retrying would have helped.

Contract under test:
  groq.com base URL -> 1024
  anything else     -> 8192
  explicit override -> wins over both
  no live API calls, no Lean server

The Lean side is stubbed: LeanAgentHarness.__init__ builds a LeanServer, which
costs ~10 minutes of `import Mathlib` and is irrelevant to what is being tested.
"""
import sys
from pathlib import Path

sys.path.insert(0, r"C:\lean")

from harness import lean_agent
from harness.lean_agent import (
    GROQ_MAX_TOKENS, MAX_TOKENS, LeanAgentHarness, _resolve_max_tokens,
)

GROQ_URL = "https://api.groq.com/openai/v1"
NVIDIA_URL = "https://integrate.api.nvidia.com/v1"


class _StubServer:
    def is_alive(self):
        return True


def build(base_url, tmpdir, max_tokens=None, api_key="test-key"):
    """Construct a real LeanAgentHarness with only the Lean side stubbed."""
    saved = {k: lean_agent.__dict__[k]
             for k in ("LeanREPLConfig", "LeanServer", "LocalProject")}
    env_saved = {k: __import__("os").environ.get(k)
                 for k in ("QWEN_BASE_URL", "QWEN_API_KEY")}
    import os

    lean_agent.LocalProject = lambda **kw: object()
    lean_agent.LeanREPLConfig = lambda **kw: object()
    lean_agent.LeanServer = lambda cfg: _StubServer()
    os.environ["QWEN_BASE_URL"] = base_url
    os.environ["QWEN_API_KEY"] = api_key
    try:
        return LeanAgentHarness(
            project_dir="unused",
            log_path=str(Path(tmpdir) / "unused.jsonl"),
            model="test-model",
            echo=False,
            max_tokens=max_tokens,
        )
    finally:
        lean_agent.__dict__.update(saved)
        for k, v in env_saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def run():
    failures = []

    def check(name, cond, detail=""):
        if cond:
            print("  PASS  %s" % name)
        else:
            print("  FAIL  %s  %s" % (name, detail))
            failures.append(name)

    import tempfile
    tmp = tempfile.mkdtemp(prefix="harness_init_")

    # --------------------------------------------------------------- case 1
    print("Case 1: the two budgets are what the providers allow")
    check("NVIDIA default is 8192", MAX_TOKENS == 8192, MAX_TOKENS)
    check("Groq budget is 1024", GROQ_MAX_TOKENS == 1024, GROQ_MAX_TOKENS)
    check("Groq budget clears the 8000 TPM ceiling with room for a ~7k prompt",
          GROQ_MAX_TOKENS <= 8000 - 6500, GROQ_MAX_TOKENS)

    # --------------------------------------------------------------- case 2
    print("Case 2: a constructed harness picks the budget from its base URL")
    h = build(GROQ_URL, tmp)
    check("groq.com -> 1024", h.max_tokens == 1024, h.max_tokens)

    h = build(NVIDIA_URL, tmp)
    check("NVIDIA NIM -> 8192", h.max_tokens == 8192, h.max_tokens)

    h = build("https://dashscope.aliyuncs.com/compatible-mode/v1", tmp)
    check("DashScope -> 8192", h.max_tokens == 8192, h.max_tokens)

    h = build("https://api.together.xyz/v1", tmp)
    check("Together -> 8192", h.max_tokens == 8192, h.max_tokens)

    # --------------------------------------------------------------- case 3
    print("Case 3: an explicit max_tokens wins over auto-detection")
    h = build(GROQ_URL, tmp, max_tokens=4096)
    check("override beats the Groq default", h.max_tokens == 4096, h.max_tokens)
    h = build(NVIDIA_URL, tmp, max_tokens=256)
    check("override beats the NVIDIA default", h.max_tokens == 256, h.max_tokens)

    # --------------------------------------------------------------- case 4
    print("Case 4: the resolver itself, without construction")
    for url, want, label in [
        (GROQ_URL, 1024, "groq.com"),
        ("https://api.groq.com/openai/v1/", 1024, "groq.com trailing slash"),
        ("HTTPS://API.GROQ.COM/openai/v1", 8192, "uppercase host (substring is case-sensitive)"),
        (NVIDIA_URL, 8192, "nvidia"),
        ("", 8192, "empty"),
    ]:
        got = _resolve_max_tokens(url)
        check("%s -> %d" % (label, want), got == want, got)
    check("override short-circuits everything",
          _resolve_max_tokens(GROQ_URL, 77) == 77)
    check("override of 0 is honoured, not treated as absent",
          _resolve_max_tokens(GROQ_URL, 0) == 0)

    # --------------------------------------------------------------- case 5
    print("Case 5: the budget is what actually reaches the API call")
    calls = []

    class FakeCompletions:
        def create(self, model, max_tokens, messages, timeout=None):
            calls.append(max_tokens)
            raise RuntimeError("stop here -- only the argument matters")

    h = build(GROQ_URL, tmp)
    h._client = type("c", (), {"chat": type("ch", (), {"completions": FakeCompletions()})()})()
    try:
        h._get_tactic("goal", [])
    except Exception:
        pass
    check("Groq harness sends max_tokens=1024", calls == [1024], calls)

    print("")
    if failures:
        print("FAILED: %d check(s): %s" % (len(failures), ", ".join(failures)))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
