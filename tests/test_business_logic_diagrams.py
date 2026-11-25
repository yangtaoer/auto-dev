from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image


def raster() -> bytes:
    output = BytesIO()
    with Image.new("RGB", (100, 100), "white") as image:
        image.save(output, "PNG")
    return output.getvalue()


class BusinessLogicDiagramsTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app import db, main
        from app.services import business_logic_diagrams as diagrams, delivery

        self.db, self.diagrams, self.delivery = db, diagrams, delivery
        directory = self.enterContext(tempfile.TemporaryDirectory())
        config = replace(main.settings, data_dir=Path(directory), worker_enabled=False, redis_url="", seed_demo=True,
                         runner_id="yangtao-pc", runner_token="local-diagram-test-runner",
                         bootstrap_admin_password="diagram-demo", bootstrap_pm_password="diagram-demo-pm",
                         smtp_host="example.test", smtp_from="autodev@example.test", task_admin_email="admin@example.test")
        for module in (db, main, diagrams, delivery):
            self.enterContext(patch.object(module, "settings", config))
        db.init_db()
        self.owner = db.row("SELECT * FROM users WHERE role='pm' ORDER BY id LIMIT 1")
        self.admin = db.row("SELECT * FROM users WHERE role='admin' ORDER BY id LIMIT 1")
        project = db.row("SELECT * FROM projects ORDER BY id LIMIT 1")
        self.runner = project["runner_id"]
        self.request_id = db.create_delivery_request(project, self.owner["id"], 610013, "local_package", [])
        db.update_request(self.request_id, title="采购审批", status="delivered", completed_at=db.utc_now(),
                          requirement_summary="部门审批后按金额分支处理", result_summary="金额超过一万元时财务复核。")
        self.session = self.enterContext(patch.object(main, "get_session_user", return_value=self.owner))
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.headers = {"Authorization": "Bearer local-diagram-test-runner"}
        self.route = f"/api/requests/{self.request_id}"
        self.worker_route = f"/api/runner/business-logic-diagrams/{self.request_id}"

    def claim(self):
        return self.client.post("/api/runner/business-logic-diagrams/claim", headers=self.headers,
                                json={"runner_id": self.runner}).json()["job"]

    def publish(self, generation):
        payload = {"runner_id": self.runner, "claim_token": generation["claim_token"],
                   "explanation": "金额超过一万元时，由财务复核后建立采购单。"}
        result = self.client.post(self.worker_route+"/complete", headers=self.headers, data=payload,
                                  files={"file": ("diagram.png", raster(), "image/png")})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()["job"]

    def test_completed_task_queues_once_and_publishes_one_preview_with_inline_mail(self):
        request = self.client.get(self.route).json()["request"]
        self.assertTrue(request["can_generate_business_logic"])
        first = self.client.post(self.route+"/business-logic-diagram", json={}).json()["job"]
        second = self.client.post(self.route+"/business-logic-diagram", json={}).json()["job"]
        self.assertEqual(first, second)
        generation = self.claim()
        self.assertEqual(generation["action"], "generate")
        self.assertIsNone(self.claim())
        job = self.publish(generation)
        self.assertEqual(self.publish(generation)["artifact_id"], job["artifact_id"])
        mail = self.claim()
        self.assertEqual(mail["action"], "mail")
        sent = []

        def capture(mailer, **kwargs):
            sent.append(mailer.build_message(**kwargs))

        payload = {"runner_id": self.runner, "claim_token": mail["claim_token"]}
        with patch.object(self.delivery.Mailer, "send", capture):
            for _ in range(2):
                receipt = self.client.post(self.worker_route+"/notify", headers=self.headers, json=payload)
                self.assertEqual(receipt.json()["job"]["mail_status"], "sent")
        self.assertEqual(len(sent), 1)
        self.assertIn(self.owner["email"], str(sent[0]["To"]))
        self.assertIn("admin@example.test", str(sent[0]["Cc"]))
        self.assertIn("财务复核", sent[0].get_body(preferencelist=("plain",)).get_content())
        self.assertIn("cid:autodev-business-logic", sent[0].get_body(preferencelist=("html",)).get_content())
        inline = next(part for part in sent[0].walk() if part.get_content_type() == "image/png")
        self.assertEqual(inline["Content-ID"], "<autodev-business-logic>")
        self.assertEqual(inline.get_content_disposition(), "inline")
        detail = self.client.get(self.route).json()["request"]
        self.assertEqual([item["kind"] for item in detail["artifacts"]], ["business_logic_diagram"])
        self.assertEqual(detail["status"], "delivered")
        preview = self.client.get(f'/api/artifacts/{job["artifact_id"]}?preview=true')
        self.assertEqual(preview.headers["content-type"], "image/png")
        self.assertEqual(preview.content, inline.get_payload(decode=True))
        self.session.return_value = {"id": 987, "role": "pm"}
        self.assertEqual(self.client.get(f'/api/artifacts/{job["artifact_id"]}?preview=true').status_code, 403)

    def test_generation_requires_delivery_and_delayed_attempt_cannot_replace_the_result(self):
        path = self.route+"/business-logic-diagram"
        self.session.return_value = {"id": 987, "role": "pm"}
        self.assertEqual(self.client.post(path, json={}).status_code, 403)
        self.session.return_value = self.owner
        self.db.update_request(self.request_id, status="developing")
        self.assertEqual(self.client.post(path, json={}).status_code, 422)
        self.db.update_request(self.request_id, status="delivered")
        self.client.post(path, json={})
        first = self.claim()
        self.diagrams.generation_update(self.request_id, self.runner, first["claim_token"], error_message="连接中断")
        self.client.post(path, json={})
        second = self.claim()
        self.assertNotEqual(first["claim_token"], second["claim_token"])
        with self.assertRaises(PermissionError):
            self.diagrams.complete(self.request_id, self.runner, first["claim_token"], "过期", raster())
        self.assertEqual(self.publish(second)["status"], "completed")


if __name__ == "__main__":
    unittest.main()
