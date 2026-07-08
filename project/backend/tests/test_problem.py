"""
Tests for /api/v1/problem endpoint — edge cases and schema validation.
"""
import pytest


class TestProblemEdgeCases:

    def test_problem_too_short(self, client):
        r = client.post("/api/v1/problem",
                        json={"problem": "sort it", "language": "Python"})
        # Pydantic min_length=10 → 422
        assert r.status_code == 422

    def test_problem_oversized(self, client):
        r = client.post("/api/v1/problem",
                        json={"problem": "word " * 2000, "language": "Python"})
        assert r.status_code == 413

    def test_problem_always_four_solutions(self, client, two_sum_problem):
        r = client.post("/api/v1/problem",
                        json={"problem": two_sum_problem, "language": "Python"})
        assert r.status_code == 200
        assert len(r.json()["solutions"]) == 4

    def test_problem_solution_titles(self, client, two_sum_problem):
        r = client.post("/api/v1/problem",
                        json={"problem": two_sum_problem, "language": "Python"})
        titles = [s["title"] for s in r.json()["solutions"]]
        # All four must be present (order preserved)
        for expected in ("Brute Force", "Better", "Optimised", "Advanced"):
            assert any(expected in t for t in titles), f"Missing: {expected}"

    def test_problem_complexity_fields_present(self, client, two_sum_problem):
        r = client.post("/api/v1/problem",
                        json={"problem": two_sum_problem, "language": "Java"})
        for sol in r.json()["solutions"]:
            assert "time" in sol["time_complexity"]
            assert "space" in sol["space_complexity"]

    def test_problem_java_language(self, client, two_sum_problem):
        r = client.post("/api/v1/problem",
                        json={"problem": two_sum_problem, "language": "Java"})
        assert r.status_code == 200

    def test_problem_unsupported_language_422(self, client, two_sum_problem):
        r = client.post("/api/v1/problem",
                        json={"problem": two_sum_problem, "language": "COBOL"})
        assert r.status_code == 422

    def test_problem_has_request_id(self, client, two_sum_problem):
        r = client.post("/api/v1/problem",
                        json={"problem": two_sum_problem, "language": "Python"})
        assert "request_id" in r.json()

    def test_problem_x_request_id_header(self, client, two_sum_problem):
        r = client.post("/api/v1/problem",
                        json={"problem": two_sum_problem, "language": "Python"})
        assert "x-request-id" in r.headers

    def test_problem_custom_request_id_propagated(self, client, two_sum_problem):
        custom_id = "test-req-abc-123"
        r = client.post(
            "/api/v1/problem",
            json={"problem": two_sum_problem, "language": "Python"},
            headers={"X-Request-ID": custom_id},
        )
        assert r.headers.get("x-request-id") == custom_id

    def test_problem_missing_language_defaults(self, client, two_sum_problem):
        # language has a default (Python) so omitting it should still work
        r = client.post("/api/v1/problem", json={"problem": two_sum_problem})
        assert r.status_code == 200

    def test_problem_clean_code_not_empty(self, client, two_sum_problem):
        r = client.post("/api/v1/problem",
                        json={"problem": two_sum_problem, "language": "Python"})
        for sol in r.json()["solutions"]:
            assert sol.get("clean_code", "").strip() != ""
