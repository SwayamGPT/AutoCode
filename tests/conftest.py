import os

# Keep tests independent of a developer's .env / shell.
for name in ("OLLAMA_MODEL", "MAX_ITERATIONS", "PASSING_SCORE", "SKIP_VERIFY", "A2A_PEERS"):
    os.environ.pop(name, None)
os.environ["MCP_CONFIG"] = "/nonexistent/mcp.json"
