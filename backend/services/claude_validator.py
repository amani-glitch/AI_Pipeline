"""
Claude AI validation service — inspect Vite source code for deployment-breaking
issues and auto-fix them using Claude claude-sonnet-4-5-20250929.

Collects relevant source files, builds a comprehensive prompt, calls the
Anthropic API with retry logic, parses the structured JSON response, and
applies fixes back to disk.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Optional

import anthropic

from config import Settings
from models.deployment import ClaudeValidationResult
from models.enums import DeploymentMode

logger = logging.getLogger("webdeploy.claude_validator")

# File extensions to collect for inspection
_SOURCE_EXTENSIONS = frozenset({
    ".js", ".jsx", ".ts", ".tsx", ".css", ".html",
})

# Files to always include by name (at any directory level)
_INCLUDE_NAMES = frozenset({
    "vite.config.js", "vite.config.ts", "vite.config.mjs", "vite.config.mts",
    "package.json",
})

# Directories to skip entirely
_SKIP_DIRS = frozenset({
    "node_modules", "dist", ".git", "build", "__MACOSX", ".cache",
    ".vite", ".next",
})

# Maximum individual file size we send to Claude (256 KB)
_MAX_FILE_BYTES = 256 * 1024

# Maximum total payload size (~400 KB of source code text)
_MAX_TOTAL_BYTES = 400 * 1024

# Claude API retry settings
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2  # seconds


class ClaudeValidationService:
    """Use Claude AI to inspect and auto-fix Vite project source code."""

    def __init__(
        self,
        log_callback: Optional[Callable] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._log = log_callback or (lambda msg, **kw: None)
        self._settings = settings or Settings()
        self._client: Optional[anthropic.Anthropic] = None

    # ── Public API ────────────────────────────────────────────────────

    def validate_and_fix(
        self,
        source_path: str,
        website_name: str,
        mode: DeploymentMode,
        has_router: bool = False,
    ) -> ClaudeValidationResult:
        """
        Inspect source code with Claude and apply any suggested fixes.

        Parameters
        ----------
        source_path : str
            Absolute path to the Vite project root (contains package.json).
        website_name : str
            Deployment name used for base-path calculation.
        mode : DeploymentMode
            demo or prod — determines the target base path.
        has_router : bool
            Whether a client-side router was detected.

        Returns
        -------
        ClaudeValidationResult
        """
        self._log("Starting Claude AI validation", level="INFO", step="AI_INSPECT")

        # 1. Collect source files
        files = self._collect_source_files(source_path)
        if not files:
            self._log("No source files found to inspect", level="WARNING", step="AI_INSPECT")
            return ClaudeValidationResult(
                status="pass",
                summary="No source files found — skipping AI inspection.",
            )

        self._log(f"Collected {len(files)} file(s) for inspection", level="INFO", step="AI_INSPECT")

        # 2. Determine base path
        base_path = f"/{website_name}/" if mode == DeploymentMode.DEMO else "/"
        self._log(f"Target base path: {base_path}", level="INFO", step="AI_INSPECT")

        # 3. Build prompt
        prompt = self._build_prompt(files, source_path, base_path, website_name, mode, has_router)

        # 4. Call AI API (Claude → Gemini fallback)
        response_text = self._call_claude(prompt)

        # 4b. If Claude unavailable, try OpenRouter fallback (free model)
        if not response_text:
            response_text = self._call_openrouter(prompt)

        # 4c. If all AI unavailable, skip validation
        if not response_text:
            return ClaudeValidationResult(
                status="pass",
                summary="AI validation skipped (no AI provider available) — proceeding without inspection.",
            )

        # 5. Parse response
        result = self._parse_response(response_text)

        self._log(
            f"Claude found {len(result.issues_found)} issue(s), suggested {len(result.fixes)} fix(es)",
            level="INFO",
            step="AI_INSPECT",
        )

        # 6. Apply fixes
        if result.fixes:
            self._log("Applying AI-suggested fixes", level="INFO", step="AI_FIX")
            applied_count = self._apply_fixes(source_path, result.fixes)
            self._log(f"Applied {applied_count}/{len(result.fixes)} fix(es)", level="INFO", step="AI_FIX")

        return result

    # ── File collection ───────────────────────────────────────────────

    def _collect_source_files(self, source_path: str) -> dict[str, str]:
        """
        Walk the source tree and collect relevant file contents.

        Returns a dict of {relative_path: file_content}.
        """
        files: dict[str, str] = {}
        total_bytes = 0

        for dirpath, dirnames, filenames in os.walk(source_path):
            # Prune skippable directories
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

            for filename in filenames:
                abs_path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(abs_path, source_path)

                # Check inclusion criteria
                ext = os.path.splitext(filename)[1].lower()
                if ext not in _SOURCE_EXTENSIONS and filename not in _INCLUDE_NAMES:
                    continue

                # Skip oversized files
                try:
                    size = os.path.getsize(abs_path)
                except OSError:
                    continue

                if size > _MAX_FILE_BYTES:
                    self._log(
                        f"Skipping large file: {rel_path} ({size // 1024} KB)",
                        level="WARNING",
                        step="AI_INSPECT",
                    )
                    continue

                if total_bytes + size > _MAX_TOTAL_BYTES:
                    self._log(
                        "Reached payload size limit — truncating file collection",
                        level="WARNING",
                        step="AI_INSPECT",
                    )
                    break

                try:
                    content = Path(abs_path).read_text(encoding="utf-8", errors="replace")
                    files[rel_path] = content
                    total_bytes += size
                except OSError as exc:
                    self._log(f"Could not read {rel_path}: {exc}", level="WARNING", step="AI_INSPECT")

        return files

    # ── Prompt construction ───────────────────────────────────────────

    def _build_prompt(
        self,
        files: dict[str, str],
        source_path: str,
        base_path: str,
        website_name: str,
        mode: DeploymentMode,
        has_router: bool,
    ) -> str:
        """Build the detailed inspection prompt for Claude."""
        file_listing = ""
        for rel_path, content in files.items():
            file_listing += f"\n--- FILE: {rel_path} ---\n{content}\n"

        router_note = ""
        if has_router:
            router_note = """
- ROUTER BASENAME: The project uses a client-side router. Ensure the router
  basename is set to "{base_path}". For react-router-dom, this means
  <BrowserRouter basename="{base_path}"> or equivalent. For vue-router, set
  the base option in createRouter.
""".format(base_path=base_path)

        prompt = f"""You are an expert Vite deployment engineer. You are inspecting a Vite project
that will be deployed with base path: "{base_path}"

Deployment mode: {mode.value}
Website name: {website_name}
Target base path: {base_path}

Your task: Identify ALL deployment-breaking issues and provide exact fixes.

## INSPECTION CHECKLIST

1. **vite.config base path**: The `base` property in vite.config must be set to
   exactly "{base_path}". If it is missing, set to "/" when it should be
   "{base_path}", or set to a wrong value, this is a critical fix.

2. **Asset references**: Any hardcoded absolute paths in source code like
   `/assets/`, `/images/`, `/favicon.ico` must be prefixed with the base path
   or use relative paths / import statements.

3. **index.html paths**: In index.html, ensure script src and link href
   attributes either use relative paths or start with "{base_path}".

4. **CSS url() paths**: In CSS files, check for `url(/...)` absolute
   references that will break under a sub-path deployment.

5. **Navigation links**: Hardcoded `href="/"` or `to="/"` links should
   use the base path.

6. **package.json scripts**: The build script should ideally not hardcode
   a different base. Check for `--base` flags.
{router_note}
## SOURCE FILES

{file_listing}

## RESPONSE FORMAT

You MUST respond with ONLY valid JSON, no markdown fences, no explanation
outside the JSON. Use this exact schema:

{{
  "status": "pass" | "needs_fixes",
  "issues": [
    {{
      "file": "relative/path/to/file",
      "line": 10,
      "severity": "critical" | "warning",
      "description": "What is wrong"
    }}
  ],
  "fixes": [
    {{
      "file": "relative/path/to/file",
      "description": "What this fix does",
      "original": "exact original text to find",
      "replacement": "exact replacement text"
    }}
  ],
  "summary": "Brief human-readable summary of the inspection"
}}

If everything looks correct, return status "pass" with empty issues and fixes
arrays and a summary saying the project is ready for deployment.
"""
        return prompt

    # ── Claude API call ───────────────────────────────────────────────

    def _get_client(self) -> Optional[anthropic.Anthropic]:
        """Lazy-init the Anthropic client. Returns None if no API key."""
        if self._client is None:
            api_key = self._settings.ANTHROPIC_API_KEY
            if not api_key:
                return None
            self._client = anthropic.Anthropic(api_key=api_key)
        return self._client

    def _call_claude(self, prompt: str) -> str:
        """
        Call Claude API with retry logic (3 retries, exponential backoff).

        Returns the raw text response.
        """
        client = self._get_client()
        if client is None:
            self._log(
                "No ANTHROPIC_API_KEY configured — skipping AI validation",
                level="WARNING",
                step="AI_INSPECT",
            )
            return ""
        last_error: Optional[Exception] = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                self._log(
                    f"Calling Claude API (attempt {attempt}/{_MAX_RETRIES})",
                    level="INFO",
                    step="AI_INSPECT",
                )
                message = client.messages.create(
                    model="claude-sonnet-4-5-20250929",
                    max_tokens=4096,
                    messages=[
                        {"role": "user", "content": prompt},
                    ],
                )
                # Extract text content
                text_blocks = [
                    block.text for block in message.content if hasattr(block, "text")
                ]
                response_text = "\n".join(text_blocks)
                self._log("Claude API call succeeded", level="INFO", step="AI_INSPECT")
                return response_text

            except anthropic.RateLimitError as exc:
                last_error = exc
                delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                self._log(
                    f"Rate limited — retrying in {delay}s (attempt {attempt}/{_MAX_RETRIES})",
                    level="WARNING",
                    step="AI_INSPECT",
                )
                time.sleep(delay)

            except anthropic.APIStatusError as exc:
                last_error = exc
                if exc.status_code >= 500:
                    delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    self._log(
                        f"Server error {exc.status_code} — retrying in {delay}s",
                        level="WARNING",
                        step="AI_INSPECT",
                    )
                    time.sleep(delay)
                else:
                    # Client error (4xx other than 429) — expired key, out of credits, etc.
                    self._log(
                        f"Claude API error ({exc.status_code}): {exc.message} — skipping AI validation",
                        level="WARNING",
                        step="AI_INSPECT",
                    )
                    return ""

            except anthropic.APIConnectionError as exc:
                last_error = exc
                delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                self._log(
                    f"Connection error — retrying in {delay}s",
                    level="WARNING",
                    step="AI_INSPECT",
                )
                time.sleep(delay)

        # All retries exhausted — skip AI validation instead of crashing the pipeline
        self._log(
            f"Claude API unavailable after {_MAX_RETRIES} attempts ({type(last_error).__name__}) — skipping AI validation",
            level="WARNING",
            step="AI_INSPECT",
        )
        return ""

    def _call_openrouter(self, prompt: str) -> str:
        """
        Fallback: call OpenRouter API with a free model when Claude is unavailable.

        Uses the OpenAI-compatible API at https://openrouter.ai/api/v1.
        Returns the raw text response, or empty string on failure.
        """
        api_key = self._settings.OPENROUTER_API_KEY
        if not api_key:
            self._log(
                "No OPENROUTER_API_KEY configured — no fallback available",
                level="WARNING",
                step="AI_INSPECT",
            )
            return ""

        try:
            import httpx

            self._log(
                "Falling back to OpenRouter (free model) for validation",
                level="INFO",
                step="AI_INSPECT",
            )

            response = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._settings.OPENROUTER_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 4096,
                },
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if text:
                self._log("OpenRouter API call succeeded", level="INFO", step="AI_INSPECT")
            return text

        except Exception as exc:
            self._log(
                f"OpenRouter API also failed: {exc} — skipping AI validation",
                level="WARNING",
                step="AI_INSPECT",
            )
            return ""

    # ── Response parsing ──────────────────────────────────────────────

    def _parse_response(self, response_text: str) -> ClaudeValidationResult:
        """Parse Claude's JSON response into a ClaudeValidationResult."""
        # Strip markdown code fences if present
        text = response_text.strip()
        if text.startswith("```"):
            # Remove first line (```json or ```)
            lines = text.split("\n")
            lines = lines[1:]
            # Remove last ``` line
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            self._log(
                f"Failed to parse Claude response as JSON: {exc}",
                level="ERROR",
                step="AI_INSPECT",
            )
            logger.debug("Raw Claude response: %s", response_text[:2000])
            return ClaudeValidationResult(
                status="pass",
                summary="AI response could not be parsed — proceeding without fixes.",
            )

        return ClaudeValidationResult(
            status=data.get("status", "pass"),
            issues_found=data.get("issues", []),
            fixes=data.get("fixes", []),
            summary=data.get("summary", ""),
        )

    # ── Fix application ───────────────────────────────────────────────

    def _apply_fixes(self, source_path: str, fixes: list[dict]) -> int:
        """
        Apply the fixes suggested by Claude.

        Each fix is a dict with:
            - file: relative path
            - original: text to find
            - replacement: text to replace with
            - description: human-readable description

        Returns the number of successfully applied fixes.
        """
        applied = 0

        for fix in fixes:
            rel_path = fix.get("file", "")
            original = fix.get("original", "")
            replacement = fix.get("replacement", "")
            description = fix.get("description", "")

            if not rel_path or not original:
                self._log(
                    f"Skipping incomplete fix: {description}",
                    level="WARNING",
                    step="AI_FIX",
                )
                continue

            abs_path = os.path.join(source_path, rel_path)

            # Security: ensure the fix target is within the source directory
            real_source = os.path.realpath(source_path)
            real_target = os.path.realpath(abs_path)
            if not real_target.startswith(real_source):
                self._log(
                    f"Fix target escapes source directory — skipping: {rel_path}",
                    level="ERROR",
                    step="AI_FIX",
                )
                continue

            if not os.path.isfile(abs_path):
                self._log(
                    f"Fix target file not found — skipping: {rel_path}",
                    level="WARNING",
                    step="AI_FIX",
                )
                continue

            try:
                content = Path(abs_path).read_text(encoding="utf-8")
                if original not in content:
                    self._log(
                        f"Original text not found in {rel_path} — skipping fix: {description}",
                        level="WARNING",
                        step="AI_FIX",
                    )
                    continue

                new_content = content.replace(original, replacement, 1)
                Path(abs_path).write_text(new_content, encoding="utf-8")
                self._log(f"Fixed {rel_path}: {description}", level="INFO", step="AI_FIX")
                applied += 1

            except OSError as exc:
                self._log(
                    f"Failed to apply fix to {rel_path}: {exc}",
                    level="ERROR",
                    step="AI_FIX",
                )

        return applied
