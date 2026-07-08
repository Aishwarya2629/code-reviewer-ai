"""
Tests for Week 1-6 features.
All tests use mocking — no Redis, PostgreSQL, or real API calls required.
"""
import os
import json
import time
import hmac
import hashlib
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

os.environ["MOCK_MODE"] = "true"
os.environ["LOG_FORMAT"] = "text"
os.environ["LOG_LEVEL"] = "WARNING"


# ═══════════════════════════════════════════════════════════════
# WEEK 5 — Circuit Breaker
# ═══════════════════════════════════════════════════════════════

class TestCircuitBreaker:

    def _mock_redis(self, data: dict):
        """Return a mock Redis that reads/writes to `data` dict."""
        r = MagicMock()
        r.ping.return_value = True
        r.get.side_effect  = lambda k: data.get(k)
        r.set.side_effect  = lambda k, v, **kw: data.update({k: v})
        r.delete.side_effect = lambda *keys: [data.pop(k, None) for k in keys]
        r.incr.side_effect = lambda k: data.update({k: str(int(data.get(k, 0)) + 1)}) or int(data[k])
        r.expire.return_value = True
        r.keys.side_effect = lambda pat: [k for k in data if "state" in k]
        return r

    def test_initial_state_is_closed(self):
        from app.services.circuit_breaker import get_state, CBState
        with patch("app.services.circuit_breaker._get_redis", return_value=self._mock_redis({})):
            assert get_state("gemini-primary") == CBState.CLOSED

    def test_call_allowed_when_closed(self):
        from app.services.circuit_breaker import is_call_allowed
        with patch("app.services.circuit_breaker._get_redis", return_value=self._mock_redis({})):
            assert is_call_allowed("gemini-primary") is True

    def test_opens_after_threshold_failures(self):
        from app.services.circuit_breaker import record_failure, get_state, CBState
        data = {}
        mock_r = self._mock_redis(data)

        with patch("app.services.circuit_breaker._get_redis", return_value=mock_r), \
             patch("app.services.circuit_breaker.get_settings") as gs:
            gs.return_value.CB_FAILURE_THRESHOLD = 3
            gs.return_value.CB_RECOVERY_TIMEOUT_S = 30
            gs.return_value.CB_HALF_OPEN_MAX_CALLS = 2

            for _ in range(3):
                record_failure("gemini-primary")

            # After 3 failures, circuit should be OPEN
            assert data.get("cb:gemini-primary:state") == "OPEN"

    def test_success_clears_failure_count(self):
        from app.services.circuit_breaker import record_success
        data = {"cb:gemini-primary:state": "CLOSED", "cb:gemini-primary:failures": "2"}
        with patch("app.services.circuit_breaker._get_redis", return_value=self._mock_redis(data)), \
             patch("app.services.circuit_breaker.get_state") as gs:
            gs.return_value.value = "CLOSED"
            record_success("gemini-primary")
            # failures key should be deleted
            assert "cb:gemini-primary:failures" not in data

    def test_no_redis_fails_open(self):
        """Without Redis, circuit breaker fails open (all calls allowed)."""
        from app.services.circuit_breaker import is_call_allowed
        with patch("app.services.circuit_breaker._get_redis", return_value=None):
            assert is_call_allowed("any-provider") is True

    def test_get_all_states_returns_dict(self):
        from app.services.circuit_breaker import get_all_states
        data = {"cb:gemini-primary:state": "CLOSED", "cb:groq:state": "OPEN"}
        with patch("app.services.circuit_breaker._get_redis", return_value=self._mock_redis(data)):
            states = get_all_states()
            assert isinstance(states, dict)


# ═══════════════════════════════════════════════════════════════
# WEEK 4 — Rate Limiter
# ═══════════════════════════════════════════════════════════════

class TestRateLimiter:

    def _mock_redis_pipeline(self, current_count: int):
        pipe = MagicMock()
        pipe.execute.return_value = [None, current_count, None, None]
        pipe.zremrangebyscore.return_value = pipe
        pipe.zcard.return_value = pipe
        pipe.zadd.return_value = pipe
        pipe.expire.return_value = pipe
        r = MagicMock()
        r.ping.return_value = True
        r.pipeline.return_value = pipe
        r.zremrangebyscore.return_value = None
        r.zcard.return_value = current_count
        return r

    def test_allows_request_under_limit(self):
        from app.services.rate_limiter import check_rate_limit
        with patch("app.services.rate_limiter._get_redis",
                   return_value=self._mock_redis_pipeline(5)):
            # Should not raise
            check_rate_limit("tenant-abc", rpm=10)

    def test_blocks_request_over_limit(self):
        from app.services.rate_limiter import check_rate_limit, RateLimitExceeded
        with patch("app.services.rate_limiter._get_redis",
                   return_value=self._mock_redis_pipeline(10)):
            with pytest.raises(RateLimitExceeded):
                check_rate_limit("tenant-abc", rpm=10)

    def test_fails_open_without_redis(self):
        from app.services.rate_limiter import check_rate_limit
        with patch("app.services.rate_limiter._get_redis", return_value=None):
            # Should not raise even at "over limit"
            check_rate_limit("tenant-abc", rpm=0)

    def test_rate_limit_exceeded_carries_metadata(self):
        from app.services.rate_limiter import RateLimitExceeded
        exc = RateLimitExceeded("key-123", limit=10, window_s=60)
        assert exc.limit == 10
        assert exc.window_s == 60
        assert "key-123" in str(exc)


# ═══════════════════════════════════════════════════════════════
# WEEK 3 — Semantic Cache
# ═══════════════════════════════════════════════════════════════

class TestSemanticCache:

    def test_sha256_is_deterministic(self):
        from app.services.cache_service import _sha256
        code = "def foo(): pass"
        assert _sha256(code) == _sha256(code)
        assert len(_sha256(code)) == 64

    def test_lookup_returns_none_when_db_unavailable(self):
        from app.services.cache_service import lookup
        with patch("app.services.cache_service.db_available", return_value=False):
            assert lookup("def foo(): pass", "Python") is None

    def test_store_silently_skips_when_db_unavailable(self):
        from app.services.cache_service import store
        with patch("app.services.cache_service.db_available", return_value=False):
            store("def foo(): pass", "Python", {"result": "ok"})  # should not raise

    def test_exact_hit_increments_counter(self):
        from app.services import cache_service
        mock_conn = MagicMock()
        mock_cur  = MagicMock()
        mock_cur.fetchone.return_value = ("uuid-123", json.dumps({"valid": True}))
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__  = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__  = MagicMock(return_value=False)

        with patch("app.services.cache_service.db_available", return_value=True), \
             patch("app.services.cache_service.get_conn", return_value=mock_conn), \
             patch("app.services.cache_service.cache_hits_total") as mock_hits:
            result = cache_service.lookup("def foo(): pass", "Python")
            assert result is not None
            mock_hits.inc.assert_called_once()


# ═══════════════════════════════════════════════════════════════
# WEEK 1 — Celery Queue
# ═══════════════════════════════════════════════════════════════

class TestCeleryTasks:

    def test_task_result_shape(self):
        """_pipeline_result_to_dict must include all required keys."""
        from app.workers.tasks import _pipeline_result_to_dict
        fake_state = {
            "input_type": "code",
            "already_optimal": False,
            "detected_language": "Python",
            "optimized_code": "def foo(): pass",
            "before_time": "O(n)",
            "before_space": "O(1)",
            "after_time": "O(n)",
            "after_space": "O(1)",
            "security_findings": [],
            "changes_made": [],
            "analysis": "good",
            "explanation": "nothing changed",
            "fallback_used": False,
            "provider_used": "gemini-primary",
            "pipeline_ms": 1234,
        }
        result = _pipeline_result_to_dict(fake_state, "def foo(): pass")
        for key in ("valid", "optimized_code", "security_issues",
                    "before_complexity", "after_complexity", "explanation"):
            assert key in result, f"Missing key: {key}"

    def test_task_valid_flag_from_input_type(self):
        from app.workers.tasks import _pipeline_result_to_dict
        invalid_state = {"input_type": "invalid"}
        result = _pipeline_result_to_dict(invalid_state, "garbage")
        assert result["valid"] is False


# ═══════════════════════════════════════════════════════════════
# WEEK 2 — GitHub Webhook
# ═══════════════════════════════════════════════════════════════

class TestGitHubWebhook:

    def _make_signature(self, body: bytes, secret: str) -> str:
        return "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()

    def test_valid_signature_accepted(self):
        from app.api.v1.routes.webhooks import _verify_signature
        body   = b'{"action":"opened"}'
        secret = "test-secret-123"
        sig    = self._make_signature(body, secret)
        with patch("app.api.v1.routes.webhooks.settings") as s:
            s.GITHUB_WEBHOOK_SECRET = secret
            assert _verify_signature(body, sig) is True

    def test_invalid_signature_rejected(self):
        from app.api.v1.routes.webhooks import _verify_signature
        body = b'{"action":"opened"}'
        with patch("app.api.v1.routes.webhooks.settings") as s:
            s.GITHUB_WEBHOOK_SECRET = "real-secret"
            assert _verify_signature(body, "sha256=wrongsig") is False

    def test_missing_signature_rejected(self):
        from app.api.v1.routes.webhooks import _verify_signature
        with patch("app.api.v1.routes.webhooks.settings") as s:
            s.GITHUB_WEBHOOK_SECRET = "some-secret"
            assert _verify_signature(b"body", None) is False

    def test_no_secret_configured_accepts_all(self):
        """If webhook secret not set, we warn but don't block (dev mode)."""
        from app.api.v1.routes.webhooks import _verify_signature
        with patch("app.api.v1.routes.webhooks.settings") as s:
            s.GITHUB_WEBHOOK_SECRET = None
            assert _verify_signature(b"any", "sha256=anything") is True

    def test_pr_comment_format(self):
        from app.api.v1.routes.webhooks import _format_pr_comment
        result = {
            "before_complexity": {"time": "O(n²)", "space": "O(n)"},
            "after_complexity":  {"time": "O(n)",  "space": "O(1)"},
            "already_optimal": False,
            "security_issues": [
                {"severity": "HIGH", "rule_id": "SEC-001", "line": 3,
                 "description": "Hardcoded secret", "recommendation": "Use env vars"}
            ],
            "changes_made": [
                {"category": "algorithmic", "description": "Use hashmap",
                 "impact": "O(n²) → O(n)"}
            ],
            "explanation": "Changed to use a hashmap for O(n) lookup.",
            "provider_used": "gemini-primary",
            "pipeline_ms": 5400,
        }
        comment = _format_pr_comment("src/main.py", result)
        assert "src/main.py" in comment
        assert "SEC-001" in comment
        assert "O(n²)" in comment
        assert "gemini-primary" in comment

    def test_ext_map_covers_common_languages(self):
        from app.api.v1.routes.webhooks import _EXT_MAP
        for ext in (".py", ".java", ".js", ".ts", ".go", ".rs"):
            assert ext in _EXT_MAP


# ═══════════════════════════════════════════════════════════════
# WEEK 6 — Analytics API
# ═══════════════════════════════════════════════════════════════

class TestAnalyticsEndpoints:

    def test_overview_returns_zeros_without_db(self, client):
        r = client.get("/api/v1/analytics/overview?hours=24")
        assert r.status_code == 200
        body = r.json()
        assert "total_requests" in body
        assert "cache_hit_rate" in body

    def test_providers_returns_cb_states(self, client):
        r = client.get("/api/v1/analytics/providers?hours=24")
        assert r.status_code == 200
        body = r.json()
        assert "circuit_breakers" in body
        assert "usage" in body

    def test_security_summary_structure(self, client):
        r = client.get("/api/v1/analytics/security?hours=24")
        assert r.status_code == 200
        body = r.json()
        assert "total_issues" in body
        assert "total_reviews" in body

    def test_latency_endpoint(self, client):
        r = client.get("/api/v1/analytics/latency?hours=24")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_languages_endpoint(self, client):
        r = client.get("/api/v1/analytics/languages?hours=24")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_metrics_endpoint_returns_prometheus_format(self, client):
        r = client.get("/metrics")
        assert r.status_code == 200
        # Prometheus format starts with # HELP or metric name
        assert b"_total" in r.content or b"# HELP" in r.content

    def test_jobs_get_unknown_id(self, client):
        """Polling unknown job_id returns PENDING (not an error)."""
        with patch("app.api.v1.routes.jobs.celery_app") as mock_cel:
            ar = MagicMock()
            ar.state  = "PENDING"
            ar.result = None
            mock_cel.AsyncResult.return_value = ar
            r = client.get("/api/v1/jobs/nonexistent-id-123")
            assert r.status_code == 200
            assert r.json()["status"] == "PENDING"
