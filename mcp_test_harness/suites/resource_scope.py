"""
Resource scope test suite.

Tests whether resources/read enforces declared boundaries:
baseline read, nonexistent URIs, URI traversal, and URI scheme injection.

Skipped entirely when the server does not declare the resources capability.
All tests use the raw client.
"""

from __future__ import annotations

import asyncio
import time

from mcp_test_harness.client.protocol import get_error_code
from mcp_test_harness.client.stdio_raw import ReadTimeout, StdioRawClient
from mcp_test_harness.config import ServerConfig
from mcp_test_harness.suites.base import BaseSuite, TestResult

# Content indicators that suggest a successful path traversal via resource URI
_TRAVERSAL_INDICATORS = [
    "root:", "/bin/sh", "/bin/bash", "nobody:",          # Unix passwd
    "[boot loader]", "[operating systems]", "MSDOS.SYS", # Windows system.ini
]

# URI schemes that should not trigger outbound requests or execution
_SUSPECT_SCHEMES = [
    ("http://203.0.113.1/harness_ssrf_probe",  "external HTTP URI"),
    ("javascript:alert(1)",                    "javascript URI"),
    ("file:///etc/passwd",                     "absolute file URI to system path"),
]


class ResourceScopeSuite(BaseSuite):
    """Test resource-read boundary enforcement."""

    name = "resource_scope"

    async def run(self, config: ServerConfig) -> list[TestResult]:
        results: list[TestResult] = []
        tests = [
            self._test_resource_read_declared_uri,
            self._test_resource_read_nonexistent_uri,
            self._test_resource_read_traversal_uri,
            self._test_resource_uri_scheme_injection,
        ]
        try:
            async with StdioRawClient(
                config.command, env=config.env, timeout=config.timeout
            ) as client:
                init_result = await client.initialize()
                if "resources" not in init_result.get("capabilities", {}):
                    return [self._skip("resource_scope", "server did not declare resources capability")]

                # Fetch resource list once; share across tests
                listed = await self._list_resources(client)

                for test_fn in tests:
                    t0 = time.monotonic()
                    try:
                        result = await test_fn(client, listed, config)
                    except asyncio.CancelledError as exc:
                        result = self._error(
                            test_fn.__name__.lstrip("_test_"),
                            f"test cancelled: {exc}",
                        )
                    except Exception as exc:
                        result = self._error(
                            test_fn.__name__.lstrip("_test_"),
                            f"unexpected harness error: {exc}",
                        )
                    result.duration_ms = (time.monotonic() - t0) * 1000
                    results.append(result)

        except asyncio.CancelledError as exc:
            results.append(self._error("resource_scope", f"suite cancelled: {exc}"))
        except Exception as exc:
            results.append(self._error("resource_scope", f"suite-level error: {exc}"))

        return results

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    async def _list_resources(self, client: StdioRawClient) -> list[dict]:
        try:
            response = await client.send_request("resources/list")
        except ReadTimeout:
            return []
        if "error" in response:
            return []
        items = response.get("result", {}).get("resources", [])
        return items if isinstance(items, list) else []

    async def _read_resource(
        self, client: StdioRawClient, uri: str
    ) -> dict | None:
        try:
            return await client.send_request(
                "resources/read", params={"uri": uri}
            )
        except ReadTimeout:
            return None

    def _contains_traversal_content(self, response: dict | None) -> bool:
        if response is None:
            return False
        full_text = _extract_resource_text(response)
        return any(ind in full_text for ind in _TRAVERSAL_INDICATORS)

    # ------------------------------------------------------------------ #
    # Tests
    # ------------------------------------------------------------------ #

    async def _test_resource_read_declared_uri(
        self,
        client: StdioRawClient,
        listed: list[dict],
        config: ServerConfig,
    ) -> TestResult:
        name = "resource_read_declared_uri"
        if not listed:
            return self._skip(name, "resources/list returned empty list")

        first = listed[0]
        uri = first.get("uri", "")
        if not uri:
            return self._skip(name, "first resource has no URI")

        response = await self._read_resource(client, uri)
        if response is None:
            return self._fail(name, f"timed out reading declared resource {uri!r}")
        if "error" in response:
            code = get_error_code(response)
            return self._fail(name, f"resources/read returned error {code} for declared URI {uri!r}")
        if response.get("result", {}).get("isError"):
            return self._fail(name, f"resources/read returned isError for declared URI {uri!r}")
        return self._pass(name, f"successfully read declared resource {uri!r}")

    async def _test_resource_read_nonexistent_uri(
        self,
        client: StdioRawClient,
        listed: list[dict],
        config: ServerConfig,
    ) -> TestResult:
        name = "resource_read_nonexistent_uri"
        uri = "file:///nonexistent_harness_test_path_xyz_9f3a"
        response = await self._read_resource(client, uri)
        if response is None:
            return self._warn(name, "server timed out on nonexistent resource URI")
        if "error" in response:
            return self._pass(name, f"server returned error for nonexistent URI (code {get_error_code(response)})")
        result = response.get("result", {})
        if result.get("isError"):
            return self._pass(name, "server returned isError for nonexistent URI")
        # Server returned content for a nonexistent path — suspicious
        return self._warn(name, "server returned content for nonexistent resource URI — may be serving arbitrary paths")

    async def _test_resource_read_traversal_uri(
        self,
        client: StdioRawClient,
        listed: list[dict],
        config: ServerConfig,
    ) -> TestResult:
        name = "resource_read_traversal_uri"

        # Build traversal URIs based on listed file:// resources when possible
        traversal_uris: list[str] = []
        for resource in listed:
            uri = resource.get("uri", "")
            if uri.startswith("file://"):
                path_part = uri[len("file://"):]
                segments = path_part.replace("\\", "/").split("/")
                # Walk up to root and try /etc/passwd
                depth = max(1, len([s for s in segments if s and s != ".."])) + 2
                prefix = "/".join([".."] * depth)
                traversal_uris.append(f"file://{prefix}/etc/passwd")
                break

        # Always probe a few generic traversal URIs regardless
        traversal_uris += [
            "file:///../../../../etc/passwd",
            "file:///../../../etc/passwd",
        ]

        for uri in traversal_uris:
            response = await self._read_resource(client, uri)
            if self._contains_traversal_content(response):
                return self._fail(name, f"resource URI traversal succeeded: {uri!r}")

        return self._pass(name, "resource URI traversal attempts blocked")

    async def _test_resource_uri_scheme_injection(
        self,
        client: StdioRawClient,
        listed: list[dict],
        config: ServerConfig,
    ) -> TestResult:
        name = "resource_uri_scheme_injection"
        warnings: list[str] = []

        for uri, description in _SUSPECT_SCHEMES:
            response = await self._read_resource(client, uri)
            if response is None:
                continue  # timeout — ambiguous, don't warn

            if "error" in response:
                continue  # correctly rejected

            result = response.get("result", {})
            if result.get("isError"):
                continue  # correctly rejected

            # Server returned content — inspect it
            content = _extract_resource_text(response)
            if content:
                warnings.append(f"server returned content for {description} ({uri!r})")
            else:
                # Empty success response — mildly suspicious but not conclusive
                warnings.append(f"server accepted {description} without error ({uri!r})")

        if warnings:
            return self._warn(name, "; ".join(warnings))
        return self._pass(name, "all suspect URI schemes were rejected or returned no content")


# ------------------------------------------------------------------ #
# Pure helpers
# ------------------------------------------------------------------ #

def _extract_resource_text(response: dict) -> str:
    """Extract all text content from a resources/read response."""
    texts: list[str] = []
    result = response.get("result", {})
    for item in result.get("contents", []):
        if isinstance(item, dict):
            text = item.get("text", "")
            if isinstance(text, str):
                texts.append(text)
    return "\n".join(texts)
