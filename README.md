# MCP Test Harness

Security and conformance testing for MCP servers.

[datasculptures.com](https://datasculptures.com)

## Install

```bash
pip install mcp-test-harness
```

Or from source:

```bash
pip install -e .
```

## Usage

```bash
# Test a STDIO MCP server (all suites)
mcp-test-harness stdio -- python -m my_server

# Run only conformance tests
mcp-test-harness stdio --suite conformance -- python -m my_server

# Run only security tests
mcp-test-harness stdio --suite security -- python -m my_server

# Run only operational tests
mcp-test-harness stdio --suite operational -- python -m my_server

# JSON output to file
mcp-test-harness stdio --format json -o report.json -- python -m my_server

# Markdown report (for GitHub PRs, READMEs, CI summaries)
mcp-test-harness stdio --format markdown -o report.md -- python -m my_server

# Print a shields.io badge URL for the score
mcp-test-harness stdio --badge -- python -m my_server

# Verbose (show all test details including passing tests)
mcp-test-harness stdio -v -- python -m my_server

# Use a config file
mcp-test-harness stdio --config .mcp-test-harness.yaml
```

## Config File

Create `.mcp-test-harness.yaml` in your working directory to avoid repeating CLI arguments:

```yaml
# .mcp-test-harness.yaml
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

Run with just:

```bash
mcp-test-harness stdio
```

Config file values are overridden by CLI arguments. The `command` field must be a list, not a string.

## Scoring

Every run produces a **0–100 quality score**:

| Score | Grade | Label |
|---|---|---|
| 90–100 | A | Excellent |
| 75–89 | B | Good |
| 60–74 | C | Acceptable |
| 40–59 | D | Poor |
| 0–39 | F | Failing |

Scoring weights (deducted from 100):

| Finding | Deduction |
|---|---|
| Conformance failure | 5 pts |
| Security failure | 8 pts |
| Security warning | 2 pts |
| Operational failure | 3 pts |
| Operational warning | 1 pt |
| Skip or harness error | 0 pts |

The score appears in all three output formats. It does **not** affect the exit code — only failures do.

## Badge

```bash
mcp-test-harness stdio --badge -- python -m my_server
# https://img.shields.io/badge/MCP%20Harness-96%2F100%20A-brightgreen
```

Add to your README:

```markdown
![MCP Harness](https://img.shields.io/badge/MCP%20Harness-96%2F100%20A-brightgreen)
```

## What It Tests

**Conformance** (`--suite conformance`)
- Initialization lifecycle and protocol version negotiation
- Capability declaration vs. actual behaviour
- Tool listing, invocation, and input schema validation
- JSON-RPC error handling (parse errors, invalid requests, method not found)

**Security** (`--suite security`)
- Shell injection: semicolons, pipes, backticks, `$()`, `&&`
- Environment variable expansion: `${HOME}`, `$PATH`, `%USERPROFILE%`
- Path traversal via tools that read files: `../` sequences, URL-encoding, null bytes
- Resource URI scope: traversal URIs, scheme injection (`javascript:`, `http://`)
- Input validation bypass: wrong types, missing required args, null bytes, oversized input, deeply nested objects, large arrays
- Information disclosure in error responses (stack traces, file paths, credential references)

**Operational** (`--suite operational`)
- Partial JSON recovery
- Binary garbage resilience
- Rapid sequential request handling
- Large request handling (1 MB payload)
- Response time baseline
- Unknown notification tolerance

## Output

```
MCP Test Harness v0.3.0
Spec: 2025-11-25
Server: my-server v1.0.0
Transport: stdio

=== initialization ===
  ✓ initialize_response_valid
  ✓ version_negotiation
  ...
  8 passed

=== security ===
  ✓ shell_metachar_semicolon__echo
  ⚠ null_byte_argument — null byte in string argument not rejected
  ...
  5 passed, 2 warnings

────────────────────────────────────────
Total: 52 passed, 0 failed, 2 warnings, 2 skipped
Score: 96/100 (A — Excellent)
```

**Exit codes:** `0` = no failures, `1` = failures detected, `2` = harness error.

**Symbols:** `✓` pass, `✗` fail, `⚠` warn, `○` skip, `!` error.

## CI Integration

See [`examples/github-actions.yml`](examples/github-actions.yml) for a complete GitHub Actions workflow that:
- Runs the full test suite
- Uploads a JSON report as an artifact
- Writes a markdown summary to the GitHub Actions job summary
- Prints a badge URL

## Spec Version

Targets MCP specification version `2025-11-25`.

## Options

| Option | Description |
|---|---|
| `--suite conformance\|security\|operational\|all` | Select suites (default: all, repeatable) |
| `--format text\|json\|markdown` | Output format (default: text) |
| `--output FILE`, `-o FILE` | Write report to file |
| `--config FILE`, `-c FILE` | Config file (default: `.mcp-test-harness.yaml`) |
| `--verbose`, `-v` | Show details for all tests |
| `--timeout SECONDS` | Per-request timeout (default: 10) |
| `--badge` | Print a shields.io badge URL |
