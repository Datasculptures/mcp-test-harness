# MCP Test Harness — How-To Guide

Security and conformance testing for MCP servers. [datasculptures.com](https://datasculptures.com)

---

## What This Tool Does

MCP Test Harness connects to a running MCP server over STDIO, fires conformance and security-focused payloads at it, and produces a structured quality report with a 0–100 score. It targets the MCP specification version `2025-11-25`.

Three test suites are included:

- **Conformance** — protocol negotiation, tool listing, input schema validation, JSON-RPC error handling
- **Security** — shell injection, path traversal, env variable expansion, info disclosure in errors
- **Operational** — resilience to malformed input, large payloads, rapid requests, unknown notifications

---

## Prerequisites

- **Python 3.10 or newer.** Check with:
  ```bash
  python --version
  ```
  If you have both `python` and `python3` on your system, use whichever resolves to 3.10+.

- **pip** (bundled with Python). Check with:
  ```bash
  pip --version
  ```

- **A running MCP server** that can be launched as a subprocess via a command (e.g. `python -m my_server`). The server must communicate over STDIO using the MCP protocol.

---

## Installation

### Option A — Install from PyPI (recommended)

```bash
pip install mcp-test-harness
```

This installs the `mcp-test-harness` CLI command globally (or into your active virtual environment).

### Option B — Install from source

Clone or download this repository, then from inside the project directory:

```bash
pip install -e .
```

The `-e` flag installs in "editable" mode — changes to the source files take effect immediately without reinstalling.

### Verify the installation

```bash
mcp-test-harness --help
```

You should see the help text with available subcommands and options. If you get `command not found`, check that pip's script directory is on your `PATH` (see Troubleshooting below).

---

## Running Against a Server

The main subcommand is `stdio`. Everything after the `--` separator is the command used to launch your MCP server as a subprocess.

### Basic run — all suites, text output

```bash
mcp-test-harness stdio -- python -m my_server
```

Replace `python -m my_server` with whatever command starts your server. The harness launches it, connects over STDIO, runs all tests, then shuts it down.

**Example with a real command:**
```bash
mcp-test-harness stdio -- node ./dist/server.js
```

```bash
mcp-test-harness stdio -- ./my-mcp-server --port 0
```

### Run only one suite

```bash
# Conformance tests only (protocol + tool behaviour)
mcp-test-harness stdio --suite conformance -- python -m my_server

# Security tests only (injection, traversal, info disclosure)
mcp-test-harness stdio --suite security -- python -m my_server

# Operational tests only (resilience, large payloads, timing)
mcp-test-harness stdio --suite operational -- python -m my_server
```

You can repeat `--suite` to combine specific suites:
```bash
mcp-test-harness stdio --suite conformance --suite security -- python -m my_server
```

### Verbose output — see every test result

By default, only failures, warnings, and skips are shown. Add `-v` to see all results including passing tests:

```bash
mcp-test-harness stdio -v -- python -m my_server
```

### Save output to a file

```bash
# Plain text report
mcp-test-harness stdio --format text -o report.txt -- python -m my_server

# JSON report (machine-readable, for CI or dashboards)
mcp-test-harness stdio --format json -o report.json -- python -m my_server

# Markdown report (for GitHub PRs, READMEs, CI job summaries)
mcp-test-harness stdio --format markdown -o report.md -- python -m my_server
```

### Print a badge URL

```bash
mcp-test-harness stdio --badge -- python -m my_server
```

This prints a [shields.io](https://shields.io) badge URL you can embed in your README:

```markdown
![MCP Harness](https://img.shields.io/badge/MCP%20Harness-96%2F100%20A-brightgreen)
```

### Set a custom timeout

The default per-request timeout is 10 seconds. Increase it for slow servers:

```bash
mcp-test-harness stdio --timeout 30 -- python -m my_server
```

---

## Using a Config File

If you run the harness often against the same server, create a `.mcp-test-harness.yaml` file in your working directory to avoid repeating arguments every time.

**Example `.mcp-test-harness.yaml`:**

```yaml
command:
  - python
  - -m
  - my_mcp_server
suites:
  - conformance
  - security
  - operational
timeout: 15
format: text
verbose: false
```

> **Important:** `command` must be a YAML list, not a string. Each word/argument is a separate list item.

Once the config file exists, run the harness with no arguments:

```bash
mcp-test-harness stdio
```

CLI arguments override config file values, so you can still do one-off overrides:

```bash
# Use config file but override format to JSON for this run
mcp-test-harness stdio --format json -o report.json
```

To point at a config file at a non-default path:

```bash
mcp-test-harness stdio --config /path/to/my-config.yaml
```

---

## Understanding the Output

A typical run looks like this:

```
MCP Test Harness v0.3.0
Spec: 2025-11-25
Server: my-server v1.0.0
Transport: stdio

=== initialization ===
  ✓ initialize_response_valid
  ✓ version_negotiation
  ✓ capabilities_declared
  ...
  8 passed

=== tools ===
  ✓ tools_list_returns_array
  ✓ tool_call_valid_response
  ✗ tool_input_schema_invalid — schema missing 'type' field
  ...
  7 passed, 1 failed

=== security ===
  ✓ shell_metachar_semicolon__echo
  ⚠ null_byte_argument — null byte in string argument not rejected
  ...
  5 passed, 2 warnings

────────────────────────────────────────
Total: 52 passed, 1 failed, 2 warnings, 2 skipped
Score: 83/100 (B — Good)
```

### Symbols

| Symbol | Meaning |
|--------|---------|
| `✓` | Test passed |
| `✗` | Test failed |
| `⚠` | Warning (non-blocking issue) |
| `○` | Skipped (server doesn't expose the relevant capability) |
| `!` | Harness error (bug in the harness, not your server) |

### Score

| Score | Grade | Label |
|-------|-------|-------|
| 90–100 | A | Excellent |
| 75–89 | B | Good |
| 60–74 | C | Acceptable |
| 40–59 | D | Poor |
| 0–39 | F | Failing |

Points are deducted from 100 as follows:

| Finding | Deduction |
|---------|-----------|
| Conformance failure | 5 pts |
| Security failure | 8 pts |
| Security warning | 2 pts |
| Operational failure | 3 pts |
| Operational warning | 1 pt |
| Skip or harness error | 0 pts |

The score is informational — it does **not** affect the exit code.

### Exit Codes

| Code | Meaning |
|------|---------|
| `0` | No failures |
| `1` | One or more test failures detected |
| `2` | Harness error (the harness itself failed to run) |

Use exit code `1` in CI to gate on test failures:

```bash
mcp-test-harness stdio -- python -m my_server || echo "Tests failed!"
```

---

## CI Integration (GitHub Actions)

A complete workflow template is included at [`examples/github-actions.yml`](examples/github-actions.yml). It:

1. Runs the full test suite against your server
2. Uploads the JSON report as a build artifact
3. Writes a markdown summary to the GitHub Actions job summary page
4. Prints a badge URL

Copy it to `.github/workflows/mcp-test.yml` in your server's repository and adjust the server launch command.

---

## Troubleshooting

### `command not found: mcp-test-harness`

pip installs scripts to a directory that may not be on your `PATH`. Common locations:

- **Linux/macOS (system Python):** `~/.local/bin`
- **Linux/macOS (virtual env):** `<venv>/bin`
- **Windows:** `%APPDATA%\Python\PythonXX\Scripts`

Add the appropriate directory to your `PATH`, or use a virtual environment (recommended):

```bash
python -m venv .venv
source .venv/bin/activate      # macOS/Linux
.venv\Scripts\activate         # Windows
pip install mcp-test-harness
mcp-test-harness --help
```

### Server starts but no tests run

The harness launches your server as a subprocess and communicates over its STDIO streams. Make sure your server:

1. **Reads JSON-RPC messages from stdin** and **writes responses to stdout**
2. Does not write anything to stdout before the MCP handshake (startup logs must go to stderr, not stdout)
3. Does not exit immediately — it must stay alive to handle requests

### All tests are skipped

Skips usually mean the harness could not negotiate the MCP handshake (initialization failed), or the server declared no tools. Run with `-v` to see the skip reasons:

```bash
mcp-test-harness stdio -v -- python -m my_server
```

### Timeout errors

Increase `--timeout`:

```bash
mcp-test-harness stdio --timeout 30 -- python -m my_server
```

If timeouts persist, the server may be blocking on stdout (e.g., buffering). Make sure your server flushes stdout after each response.

### Harness exits with code 2

Exit code `2` means the harness itself encountered an error (not a test failure). Run with `-v` to see the error details. Common causes:

- The server command is wrong or the executable is not found
- The server crashed during startup before the handshake completed
- A Python dependency is missing — try reinstalling: `pip install --force-reinstall mcp-test-harness`

---

## Options Reference

| Option | Short | Description |
|--------|-------|-------------|
| `--suite` | | `conformance`, `security`, `operational`, or `all` (default: all, repeatable) |
| `--format` | | `text`, `json`, or `markdown` (default: `text`) |
| `--output FILE` | `-o` | Write report to a file instead of stdout |
| `--config FILE` | `-c` | Config file path (default: `.mcp-test-harness.yaml`) |
| `--verbose` | `-v` | Show details for all tests, including passes |
| `--timeout SECONDS` | | Per-request timeout in seconds (default: 10) |
| `--badge` | | Print a shields.io badge URL after the run |
