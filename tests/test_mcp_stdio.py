"""End-to-end test of the `smartmdao-mcp` console entry point.

Every other MCP test calls handlers or an in-process server object. This one
spawns the real binary and speaks JSON-RPC to it over stdin/stdout, exactly the
way a coding agent does — which is the only way to catch a broken entry point,
a bad console script, or a server that fails to start.

Slower than the rest of the suite (about a second) and worth it: this is the
path every real user takes.
"""
import json
import pathlib
import shutil
import subprocess
import sys

import pytest

PROTOCOL_VERSION = "2026-07-28"


def binary():
    """The installed console script, looked for beside the running interpreter."""
    alongside = str(pathlib.Path(sys.executable).parent)
    found = shutil.which("smartmdao-mcp", path=alongside) or shutil.which("smartmdao-mcp")
    if found is None:                       # pragma: no cover - depends on install
        pytest.skip("smartmdao-mcp console script is not installed")
    return found


class Client:
    """A minimal MCP stdio client: newline-delimited JSON-RPC."""

    def __init__(self, command):
        self.process = subprocess.Popen(
            [command],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def read(self):
        line = self.process.stdout.readline()
        assert line.strip(), "server closed the connection without replying"
        return json.loads(line)

    def request(self, identifier, method, params=None):
        self.send(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "method": method,
                "params": params or {},
            }
        )
        return self.read()

    def close(self):
        self.process.stdin.close()
        self.process.terminate()
        self.process.wait(timeout=10)


@pytest.fixture
def client():
    connection = Client(binary())
    handshake = connection.request(
        1,
        "initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "smartmdao-tests", "version": "0"},
        },
    )
    connection.send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    connection.handshake = handshake
    try:
        yield connection
    finally:
        connection.close()


def test_server_starts_and_identifies_itself(client):
    info = client.handshake["result"]["serverInfo"]
    assert info["name"] == "smartmdao"
    # A blank version shows up in client UIs as an unnamed server.
    assert info["version"] and info["version"] != ""


def test_tools_are_listed_over_the_protocol(client):
    tools = client.request(2, "tools/list")["result"]["tools"]
    assert {tool["name"] for tool in tools} == {
        "smartmdao_cookbook",
        "run_pipeline",
        "analyze_pipeline",
        "validate_pipeline",
        "explain_pipeline",
        "render_pipeline_diagram",
    }


def test_a_real_tool_call_analyses_a_real_file(client):
    response = client.request(
        3,
        "tools/call",
        {
            "name": "analyze_pipeline",
            "arguments": {
                "path": "scripts/sellar_benchmark_mda.py",
                "inputs": ["z1", "z2", "x1"],
            },
        },
    )
    payload = json.loads(response["result"]["content"][0]["text"])

    assert payload["ok"] is True
    assert payload["recommended_solver"] == "HybridSolver"
    assert len(payload["cycles"]) == 1

    # The caller under-declared its inputs, omitting the cycle's seed - exactly
    # what a coding agent did in practice. The seed is recovered from the file's
    # own run() call rather than reported as missing, and where it came from is
    # reported so the agent can say so.
    assert payload["initial_guesses_required"] == []
    assert "y2" in payload["inputs_used"]["found_in_source"]
    assert payload["inputs_used"]["requested"] == ["z1", "z2", "x1"]


def test_resources_and_prompts_are_reachable(client):
    resources = client.request(4, "resources/list")["result"]["resources"]
    assert "smartmdao://docs/architecture" in {str(r["uri"]) for r in resources}

    prompts = client.request(5, "prompts/list")["result"]["prompts"]
    assert {p["name"] for p in prompts} == {"pipeline_from_prose", "review_pipeline"}
