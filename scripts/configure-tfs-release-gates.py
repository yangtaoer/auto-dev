"""Install the audited regional build gate. Dry-run unless --confirm-update is set."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app/services"))
from tfs_pipeline_runner import TfsBuildClient, DEFAULT_BASE_URL
from tfs_release_integrity import GATE_FILE, GATE_NAME, gate_script


def configure(client, *, confirm: bool, backup_dir: Path) -> dict:
    definition = client.get_definition(5481)
    assert definition["repository"]["name"] == "dcsd-springboot-starter"
    assert definition["name"] == "标准版springboot-四川广安"
    steps = definition.get("build")
    target = definition
    if not steps:
        phases = (definition.get("process") or {}).get("phases", [])
        if len(phases) != 1:
            raise RuntimeError("主包流水线不是已核实的单阶段结构，未更新")
        steps = phases[0]["steps"]
        target = phases[0].setdefault("target", {"type": 1})
    maven = [i for i, s in enumerate(steps) if s.get("enabled", True) and (s.get("inputs") or {}).get("goals")]
    if len(maven) != 1 or "sichuanga" not in steps[maven[0]]["inputs"]["goals"]:
        raise RuntimeError("主包 Maven Profile 不符合广安配置，未更新")
    source = gate_script()
    existing = [s for s in steps if s.get("displayName") == GATE_NAME]
    copies = [s for s in steps if s.get("enabled", True) and "TargetFolder" in (s.get("inputs") or {})]
    clean_copy = bool(copies) and all(str(s["inputs"].get("CleanTargetFolder", "false")).lower() == "true"
                                    and str(s["inputs"].get("OverWrite", "false")).lower() == "true" for s in copies)
    critical = [s for s in steps if s.get("enabled", True) and ("TargetFolder" in (s.get("inputs") or {})
                or "ArtifactName" in (s.get("inputs") or {}) or "sshEndpoint" in (s.get("inputs") or {}))]
    strict_conditions = all(not s.get("alwaysRun") and not s.get("continueOnError") and s.get("condition") == "succeeded()" for s in critical)
    linux_demand = "Agent.OS -equals Linux"
    if (len(existing) == 1 and existing[0].get("enabled") is True
            and not existing[0].get("continueOnError")
            and existing[0].get("inputs", {}).get("script", "").replace("\r\n", "\n") == source.replace("\r\n", "\n")
            and clean_copy and strict_conditions and linux_demand in (target.get("demands") or [])
            and (definition.get("variables", {}).get("AUTODEV_EXPECTED_MODULES") or {}).get("allowOverride")):
        return {"definitionId": 5481, "changed": False, "revision": definition["revision"]}
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / ("5481-revision-" + str(definition["revision"]) + ".json")
    raw = json.dumps(definition, ensure_ascii=False, indent=2)
    if backup.exists() and backup.read_text("utf-8") != raw:
        raise RuntimeError("原流水线备份与相同 revision 不一致，未更新")
    if not backup.exists():
        backup.write_text(raw, encoding="utf-8")
    tasks = client.request_json("GET", client.base_url + "/_apis/distributedtask/tasks?api-version=4.1").get("value", [])
    commands = [t for t in tasks if t.get("name") in {"CmdLine", "CommandLine"} and (t.get("version") or {}).get("major") == 2]
    if not commands:
        raise RuntimeError("服务器未安装命令行 v2 构建任务，未更新")
    task = commands[-1]
    steps[:] = [s for s in steps if s.get("displayName") != GATE_NAME]
    index = next(i for i, s in enumerate(steps) if s.get("enabled", True) and (s.get("inputs") or {}).get("goals")) + 1
    steps.insert(index, {"enabled": True, "continueOnError": False, "alwaysRun": False,
        "condition": "succeeded()", "timeoutInMinutes": 10, "displayName": GATE_NAME,
        "task": {"id": task["id"], "versionSpec": "2.*", "definitionType": "task"},
        "inputs": {"script": source, "failOnStderr": "false", "workingDirectory": "$(Build.SourcesDirectory)"}})
    if not copies:
        raise RuntimeError("主包流水线缺少已核实的产物复制步骤，未更新")
    for copy in copies:
        if "artifactstagingdirectory" not in str(copy["inputs"].get("TargetFolder", "")).lower():
            raise RuntimeError("产物复制目标不属于构建暂存目录，未更新")
        copy["inputs"].update(CleanTargetFolder="true", OverWrite="true")
    for step in critical:
        step.update(alwaysRun=False, continueOnError=False, condition="succeeded()")
    if linux_demand not in target.setdefault("demands", []):
        target["demands"].append(linux_demand)
    definition.setdefault("variables", {})["AUTODEV_EXPECTED_MODULES"] = {"value": "", "allowOverride": True, "isSecret": False}
    result = {"definitionId": 5481, "changed": True, "gateScriptSha256": hashlib.sha256(source.encode()).hexdigest(),
              "backup": str(backup), "confirmed": confirm}
    if confirm:
        response = client.request_json("PUT", client.api_url("definitions/5481"), definition)
        result["revision"] = response["revision"]
        fresh = client.get_definition(5481)
        live_steps = fresh.get("build") or (fresh.get("process") or {})["phases"][0]["steps"]
        gate = next(s for s in live_steps if s.get("displayName") == GATE_NAME)
        assert gate["enabled"] and not gate["continueOnError"] and gate["inputs"]["script"].replace("\r\n", "\n") == source.replace("\r\n", "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-update", action="store_true")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "data/release-gate-backups")
    args = parser.parse_args()
    token = os.environ.get("TFS_PAT", "")
    if not token:
        raise RuntimeError("请在本机环境中提供 TFS_PAT，不能放入命令行")
    print(json.dumps(configure(TfsBuildClient(DEFAULT_BASE_URL, "DCS", token), confirm=args.confirm_update,
                               backup_dir=args.backup_dir), ensure_ascii=False, indent=2))
