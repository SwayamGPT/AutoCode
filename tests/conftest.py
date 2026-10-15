import os
import tempfile

# Keep tests independent of a developer's .env / shell.
for name in ("OLLAMA_MODEL", "MAX_ITERATIONS", "PASSING_SCORE", "SKIP_VERIFY", "A2A_PEERS", "FAST_MODE"):
    os.environ.pop(name, None)
os.environ["MCP_CONFIG"] = "/nonexistent/mcp.json"

# Never touch the real ~/.autocode (logs, run history, settings) from tests, including subprocesses.
os.environ["AUTOCODE_HOME"] = tempfile.mkdtemp(prefix="autocode-test-")
