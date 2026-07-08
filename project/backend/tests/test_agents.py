"""
Tests for /api/v1/problem and agent nodes.
"""
import pytest


class TestProblemEndpoint:

    def test_problem_valid(self, client, two_sum_problem):
        r = client.post("/api/v1/problem",
                        json={"problem": two_sum_problem, "language": "Python"})
        assert r.status_code == 200
        body = r.json()
        assert body["valid"] is True
        assert len(body["solutions"]) == 4

    def test_problem_always_returns_4_solutions(self, client, two_sum_problem):
        r = client.post("/api/v1/problem",
                        json={"problem": two_sum_problem, "language": "Java"})
        assert len(r.json()["solutions"]) == 4

    def test_problem_solution_schema(self, client, two_sum_problem):
        r = client.post("/api/v1/problem",
                        json={"problem": two_sum_problem, "language": "Python"})
        for sol in r.json()["solutions"]:
            assert "title" in sol
            assert "clean_code" in sol
            assert "commented_code" in sol
            assert "time_complexity" in sol
            assert "space_complexity" in sol

    def test_problem_too_short_returns_422(self, client):
        r = client.post("/api/v1/problem",
                        json={"problem": "short", "language": "Python"})
        assert r.status_code == 422

    def test_problem_too_large_returns_413(self, client):
        r = client.post("/api/v1/problem",
                        json={"problem": "word " * 2000, "language": "Python"})
        assert r.status_code == 413

    def test_problem_request_id_in_body(self, client, two_sum_problem):
        r = client.post("/api/v1/problem",
                        json={"problem": two_sum_problem, "language": "Python"})
        assert "request_id" in r.json()


class TestAgentNodes:
    """Unit tests for individual agent nodes — no LLM calls needed."""

    def test_classifier_empty_code(self):
        from app.agents.nodes.classifier import classifier_node
        state = {"raw_code": "", "requested_language": "auto", "nodes_executed": []}
        result = classifier_node(state)
        assert result["input_type"] == "invalid"

    def test_classifier_language_override(self):
        from app.agents.nodes.classifier import classifier_node
        state = {
            "raw_code": "print('hello')",
            "requested_language": "Python",
            "nodes_executed": [],
        }
        result = classifier_node(state)
        assert result["detected_language"] == "Python"
        assert result["classifier_confidence"] == 1.0

    def test_security_regex_detects_hardcoded_secret(self):
        from app.agents.nodes.security_scanner import _regex_scan
        code = 'API_KEY = "sk-supersecret123abc"'
        findings = _regex_scan(code)
        assert any(f["rule_id"] == "SEC-001" for f in findings)

    def test_security_regex_detects_shell_true(self):
        from app.agents.nodes.security_scanner import _regex_scan
        code = "subprocess.run(cmd, shell=True)"
        findings = _regex_scan(code)
        assert any(f["rule_id"] == "SEC-002" for f in findings)

    def test_security_regex_detects_eval(self):
        from app.agents.nodes.security_scanner import _regex_scan
        code = "result = eval(user_input)"
        findings = _regex_scan(code)
        assert any(f["rule_id"] == "SEC-004" for f in findings)

    def test_security_regex_detects_pickle(self):
        from app.agents.nodes.security_scanner import _regex_scan
        code = "data = pickle.loads(raw_bytes)"
        findings = _regex_scan(code)
        assert any(f["rule_id"] == "SEC-005" for f in findings)

    def test_security_clean_code_no_findings(self):
        from app.agents.nodes.security_scanner import _regex_scan
        code = (
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n"
        )
        findings = _regex_scan(code)
        assert findings == []

    def test_complexity_heuristic_python(self):
        from app.agents.nodes.complexity_analyzer import _ast_heuristic
        code = "for i in range(n):\n    for j in range(n):\n        pass"
        time_c, space_c = _ast_heuristic(code, "Python")
        assert "O(" in time_c

    def test_complexity_heuristic_non_python(self):
        from app.agents.nodes.complexity_analyzer import _ast_heuristic
        t, s = _ast_heuristic("int main() {}", "C++")
        assert t == "O(?)"

    def test_ocr_text_classifier_code(self):
        from app.services.ocr_service import classify_extracted_text
        code = "def fibonacci(n):\n    if n <= 1: return n\n    return fibonacci(n-1) + fibonacci(n-2)"
        assert classify_extracted_text(code) == "code"

    def test_ocr_text_classifier_problem(self):
        from app.services.ocr_service import classify_extracted_text
        problem = "Given an array of integers, find two numbers that add up to target. Input: [2,7,11,15], target=9. Output: [0,1]"
        assert classify_extracted_text(problem) == "problem"
