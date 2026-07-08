"""
/api/v1/webhooks/github — Receives GitHub PR events and posts review comments.

Setup on GitHub:
  Repo → Settings → Webhooks → Add webhook
    Payload URL: https://your-domain.com/api/v1/webhooks/github
    Content type: application/json
    Secret: (same as GITHUB_WEBHOOK_SECRET in .env)
    Events: Pull requests

Flow:
  1. Verify HMAC-SHA256 signature  (security — reject forged events)
  2. Only process pull_request[opened|synchronize]
  3. Fetch changed files via GitHub API
  4. Filter to supported languages (Python, Java, JS, TS, Go, Rust, C++)
  5. For each file: run the review pipeline (async via Celery)
  6. Wait for all jobs to complete (max 90s)
  7. Post inline PR review comments via GitHub API
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.logging_config import get_logger

router = APIRouter()
logger = get_logger(__name__)
settings = get_settings()

# Language extension → name mapping (for pipeline language hint)
_EXT_MAP = {
    ".py":   "Python",
    ".java": "Java",
    ".js":   "JavaScript",
    ".ts":   "TypeScript",
    ".go":   "Go",
    ".rs":   "Rust",
    ".cpp":  "C++",
    ".cc":   "C++",
    ".c":    "C++",
}


# ── Signature verification ────────────────────────────────────────────────────

def _verify_signature(body: bytes, signature_header: Optional[str]) -> bool:
    """Reject events not signed by our webhook secret."""
    if not settings.GITHUB_WEBHOOK_SECRET:
        logger.warning("GITHUB_WEBHOOK_SECRET not set — skipping signature verification")
        return True

    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected = "sha256=" + hmac.new(
        settings.GITHUB_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature_header)


# ── GitHub API helpers ────────────────────────────────────────────────────────

async def _gh_get(url: str) -> Any:
    """Authenticated GET to GitHub API."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        return r.json()


async def _gh_post(url: str, payload: Dict) -> None:
    """Authenticated POST to GitHub API."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    if settings.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, headers=headers, json=payload)
        if r.status_code not in (200, 201):
            logger.warning(f"GitHub API POST failed status={r.status_code} body={r.text[:200]}")


async def _fetch_file_content(raw_url: str) -> Optional[str]:
    """Fetch raw file content from GitHub."""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(raw_url)
            if r.status_code == 200 and len(r.content) <= settings.GITHUB_MAX_FILE_BYTES:
                return r.text
    except Exception as exc:
        logger.warning(f"Failed to fetch file content: {exc}")
    return None


# ── Comment formatting ────────────────────────────────────────────────────────

def _format_pr_comment(filename: str, result: Dict) -> str:
    """Format a review result as a GitHub PR comment (Markdown)."""
    lines = [f"## 🔍 AI Code Review — `{filename}`\n"]

    before = result.get("before_complexity", {})
    after  = result.get("after_complexity", {})
    lines.append(
        f"**Complexity:** `{before.get('time','?')}` time / `{before.get('space','?')}` space"
        + (f" → `{after.get('time','?')}` / `{after.get('space','?')}`"
           if not result.get("already_optimal") else " *(already optimal)*")
    )

    security = result.get("security_issues", [])
    if security:
        lines.append(f"\n### 🔐 Security Issues ({len(security)})")
        for issue in security:
            sev   = issue.get("severity", "INFO")
            emoji = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢", "INFO": "🔵"}.get(sev, "⚪")
            line_note = f" (line {issue['line']})" if issue.get("line") else ""
            lines.append(
                f"- {emoji} **[{sev}]** `{issue.get('rule_id','')}`{line_note}: "
                f"{issue.get('description','')}\n  > 🔧 {issue.get('recommendation','')}"
            )

    changes = result.get("changes_made", [])
    if changes:
        lines.append(f"\n### 🛠️ Changes ({len(changes)})")
        for c in changes[:5]:
            lines.append(f"- **{c.get('category','').upper()}**: {c.get('description','')} → *{c.get('impact','')}*")

    if result.get("explanation"):
        lines.append(f"\n### 💡 Explanation\n{result['explanation'][:600]}…")

    lines.append(
        f"\n---\n*🤖 AI Code Reviewer | provider: `{result.get('provider_used','—')}` "
        f"| {result.get('pipeline_ms','?')} ms*"
    )
    return "\n".join(lines)


# ── Webhook endpoint ──────────────────────────────────────────────────────────

@router.post(
    "/webhooks/github",
    summary="Receive GitHub pull_request webhook events",
    status_code=202,
)
async def github_webhook(request: Request):
    body = await request.body()

    # Verify signature
    sig = request.headers.get("X-Hub-Signature-256")
    if not _verify_signature(body, sig):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    event_type = request.headers.get("X-GitHub-Event", "unknown")
    if event_type != "pull_request":
        return JSONResponse({"accepted": False, "reason": f"Ignoring event: {event_type}"})

    payload = json.loads(body)
    action  = payload.get("action")
    if action not in ("opened", "synchronize", "reopened"):
        return JSONResponse({"accepted": False, "reason": f"Ignoring action: {action}"})

    pr      = payload["pull_request"]
    repo    = payload["repository"]
    pr_num  = pr["number"]
    head_sha = pr["head"]["sha"]
    files_url = f"{repo['url']}/pulls/{pr_num}/files"
    comments_url = pr["comments_url"]
    review_url = f"{repo['url']}/pulls/{pr_num}/reviews"

    logger.info(f"GitHub webhook PR #{pr_num} action={action} repo={repo['full_name']}")

    # Fetch changed files
    try:
        files = await _gh_get(files_url)
    except Exception as exc:
        logger.error(f"Failed to fetch PR files: {exc}")
        return JSONResponse({"accepted": False, "reason": "Could not fetch PR files"})

    # Filter to supported extensions, limit count
    reviewable = []
    for f in files[:settings.GITHUB_MAX_FILES_PER_PR]:
        name = f.get("filename", "")
        ext  = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext in _EXT_MAP and f.get("status") != "removed":
            reviewable.append({
                "filename": name,
                "language": _EXT_MAP[ext],
                "raw_url":  f.get("raw_url") or f.get("contents_url"),
            })

    if not reviewable:
        return JSONResponse({"accepted": True, "reviewed_files": 0,
                             "reason": "No supported source files changed"})

    # Fetch content + enqueue Celery jobs
    import uuid
    from app.workers.tasks import review_code_task

    job_map: Dict[str, Dict] = {}   # filename → {task_id, language}
    for file_info in reviewable:
        content = await _fetch_file_content(file_info["raw_url"])
        if not content or not content.strip():
            continue

        req_id  = str(uuid.uuid4())
        task    = review_code_task.delay(
            code=content,
            language=file_info["language"],
            request_id=req_id,
            tenant_id="github-webhook",
        )
        job_map[file_info["filename"]] = {
            "task_id":  task.id,
            "language": file_info["language"],
        }
        logger.info(f"Enqueued review task_id={task.id} file={file_info['filename']}")

    # Poll for results (max 90s)
    deadline = time.time() + 90
    results: Dict[str, Any] = {}

    while job_map and time.time() < deadline:
        await asyncio.sleep(3)
        done = []
        for filename, info in job_map.items():
            from app.workers.celery_app import celery_app
            ar = celery_app.AsyncResult(info["task_id"])
            if ar.ready():
                results[filename] = ar.result if ar.successful() else None
                done.append(filename)
        for fn in done:
            del job_map[fn]

    # Post PR review with all comments
    if results:
        review_comments = []
        for filename, result in results.items():
            if result:
                review_comments.append({
                    "path":     filename,
                    "position": 1,
                    "body":     _format_pr_comment(filename, result),
                })

        if review_comments:
            await _gh_post(review_url, {
                "commit_id": head_sha,
                "body": f"🔍 AI Code Reviewer scanned {len(review_comments)} file(s).",
                "event": "COMMENT",
                "comments": review_comments,
            })
            logger.info(f"Posted review with {len(review_comments)} comments on PR #{pr_num}")

    return JSONResponse({
        "accepted": True,
        "reviewed_files": len(results),
        "timed_out_files": len(job_map),   # files whose jobs didn't finish in time
    }, status_code=202)
