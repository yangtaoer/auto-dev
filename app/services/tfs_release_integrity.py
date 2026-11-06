"""Source-pinned Maven dependencies and package proof, including encrypted deliveries.

The build checks complete module bytes before the official encryption step.
After encryption, resources and class presence are checked without altering JARs.
This module intentionally has no application settings or credential-file imports.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote, urlsplit
from urllib.request import urlopen
import xml.etree.ElementTree as ET
import zipfile


PARAMETER = "AUTODEV_EXPECTED_MODULES"
GATE_NAME = "AutoDev - Verify dependency modules"
ROOT = Path(__file__).resolve().parents[2]
GATE_FILE = ROOT / "local-runner/project-scripts/verify-release-modules.sh"


def gate_script() -> str:
    java = GATE_FILE.with_name("AutoDevVerifyModules.java").read_text("utf-8")
    return GATE_FILE.read_text("utf-8").replace("@@JAVA_SOURCE@@", java)


def apply_workflows(catalog: dict) -> dict:
    overrides = json.loads(Path(__file__).with_name("release_workflows.json").read_text("utf-8"))
    for project in catalog.get("projects", []):
        extra = overrides["projects"].get(project.get("standardName"))
        if not extra:
            continue
        previous = {int(p["definitionId"]): p for p in project.get("pipelines", [])}
        project["pipelines"] = [{**previous.get(p["definitionId"], {}), **p} for p in extra["pipelines"]]
        project["releaseIntegrity"] = extra["releaseIntegrity"]
    return catalog


def normalized_parameters(build: dict) -> dict:
    value = build.get("parameters") or {}
    return json.loads(value) if isinstance(value, str) else value


def build_matches(build: dict, plan: dict) -> bool:
    return (int((build.get("definition") or {}).get("id") or plan["definitionId"]) == plan["definitionId"]
            and build.get("sourceVersion") == plan.get("sourceVersion")
            and build.get("sourceBranch") == plan["sourceBranch"]
            and all(normalized_parameters(build).get(k) == v for k, v in plan.get("parameters", {}).items()))


def module_manifest(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as jar:
        names = [n for n in jar.namelist() if not n.endswith("/")]
        if len(names) != len(set(names)) or len(names) > 30000:
            raise RuntimeError("依赖模块含重复或过多条目")
        if sum(x.file_size for x in jar.infolist()) > 250 * 1024 * 1024:
            raise RuntimeError("依赖模块解压大小超过限制")
        return {"classes": sorted(n for n in names if n.endswith(".class")),
                "resources": {n: hashlib.sha256(jar.read(n)).hexdigest() for n in sorted(names) if not n.endswith(".class")}}


def verify_package(path: Path, proofs: list[dict]) -> list[dict]:
    """Verify encrypted output without modifying it or pretending classes are plaintext."""
    results = []
    with zipfile.ZipFile(path) as outer:
        if len(outer.namelist()) != len(set(outer.namelist())):
            raise RuntimeError("正式包存在重复条目")
        for proof in proofs:
            name = proof["repository"]
            entries = [n for n in outer.namelist() if n.startswith("BOOT-INF/lib/" + name + "-") and n.endswith(".jar")]
            if len(entries) != 1:
                raise RuntimeError(f"正式包缺少唯一的依赖模块 {name}")
            actual = module_manifest(outer.read(entries[0]))
            expected = proof["manifest"]
            if actual["resources"] != expected["resources"]:
                differences = sorted(n for n in set(actual["resources"]) | set(expected["resources"])
                                     if actual["resources"].get(n) != expected["resources"].get(n))
                raise RuntimeError(f"正式包中的 {name} 资源与目标提交构建不一致：" + "、".join(differences[:8]))
            if actual["classes"] != expected["classes"]:
                raise RuntimeError(f"正式包中的 {name} 加密后类目录不完整")
            results.append({k: proof[k] for k in ("repository", "definitionId", "buildId", "sourceVersion", "moduleSha256")}
                            | {"resourceCount": len(expected["resources"]), "classCount": len(expected["classes"]), "verified": True})
    return results


class ReleaseIntegrity:
    def __init__(self, project: dict, plans: list[dict], clients: dict, cache: Path, required: dict | None = None):
        self.project, self.plans, self.clients = project, plans, clients
        self.contract = project["releaseIntegrity"]
        self.cache = Path(cache) / "verified-modules"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.required = required or {}
        self.proofs: dict[int, dict] = {}

    def git_url(self, client, repository: str, suffix: str = "") -> str:
        return client.base_url + "/" + quote(client.tfs_project, safe="") + "/_apis/git/repositories/" + quote(repository, safe="") + suffix

    def freeze(self) -> None:
        by_name = {p["repository"]: p for p in self.plans}
        unknown = set(self.required.get("commits", {})) - set(by_name)
        if unknown:
            raise RuntimeError("待交付提交所属仓库不在发版清单：" + "、".join(sorted(unknown)))
        for plan in self.plans:
            client = self.clients[plan["tfsProject"]]
            repo = client.request_json("GET", self.git_url(client, plan["repository"]) + "?api-version=4.1")
            plan["repositoryId"] = repo["id"]
            refs = client.request_json("GET", self.git_url(client, repo["id"], "/refs")
                                       + "?filter=" + quote(plan["sourceBranch"].removeprefix("refs/"), safe="") + "&api-version=4.1").get("value", [])
            head = next((r["objectId"] for r in refs if r["name"] == plan["sourceBranch"]), "")
            if not re.fullmatch(r"[a-f0-9]{40}", head):
                raise RuntimeError(f"无法冻结 {plan['repository']} 的目标分支")
            plan["sourceVersion"] = head
            for commit in self.required.get("commits", {}).get(plan["repository"], []):
                if not re.fullmatch(r"[a-f0-9]{40}", commit):
                    raise RuntimeError("非法的待交付提交")
                if commit == head:
                    continue
                url = self.git_url(client, repo["id"], "/diffs/commits")
                query = f"?baseVersion={commit}&baseVersionType=commit&targetVersion={head}&targetVersionType=commit&$top=1&api-version=4.1"
                comparison = client.request_json("GET", url + query)
                if comparison.get("commonCommit") != commit:
                    raise RuntimeError(f"{plan['repository']} 的目标分支未包含需求提交 {commit}")

    def validate_definition(self, client, plan: dict) -> None:
        definition = client.get_definition(plan["definitionId"])
        if str((definition.get("repository", {}).get("properties") or {}).get("skipSyncSource", "false")).lower() == "true":
            raise RuntimeError("流水线跳过了源码同步，不能证明本次构建使用了冻结提交")
        if plan.get("repositoryId") and str(definition["repository"]["id"]).casefold() != plan["repositoryId"].casefold():
            raise RuntimeError("流水线绑定的仓库 GUID 与发版仓库不一致")
        steps = list(definition.get("build") or [])
        for phase in (definition.get("process") or {}).get("phases", []):
            steps.extend(phase.get("steps") or [])
        steps = [s for s in steps if s.get("enabled", True)]
        if plan["purpose"] == "dependency":
            if not any("deploy" in str((s.get("inputs") or {}).get("goals", "")).split() for s in steps):
                raise RuntimeError(f"依赖流水线 {plan['definitionId']} 不会发布 Maven 模块")
            return
        expected_script = gate_script().replace("\r\n", "\n")
        gates = [(i, s) for i, s in enumerate(steps) if s.get("displayName") == GATE_NAME]
        maven = [i for i, s in enumerate(steps) if (s.get("inputs") or {}).get("goals")]
        publish = [i for i, s in enumerate(steps) if (s.get("inputs") or {}).get("ArtifactName")]
        copies = [s for s in steps if "TargetFolder" in (s.get("inputs") or {})]
        if (len(gates) != 1 or not maven or not publish or not max(maven) < gates[0][0] < min(publish)
                or gates[0][1].get("continueOnError")
                or (gates[0][1].get("inputs") or {}).get("script", "").replace("\r\n", "\n") != expected_script):
            raise RuntimeError("主包流水线缺少有效的依赖模块发布门禁，未开始构建")
        if not copies or any(str(s["inputs"].get("CleanTargetFolder", "false")).lower() != "true"
                             or str(s["inputs"].get("OverWrite", "false")).lower() != "true" for s in copies):
            raise RuntimeError("主包暂存目录没有清理或覆盖保护，不能排除旧包混入")
        critical = [s for s in steps if "TargetFolder" in (s.get("inputs") or {})
                    or "ArtifactName" in (s.get("inputs") or {}) or "sshEndpoint" in (s.get("inputs") or {})]
        if any(s.get("alwaysRun") or s.get("continueOnError") or s.get("condition") != "succeeded()" for s in critical):
            raise RuntimeError("复制、加密或发布步骤仍允许失败后继续，不能保证交付完整")

    def read_log(self, client, build_id: int) -> list[str]:
        timeline = client.request_json("GET", client.api_url(f"builds/{build_id}/timeline"))
        output = []
        for record in timeline.get("records", []):
            if "maven" not in record.get("name", "").lower() or not (record.get("log") or {}).get("id"):
                continue
            doc = client.request_json("GET", client.api_url(f"builds/{build_id}/logs/{record['log']['id']}"))
            output.extend(doc.get("value", []) if isinstance(doc, dict) else doc)
        return output

    def read_public(self, url: str) -> bytes:
        allowed = urlsplit(self.contract["artifactUrlPrefix"])
        parsed = urlsplit(url)
        if (parsed.scheme != allowed.scheme or parsed.netloc != allowed.netloc
                or not parsed.path.startswith(allowed.path) or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise RuntimeError("模块发布地址不属于配置的 Maven 仓库")
        with urlopen(url, timeout=45) as response:
            data = response.read(100 * 1024 * 1024 + 1)
        if len(data) > 100 * 1024 * 1024:
            raise RuntimeError("模块产物超过校验大小限制")
        return data

    def receipt(self, client, plan: dict, build: dict, *, require_current: bool = True) -> dict:
        if build.get("status") != "completed" or build.get("result") != "succeeded" or not build_matches(build, plan):
            raise RuntimeError("依赖模块构建不属于冻结的目标提交")
        repo = plan["repository"]
        uploads = []
        for line in self.read_log(client, int(build["id"])):
            if "Uploaded to" in line and repo + "-" in line:
                uploads.extend(re.findall(r"https?://[^\s]+/" + re.escape(repo) + r"-[^/\s]+\.jar(?=\s|$)", line))
        if not uploads:
            raise RuntimeError(f"{repo} 缺少成功发布 Maven JAR 的回执")
        url = uploads[-1]
        if require_current:
            metadata = ET.fromstring(self.read_public(url.rsplit("/", 1)[0] + "/maven-metadata.xml"))
            current = [n.findtext("value") for n in metadata.findall("./versioning/snapshotVersions/snapshotVersion")
                       if n.findtext("extension") == "jar" and not n.findtext("classifier")]
            if len(current) != 1 or url.rsplit("/", 1)[-1] != repo + "-" + current[0] + ".jar":
                raise RuntimeError(f"{repo} 的 Maven 当前版本不再对应该构建，不能复用")
        raw = self.read_public(url)
        sha = hashlib.sha256(raw).hexdigest()
        proof = {"repository": repo, "definitionId": plan["definitionId"], "buildId": int(build["id"]),
                 "sourceVersion": plan["sourceVersion"], "moduleSha256": sha, "manifest": module_manifest(raw)}
        (self.cache / (sha + ".json")).write_text(json.dumps(proof, ensure_ascii=False), encoding="utf-8")
        (self.cache / (sha + ".jar")).write_bytes(raw)
        self.proofs[plan["definitionId"]] = proof
        return proof

    def reuse_dependency(self, client, plan: dict) -> dict | None:
        for candidate in client.recent_builds(plan["definitionId"]):
            if candidate.get("status") == "completed" and candidate.get("result") == "succeeded" and build_matches(candidate, plan):
                try:
                    full = client.get_build(int(candidate["id"]))
                    self.receipt(client, plan, full)
                    return full
                except (RuntimeError, ValueError, OSError, KeyError):
                    # Missing historical logs or replaced SNAPSHOTs are not proof of publication.
                    continue
        return None

    def delivery_parameters(self) -> dict:
        deps = [p for p in self.plans if p["purpose"] == "dependency"]
        if len(self.proofs) != len(deps):
            raise RuntimeError("依赖模块尚未全部发布，不能构建主包")
        manifest = {"version": 1, "modules": {p["repository"]: {
            "sha256": self.proofs[p["definitionId"]]["moduleSha256"], "buildId": self.proofs[p["definitionId"]]["buildId"],
            "definitionId": p["definitionId"], "sourceVersion": p["sourceVersion"]} for p in deps}}
        raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        return {PARAMETER: base64.b64encode(raw).decode("ascii")}

    def restore(self, client, plan: dict, build: dict) -> None:
        encoded = normalized_parameters(build).get(PARAMETER, "")
        if not encoded:
            raise RuntimeError("该历史主包没有依赖校验信息，不能自动部署；请运行完整发版流程")
        manifest = json.loads(base64.b64decode(encoded, validate=True))
        deps = {p["repository"]: p for p in self.plans if p["purpose"] == "dependency"}
        if manifest.get("version") != 1 or set(manifest.get("modules") or {}) != set(deps):
            raise RuntimeError("历史主包的依赖校验范围与项目配置不一致")
        for name, item in manifest["modules"].items():
            dep = deps[name]
            if item["definitionId"] != dep["definitionId"] or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]):
                raise RuntimeError("历史主包的依赖来源不匹配")
            dep["sourceVersion"] = item["sourceVersion"]
            cached = self.cache / (item["sha256"] + ".json")
            binary = cached.with_suffix(".jar")
            proof = json.loads(cached.read_text("utf-8")) if cached.exists() and binary.exists() else self.receipt(
                self.clients[dep["tfsProject"]], dep,
                self.clients[dep["tfsProject"]].get_build(int(item["buildId"])), require_current=False)
            if any(proof[key] != value for key, value in {
                "repository": name, "definitionId": item["definitionId"], "buildId": item["buildId"],
                "sourceVersion": item["sourceVersion"], "moduleSha256": item["sha256"]}.items()):
                raise RuntimeError("历史主包的依赖校验回执不一致")
            raw = binary.read_bytes()
            if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise RuntimeError("依赖校验缓存损坏，未部署历史产物")
            proof["manifest"] = module_manifest(raw)
            self.proofs[dep["definitionId"]] = proof
        plan["parameters"], plan["sourceVersion"] = {PARAMETER: encoded}, build["sourceVersion"]

    def verify_gate_execution(self, client, plan: dict, build: dict) -> None:
        if not build_matches(build, plan):
            raise RuntimeError("主包构建的提交或依赖校验参数与冻结计划不一致")
        timeline = client.request_json("GET", client.api_url(f"builds/{build['id']}/timeline"))
        gates = [r for r in timeline.get("records", []) if r.get("name") == GATE_NAME]
        if len(gates) != 1 or gates[0].get("result") != "succeeded":
            raise RuntimeError("主包未通过服务器端依赖校验，不能部署或交付")
        doc = client.request_json("GET", client.api_url(f"builds/{build['id']}/logs/{gates[0]['log']['id']}"))
        lines = doc.get("value", []) if isinstance(doc, dict) else doc
        digest = hashlib.sha256(base64.b64decode(plan["parameters"][PARAMETER])).hexdigest()
        if not any("AUTODEV_MODULES_VERIFIED:" + digest in line for line in lines):
            raise RuntimeError("主包缺少本轮依赖校验回执")

    def verify_delivery(self, client, plan: dict, build: dict, artifact_cache: Path) -> dict:
        self.verify_gate_execution(client, plan, build)
        # Reuse the official authenticated downloader and existing safe cache.
        from netorder_14 import download_artifact
        folder = download_artifact(client, build, plan["expectedArtifact"], artifact_cache)
        packages = list(folder.glob("dcsd-springboot-*.jar"))
        if len(packages) != 1:
            raise RuntimeError("正式产物缺少唯一业务主包")
        return {"status": "verified", "version": 1, "preEncryptionModulesVerified": True,
                "postEncryptionResourcesVerified": True,
                "modules": verify_package(packages[0], list(self.proofs.values()))}
