from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def event(method, **payload):
    return SimpleNamespace(method=method, payload=payload)


class CodexErrorTests(unittest.TestCase):
    def collect(self, events):
        from app.services.codex_runner import CodexRunner
        self.events = []
        return CodexRunner()._collect_output(iter(events), lambda kind, message: self.events.append((kind, message)))

    def test_version_rejection_is_actionable_not_missing_json(self):
        detail = {"message": json.dumps({"type": "error", "status": 400, "error": {
            "message": "The 'gpt-6-astra' model requires a newer version of Codex. Please upgrade."}}),
            "codexErrorInfo": "other"}
        with self.assertRaisesRegex(RuntimeError, "版本过旧.*不是额度不足.*HTTP 400.*gpt-6-astra"):
            self.collect([event("error", error=detail, willRetry=False),
                          event("turn/completed", turn={"status": "failed", "error": detail})])
        self.assertEqual(self.events[0][0], "devcore.error")

    def test_quota_and_connection_and_auth_are_distinct(self):
        from app.services.codex_runner import CodexRunner
        for info, message in [("usageLimitExceeded", "额度已达上限"),
                              ("unauthorized", "身份认证失败"),
                              ({"responseStreamDisconnected": {"httpStatusCode": 502}}, "连接或响应流异常")]:
            with self.subTest(info=info):
                text = CodexRunner._failure_message({"message": "upstream rejected", "codexErrorInfo": info})
                self.assertIn(message, text)
                if isinstance(info, dict):
                    self.assertIn("HTTP 502", text)

    def test_retrying_error_then_success_does_not_fail(self):
        result = self.collect([event("error", error={"message": "connection reset"}, willRetry=True),
                               event("item/completed", item={"type": "agentMessage", "text": '{"ok":true}'}),
                               event("turn/completed", turn={"status": "completed", "error": None})])
        self.assertEqual(json.loads(result), {"ok": True})
        self.assertEqual(self.events[0][0], "devcore.retrying")

    def test_failed_turn_rejects_even_valid_partial_json(self):
        with self.assertRaisesRegex(RuntimeError, "额度已达上限"):
            self.collect([event("item/completed", item={"type": "agentMessage", "text": '{"decision":"completed"}'}),
                          event("turn/completed", turn={"status": "failed", "error": {
                              "message": "You've hit your usage limit", "codexErrorInfo": "usageLimitExceeded"}})])

    def test_interruption_and_truncated_stream_never_succeed(self):
        for status in (None, "interrupted", "failed"):
            with self.subTest(status=status), self.assertRaises(RuntimeError):
                self.collect([event("item/completed", item={"type": "agentMessage", "text": '{}'}),
                              event("turn/completed", turn={"status": status})])

    def test_commentary_cannot_become_the_final_result(self):
        with self.assertRaisesRegex(RuntimeError, "未返回结构化研发结果"):
            self.collect([event("item/completed", item={"type": "agentMessage", "phase": "commentary", "text": "处理中"}),
                          event("turn/completed", turn={"status": "completed"})])

    def test_pydantic_style_payload_is_supported(self):
        payload = SimpleNamespace(model_dump=lambda **kwargs: {"turn": {"status": "failed", "error": {"message": "connection failed"}}})
        with self.assertRaisesRegex(RuntimeError, "连接或响应流异常"):
            self.collect([SimpleNamespace(method="turn/completed", payload=payload)])

    def test_stream_exception_preserves_prior_upstream_error(self):
        from app.services.codex_runner import CodexRunner
        def broken_stream():
            yield event("error", error={"message": "usage limit exceeded"}, willRetry=False)
            raise OSError("stream closed")
        with self.assertRaisesRegex(RuntimeError, "额度已达上限"):
            CodexRunner()._collect_output(broken_stream(), lambda *args: None)

    def test_secret_values_are_redacted_and_details_are_bounded(self):
        from app.services.codex_runner import CodexRunner
        with patch.dict(os.environ, {"TEST_TOKEN": "private-env-value"}), patch("app.services.codex_runner.settings", SimpleNamespace(codex_api_key="private-file-value")):
            text = CodexRunner._failure_message({"message": "private-env-value private-file-value Bearer abcdef password=hunter99 sk-proj-test123 " + "x" * 4000})
        for secret in ("private-env-value", "private-file-value", "abcdef", "hunter99", "sk-proj-test123"):
            self.assertNotIn(secret, text)
        self.assertLess(len(text), 1500)
        self.assertIn("[REDACTED]", text)

    def test_default_model_and_runtime_dependency_are_pinned(self):
        from app.config import ROOT, settings
        self.assertEqual(settings.codex_model, "gpt-6-astra")
        spec = json.loads((ROOT / "local-runner/codex-runtime/package.json").read_text())
        self.assertEqual(spec["dependencies"]["@openai/codex"], "0.153.4")
        self.assertIn("openai-codex==0.147.0", (ROOT / "requirements-runner.txt").read_text())

    def test_failure_email_shows_upstream_reason(self):
        from app.services.codex_runner import CodexRunner
        from app.services.delivery import Mailer
        from app.domain import DeliveryMode
        reason = CodexRunner._failure_message({"message": "The 'gpt-6-astra' model requires a newer version of Codex."})
        body = Mailer().delivery_html({"id": "test-error", "work_item_id": 1678762,
            "title": "测试失败通知", "delivery_mode": DeliveryMode.LOCAL_PACKAGE.value,
            "created_at": "2026-09-07T02:19:27+00:00", "artifacts": [],
            "status": "failed", "error_message": reason}, terminal_status="failed")
        self.assertIn("运行器版本过旧", body)
        self.assertIn("gpt-6-astra", body)
        self.assertNotIn("未返回结构化研发结果", body)

    def test_native_runtime_uses_managed_package_not_sdk_bundle(self):
        from app.services.codex_runtime import managed_binary
        with tempfile.TemporaryDirectory() as directory, patch("platform.system", return_value="Windows"), patch("platform.machine", return_value="AMD64"):
            root = Path(directory)
            binary = root / "node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe"
            binary.parent.mkdir(parents=True)
            binary.touch()
            self.assertEqual(managed_binary(root), binary.resolve())

    def test_old_or_invalid_explicit_runtime_never_falls_back_silently(self):
        from app.services.codex_runtime import resolve_codex_runtime
        with patch.dict(os.environ, {"CODEX_BIN": "invalid-codex.exe"}), self.assertRaisesRegex(RuntimeError, "未找到"):
            resolve_codex_runtime()
        with tempfile.NamedTemporaryFile() as executable, patch.dict(os.environ, {"CODEX_BIN": executable.name}), \
                patch("app.services.codex_runtime.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="codex-cli 0.147.0")):
            with self.assertRaisesRegex(RuntimeError, "过旧.*GPT-6 Astra"):
                resolve_codex_runtime()


if __name__ == "__main__":
    unittest.main()
