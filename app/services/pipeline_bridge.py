"""Keep the existing skill command on the versioned runner, preserving its original."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MARKER = "# AutoDev versioned TFS pipeline bridge"
BRIDGE = MARKER + '''
from pathlib import Path
import importlib.util
import json
import sys

pointer = json.loads(Path(__file__).with_name('autodev-runner.json').read_text('utf-8'))
target = Path(pointer['runner'])
if not target.is_file():
    raise RuntimeError('AutoDev 发版运行程序缺失，请修复本机执行器安装；未退回无校验的旧发版程序')
sys.path.insert(0, str(target.parent))
spec = importlib.util.spec_from_file_location('autodev_versioned_tfs_pipeline', target)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
globals().update({k:v for k,v in vars(module).items() if not k.startswith('__')})
if __name__ == '__main__':
    raise SystemExit(module.main())
'''


def install_pipeline_bridge(project_root: Path, skill_root: Path | None = None) -> bool:
    skill = skill_root or Path.home() / ".codex/skills/tfs-run-pipeline"
    script = skill / "scripts/run_tfs_pipeline.py"
    runner = project_root / "app/services/tfs_pipeline_runner.py"
    if not script.is_file() or not runner.is_file():
        return False
    original = script.read_bytes()
    pointer = script.with_name("autodev-runner.json")
    expected = json.dumps({"runner": str(runner.resolve())}, ensure_ascii=False, indent=2) + "\n"
    if not original.decode("utf-8-sig").startswith(MARKER):
        backup = script.with_name("run_tfs_pipeline.before-autodev-" + hashlib.sha256(original).hexdigest()[:12] + ".py")
        if backup.exists() and backup.read_bytes() != original:
            raise RuntimeError("发版技能备份不一致，未覆盖技能入口")
        if not backup.exists():
            backup.write_bytes(original)
    if not pointer.exists() or pointer.read_text("utf-8") != expected:
        temporary = pointer.with_suffix(".pending")
        temporary.write_text(expected, encoding="utf-8")
        temporary.replace(pointer)
    if original.decode("utf-8-sig") != BRIDGE:
        temporary = script.with_suffix(".pending")
        temporary.write_text(BRIDGE, encoding="utf-8")
        temporary.replace(script)
    # Keep raw catalog readers and the skill's own CLI on the same stage graph.
    catalog_path = skill.parent / "tfs-project-catalog/references/project-catalog.seed.json"
    workflows = project_root / "app/services/release_workflows.json"
    if catalog_path.is_file() and workflows.is_file():
        raw = catalog_path.read_text("utf-8")
        catalog = json.loads(raw)
        overrides = json.loads(workflows.read_text("utf-8"))["projects"]
        changed = False
        for project in catalog.get("projects", []):
            config = overrides.get(project.get("standardName"))
            if not config:
                continue
            previous = {p["definitionId"]: p for p in project.get("pipelines", [])}
            pipelines = []
            for item in config["pipelines"]:
                merged = {**previous.get(item["definitionId"], {}), **item}
                merged.setdefault("definitionUrl", "http://dev.tellhowsoft.com/DefaultCollection/" + item["tfsProject"]
                                  + "/_build?definitionId=" + str(item["definitionId"]))
                pipelines.append(merged)
            if project.get("pipelines") != pipelines or project.get("releaseIntegrity") != config["releaseIntegrity"]:
                project.update(pipelines=pipelines, releaseIntegrity=config["releaseIntegrity"])
                changed = True
        if changed:
            backup = catalog_path.with_name("project-catalog.before-autodev-" + hashlib.sha256(raw.encode()).hexdigest()[:12] + ".json")
            if not backup.exists():
                backup.write_text(raw, encoding="utf-8")
            temporary = catalog_path.with_suffix(".pending")
            temporary.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(catalog_path)
    return True
