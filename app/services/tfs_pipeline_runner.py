#!/usr/bin/env python3
"""Versioned TFS workflow runner with source-pinned dependency publication."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

sys.path.append(str(Path.home() / ".codex/skills/tfs-run-pipeline/scripts"))
try:
    from .tfs_release_integrity import ReleaseIntegrity, apply_workflows, build_matches
except ImportError:
    from tfs_release_integrity import ReleaseIntegrity, apply_workflows, build_matches


DEFAULT_BASE_URL = "http://dev.tellhowsoft.com/DefaultCollection"
ACTIVE_STATUSES = {"notStarted", "inProgress", "postponed"}


def default_catalog_path() -> Path:
    return Path.home() / ".codex/skills/tfs-project-catalog/references/project-catalog.seed.json"


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return apply_workflows(payload) if path.resolve() == default_catalog_path().resolve() else payload


def resolve_project(catalog: dict[str, Any], value: str) -> dict[str, Any]:
    requested = value.strip()
    exact = [
        project
        for project in catalog.get("projects", [])
        if str(project.get("standardName", "")).strip() == requested
    ]
    if len(exact) == 1:
        return exact[0]

    aliases = [
        project
        for project in catalog.get("projects", [])
        if requested in [str(alias).strip() for alias in project.get("aliases", [])]
    ]
    if len(aliases) == 1:
        return aliases[0]
    if len(aliases) > 1:
        names = ", ".join(str(project.get("standardName")) for project in aliases)
        raise ValueError(f"alias maps to multiple projects: {requested} -> {names}")
    raise ValueError(f"project is not present in the catalog: {requested}")


def pipeline_stage(pipeline: dict[str, Any]) -> int:
    return int(pipeline.get("stage") or 1)


def resolve_pipelines(
    project: dict[str, Any], definition_id: int | None
) -> list[dict[str, Any]]:
    pipelines = list(project.get("pipelines", []) or [])
    if definition_id is not None:
        matches = [p for p in pipelines if int(p.get("definitionId", 0)) == definition_id]
    else:
        staged = [p for p in pipelines if p.get("stage") is not None]
        if staged:
            matches = [
                p for p in pipelines if p.get("purpose") in {"dependency", "delivery"}
            ]
        else:
            matches = [p for p in pipelines if p.get("purpose") == "delivery"]
    standard_name = project.get("standardName", "<unknown>")
    if not matches:
        raise ValueError(f"no matching pipeline is cataloged for {standard_name}")
    if definition_id is not None and len(matches) != 1:
        raise ValueError(
            f"definition {definition_id} is not unique in the catalog for {standard_name}"
        )
    return sorted(matches, key=lambda p: (pipeline_stage(p), int(p["definitionId"])))


class TfsBuildClient:
    def __init__(self, base_url: str, tfs_project: str, pat: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.tfs_project = tfs_project
        encoded = base64.b64encode(f":{pat}".encode("utf-8")).decode("ascii")
        self.headers = {"Authorization": f"Basic {encoded}"}

    def api_url(self, suffix: str, **query: Any) -> str:
        params = {key: value for key, value in query.items() if value is not None}
        params.setdefault("api-version", "4.1")
        query_string = urlencode(params)
        project = quote(self.tfs_project, safe="")
        return f"{self.base_url}/{project}/_apis/build/{suffix}?{query_string}"

    def request_json(
        self, method: str, url: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        headers = dict(self.headers)
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8", errors="replace")
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    if "/logs/" in url:
                        return {"value": raw.splitlines()}
                    raise
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"TFS returned HTTP {error.code}: {body[:500]}") from error
        except URLError as error:
            raise RuntimeError(f"cannot reach TFS: {error.reason}") from error

    def get_definition(self, definition_id: int) -> dict[str, Any]:
        return self.request_json("GET", self.api_url(f"definitions/{definition_id}"))

    def recent_builds(self, definition_id: int) -> list[dict[str, Any]]:
        response = self.request_json(
            "GET",
            self.api_url(
                "builds",
                definitions=definition_id,
                **{"$top": 50},
                queryOrder="queueTimeDescending",
            ),
        )
        return list(response.get("value", []) or [])

    def queue_build(self, definition_id: int, source_branch: str, *, source_version: str = "", parameters: dict | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"definition": {"id": definition_id}}
        if source_branch:
            payload["sourceBranch"] = source_branch
        if source_version:
            payload["sourceVersion"] = source_version
        if parameters:
            payload["parameters"] = json.dumps(parameters)
        return self.request_json("POST", self.api_url("builds"), payload)

    def get_build(self, build_id: int) -> dict[str, Any]:
        return self.request_json("GET", self.api_url(f"builds/{build_id}"))

    def get_artifacts(self, build_id: int) -> list[dict[str, Any]]:
        response = self.request_json("GET", self.api_url(f"builds/{build_id}/artifacts"))
        return list(response.get("value", []) or [])

    def latest_successful(self, definition_id: int, branch: str) -> dict[str, Any]:
        response = self.request_json("GET", self.api_url("builds", definitions=definition_id,
            branchName=branch, statusFilter="completed", resultFilter="succeeded",
            queryOrder="finishTimeDescending", **{"$top": 1}))
        builds = response.get("value", []) or []
        if not builds:
            raise RuntimeError(f"no successful build for definition {definition_id} on {branch}")
        return builds[0]


def normalize_branch(value: str) -> str:
    branch = value.strip()
    if branch and not branch.startswith("refs/heads/"):
        return f"refs/heads/{branch}"
    return branch


def pipeline_plan(
    project: dict[str, Any], pipeline: dict[str, Any], base_url: str, source_branch: str
) -> dict[str, Any]:
    tfs_project = str(pipeline.get("tfsProject") or "DCS")
    definition_id = int(pipeline["definitionId"])
    definition_url = pipeline.get("definitionUrl") or (
        f"{base_url.rstrip('/')}/{quote(tfs_project, safe='')}/_build?definitionId={definition_id}"
    )
    return {
        "standardName": project.get("standardName"),
        "purpose": pipeline.get("purpose"),
        "stage": pipeline_stage(pipeline),
        "deliverable": pipeline.get("deliverable"),
        "pipelineName": pipeline.get("name"),
        "definitionId": definition_id,
        "definitionUrl": definition_url,
        "folderPath": pipeline.get("folderPath"),
        "tfsProject": tfs_project,
        "repository": pipeline.get("repository"),
        "sourceBranch": source_branch,
        "buildProfile": pipeline.get("buildProfile"),
        "expectedArtifact": pipeline.get("artifactName"),
        "deploymentAudit": pipeline.get("deploymentAudit"),
        "deploymentMonitoring": "not-included-in-build-monitoring",
    }


def live_check(
    client: TfsBuildClient, plan: dict[str, Any], force_new: bool
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    definition = client.get_definition(int(plan["definitionId"]))
    live_name = str(definition.get("name", ""))
    expected_name = str(plan.get("pipelineName") or "")
    if expected_name and live_name != expected_name:
        raise RuntimeError(
            f"catalog definition name mismatch: expected {expected_name!r}, got {live_name!r}"
        )
    if definition.get("queueStatus") not in (None, "enabled"):
        raise RuntimeError(f"pipeline is not enabled: {definition.get('queueStatus')}")

    expected_repository = str(plan.get("repository") or "")
    live_repository = str((definition.get("repository") or {}).get("name") or "")
    if expected_repository and live_repository != expected_repository:
        raise RuntimeError(
            "catalog repository mismatch: "
            f"expected {expected_repository!r}, got {live_repository!r}"
        )

    definition_steps = list(definition.get("build", []) or [])
    for phase in (definition.get("process") or {}).get("phases", []) or []:
        definition_steps.extend(phase.get("steps", []) or [])
    enabled_steps = [step for step in definition_steps if step.get("enabled")]
    maven_goals = [
        str((step.get("inputs") or {}).get("goals") or "")
        for step in enabled_steps
        if (step.get("inputs") or {}).get("goals")
    ]
    expected_profile = str(plan.get("buildProfile") or "")
    if expected_profile and not any(
        f"-P {expected_profile}" in goals or f"-P{expected_profile}" in goals
        for goals in maven_goals
    ):
        raise RuntimeError(
            f"catalog build profile {expected_profile!r} is not present in live Maven goals"
        )
    published_artifacts = [
        str((step.get("inputs") or {}).get("ArtifactName") or "")
        for step in enabled_steps
        if (step.get("inputs") or {}).get("ArtifactName")
    ]
    expected_artifact = str(plan.get("expectedArtifact") or "")
    if (
        expected_artifact
        and published_artifacts
        and expected_artifact not in published_artifacts
    ):
        raise RuntimeError(
            f"catalog artifact {expected_artifact!r} is not published by the live definition"
        )

    active = [
        build
        for build in client.recent_builds(int(plan["definitionId"]))
        if build.get("status") in ACTIVE_STATUSES
        and normalize_branch(str(build.get("sourceBranch") or "")) == plan["sourceBranch"]
    ]
    active_build = None if force_new or not active else active[0]
    check = {
        "liveName": live_name,
        "queueStatus": definition.get("queueStatus"),
        "liveRepository": live_repository,
        "liveDefaultBranch": (definition.get("repository") or {}).get("defaultBranch"),
        "liveMavenGoals": maven_goals,
        "livePublishedArtifacts": published_artifacts,
        "artifactPrecheck": (
            "verified"
            if not expected_artifact or expected_artifact in published_artifacts
            else "deferred-to-build-result"
        ),
        "activeBuildIds": [build.get("id") for build in active],
    }
    return check, active_build


def build_links(base_url: str, tfs_project: str, build_id: int) -> dict[str, str]:
    project = quote(tfs_project, safe="")
    root = f"{base_url.rstrip('/')}/{project}/_build/results?buildId={build_id}"
    return {
        "resultsUrl": f"{root}&view=results",
        "artifactsUrl": (
            f"{root}&view=artifacts&pathAsName=false&type=publishedArtifacts"
        ),
    }


def wait_for_build(
    client: TfsBuildClient, build_id: int, poll_seconds: int, timeout_seconds: int
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    previous = None
    while True:
        build = client.get_build(build_id)
        state = (build.get("status"), build.get("result"))
        if state != previous:
            print(
                f"build {build_id}: status={state[0]} result={state[1]}",
                file=sys.stderr,
                flush=True,
            )
            previous = state
        if build.get("status") == "completed":
            return build
        if time.monotonic() >= deadline:
            raise TimeoutError(f"timed out waiting for build {build_id}")
        time.sleep(poll_seconds)


def workflow_payload(plans: list[dict[str, Any]]) -> dict[str, Any]:
    stages: list[dict[str, Any]] = []
    for stage_number in sorted({int(plan["stage"]) for plan in plans}):
        stages.append(
            {
                "stage": stage_number,
                "waitForAll": True,
                "pipelines": [
                    plan for plan in plans if int(plan["stage"]) == stage_number
                ],
            }
        )
    return {"stages": stages}


def completed_build_result(
    plan: dict[str, Any],
    base_url: str,
    queue_action: str,
    completed: dict[str, Any],
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    build_id = int(completed["id"])
    artifact_names = [str(artifact.get("name")) for artifact in artifacts]
    expected = str(plan.get("expectedArtifact") or "")
    expected_found = not expected or expected in artifact_names
    artifact_required = plan.get("purpose") == "delivery"
    artifact_valid = expected_found and (bool(artifacts) or not artifact_required)
    return {
        "action": queue_action,
        **plan,
        "buildId": build_id,
        "buildNumber": completed.get("buildNumber"),
        "status": completed.get("status"),
        "result": completed.get("result"),
        **build_links(base_url, str(plan["tfsProject"]), build_id),
        "artifacts": artifact_names,
        "expectedArtifactFound": expected_found,
        "artifactValid": artifact_valid,
    }


def build_result_exit_code(result: dict[str, Any]) -> int:
    if result.get("result") != "succeeded":
        return 3
    if not result.get("artifactValid"):
        return 4
    if result.get("releaseIntegrityRequired") and result.get("releaseIntegrity", {}).get("status") != "verified":
        return 6
    deployment = result.get("deployment14", {})
    if deployment.get("eligible") and deployment.get("status") not in {"deployed", "already-current"}:
        return 5
    return 0


def attach_deployment14(result, project, plan, client, completed, args):
    """Only the authorized release path mutates server 14; observation stays read-only."""
    if not (args.confirm_run or args.deploy_14) or args.skip_deploy_14:
        if args.skip_deploy_14:
            result["deployment14"] = dict(status="skipped-by-user", eligible=False, applicationUrl=None)
        return
    if build_result_exit_code(result) != 0:
        return
    try:
        from netorder_14 import deploy_completed
        # Plan fields use expectedArtifact; preserve the catalog's artifact contract.
        pipeline = dict(plan, artifactName=plan.get("expectedArtifact"))
        result["deployment14"] = deploy_completed(client, project, pipeline, completed,
            cache_dir=Path(args.artifact_cache), force=args.force_redeploy_14)
        result["deploymentMonitoring"] = "server14-reported-separately; company-release-not-monitored"
    except Exception as error:
        result["deployment14"] = dict(status="failed", eligible=True, reason=str(error), applicationUrl=None)


def guarded_build(client, plan, active, integrity, args):
    """Reuse only builds for the pinned source and (for main packages) exact proof."""
    if plan["purpose"] == "dependency" and not args.force_new:
        reused = integrity.reuse_dependency(client, plan)
        if reused:
            print(f"reusing published module {plan['repository']} build {reused['id']}", file=sys.stderr, flush=True)
            return reused, "reuse-verified-dependency"
    if active:
        full = client.get_build(int(active["id"]))
        if not build_matches(full, plan):
            wait_for_build(client, int(full["id"]), args.poll_seconds, args.timeout_seconds)
            active = None
        else:
            return full, "wait-existing"
    if plan["purpose"] == "delivery" and not args.force_new:
        for candidate in client.recent_builds(plan["definitionId"]):
            if candidate.get("status") == "completed" and candidate.get("result") == "succeeded":
                full = client.get_build(int(candidate["id"]))
                if build_matches(full, plan):
                    return full, "reuse-verified-inputs"
    build = client.queue_build(plan["definitionId"], plan["sourceBranch"],
                               source_version=plan["sourceVersion"], parameters=plan.get("parameters"))
    print(f"queued source-pinned build {build['id']} for {plan['repository']}", file=sys.stderr, flush=True)
    return build, "queued"


def emit_result(payload, args):
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output_json:
        output = Path(args.output_json).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def queue_or_reuse_build(
    client: TfsBuildClient,
    plan: dict[str, Any],
    active_build: dict[str, Any] | None,
) -> tuple[dict[str, Any], str]:
    if active_build is not None:
        print(f"using active build {active_build.get('id')}", file=sys.stderr, flush=True)
        return active_build, "wait-existing"
    build = client.queue_build(int(plan["definitionId"]), str(plan["sourceBranch"]))
    print(f"queued build {build.get('id')}", file=sys.stderr, flush=True)
    return build, "queued"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-name", help="Standard project name or exact alias")
    parser.add_argument("--catalog", default=str(default_catalog_path()), help="Project catalog JSON")
    parser.add_argument(
        "--list-projects",
        action="store_true",
        help="List cataloged pipeline workflows without contacting TFS",
    )
    parser.add_argument("--definition-id", type=int, help="Select one cataloged definition explicitly")
    parser.add_argument("--base-url", default=os.environ.get("TFS_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument(
        "--source-branch",
        default="",
        help="Optional branch override for a single selected definition",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--check-definition", action="store_true", help="Read-only live workflow validation"
    )
    action.add_argument(
        "--confirm-run", action="store_true", help="Authorize one complete workflow run"
    )
    action.add_argument("--build-id", type=int, help="Monitor an already queued build")
    action.add_argument("--latest-successful", action="store_true", help="Use the latest completed successful build; do not queue a new build")
    parser.add_argument("--force-new", action="store_true", help="Queue even when an active build exists")
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    deploy = parser.add_mutually_exclusive_group()
    deploy.add_argument("--deploy-14", action="store_true", help="Deploy an existing/latest completed delivery to server 14; normal --confirm-run releases do this automatically")
    deploy.add_argument("--skip-deploy-14", action="store_true", help="Packaging-only override when the user explicitly excludes local deployment")
    parser.add_argument("--force-redeploy-14", action="store_true", help="Explicitly recreate even when the same verified package is already current")
    parser.add_argument("--artifact-cache", default=str(Path.home()/".codex/artifacts/netorder14"))
    parser.add_argument("--output-json", help="Write a credential-free build/deployment report")
    parser.add_argument("--required-commits", help="Internal JSON context containing required repository commits")
    args = parser.parse_args()

    try:
        catalog = load_json(Path(args.catalog))
        if args.list_projects:
            if any(
                (
                    args.project_name,
                    args.definition_id,
                    args.source_branch,
                    args.check_definition,
                    args.confirm_run,
                    args.build_id,
                    args.latest_successful,
                    args.force_new,
                    args.deploy_14,
                    args.skip_deploy_14,
                    args.force_redeploy_14,
                )
            ):
                raise ValueError("--list-projects cannot be combined with run options")
            pipelines: list[dict[str, Any]] = []
            for catalog_project in catalog.get("projects", []) or []:
                catalog_pipelines = list(catalog_project.get("pipelines", []) or [])
                has_stages = any(p.get("stage") is not None for p in catalog_pipelines)
                for catalog_pipeline in catalog_pipelines:
                    purpose = catalog_pipeline.get("purpose")
                    if purpose != "delivery" and not (has_stages and purpose == "dependency"):
                        continue
                    pipelines.append(
                        {
                            "standardName": catalog_project.get("standardName"),
                            "purpose": purpose,
                            "stage": pipeline_stage(catalog_pipeline),
                            "deliverable": catalog_pipeline.get("deliverable"),
                            "definitionId": catalog_pipeline.get("definitionId"),
                            "pipelineName": catalog_pipeline.get("name"),
                            "definitionUrl": catalog_pipeline.get("definitionUrl"),
                            "sourceBranch": catalog_pipeline.get("sourceBranch"),
                            "buildProfile": catalog_pipeline.get("buildProfile"),
                            "expectedArtifact": catalog_pipeline.get("artifactName"),
                            "deploymentAudit": catalog_pipeline.get("deploymentAudit"),
                        }
                    )
            pipelines.sort(
                key=lambda item: (
                    str(item.get("standardName") or ""),
                    int(item.get("stage") or 1),
                    int(item.get("definitionId") or 0),
                )
            )
            emit_result({"projects": pipelines}, args)
            return 0
        if not args.project_name:
            parser.error("--project-name is required unless --list-projects is used")
        project = resolve_project(catalog, args.project_name)
        workflow_pipelines = resolve_pipelines(project, None)
        selected_pipelines = resolve_pipelines(project, args.definition_id)
        if args.confirm_run and args.definition_id is not None and len(workflow_pipelines) > 1:
            raise ValueError(
                "--confirm-run executes the complete staged workflow; "
                "do not combine it with --definition-id"
            )
        if args.build_id and len(selected_pipelines) != 1:
            raise ValueError(
                "--build-id requires --definition-id when the project has multiple pipelines"
            )
        if args.latest_successful and len(selected_pipelines) != 1:
            raise ValueError("--latest-successful requires --definition-id for multi-pipeline projects")
        if args.deploy_14 and not (args.confirm_run or args.build_id or args.latest_successful):
            raise ValueError("--deploy-14 requires a run, existing build, or latest-successful action")
        if args.force_redeploy_14 and (not (args.confirm_run or args.deploy_14) or args.skip_deploy_14):
            raise ValueError("--force-redeploy-14 requires an authorized deployment action")
        if args.skip_deploy_14 and args.check_definition:
            raise ValueError("A read-only definition check does not need deployment overrides")
        if args.source_branch and len(selected_pipelines) != 1:
            raise ValueError(
                "--source-branch requires --definition-id when the project has multiple pipelines"
            )

        plans: list[dict[str, Any]] = []
        for pipeline in selected_pipelines:
            source_branch = normalize_branch(
                args.source_branch or str(pipeline.get("sourceBranch") or "")
            )
            if not source_branch:
                raise ValueError(
                    f"pipeline {pipeline.get('definitionId')} has no source branch"
                )
            plans.append(
                pipeline_plan(project, pipeline, args.base_url, source_branch)
            )

        if args.force_new and not args.confirm_run:
            raise ValueError("--force-new requires --confirm-run")
        if not args.check_definition and not args.confirm_run and not args.build_id and not args.latest_successful:
            if len(plans) == 1:
                payload = {"action": "plan", **plans[0]}
            else:
                payload = {
                    "action": "plan",
                    "standardName": project.get("standardName"),
                    "workflow": workflow_payload(plans),
                }
            payload["releaseIntegrityRequired"] = bool(project.get("releaseIntegrity"))
            emit_result(payload, args)
            return 0

        pat = os.environ.get("TFS_PAT", "")
        if not pat:
            raise RuntimeError("TFS_PAT is required for live pipeline operations")
        clients: dict[str, TfsBuildClient] = {}
        checks: dict[int, dict[str, Any]] = {}
        active_builds: dict[int, dict[str, Any] | None] = {}
        for plan in plans:
            tfs_project = str(plan["tfsProject"])
            client = clients.setdefault(
                tfs_project, TfsBuildClient(args.base_url, tfs_project, pat)
            )
            check, active_build = live_check(client, plan, args.force_new)
            definition_id = int(plan["definitionId"])
            checks[definition_id] = check
            active_builds[definition_id] = active_build

        integrity = None
        if project.get("releaseIntegrity"):
            if args.force_new:
                raise ValueError("受校验的发版不强制重复构建；请等待现有构建或使用已验证产物")
            if args.build_id or args.latest_successful:
                all_plans = [pipeline_plan(project, p, args.base_url, normalize_branch(p["sourceBranch"])) for p in workflow_pipelines]
            else:
                all_plans = plans
            for plan in all_plans:
                clients.setdefault(plan["tfsProject"], TfsBuildClient(args.base_url, plan["tfsProject"], pat))
            required = load_json(Path(args.required_commits)) if args.required_commits else {}
            integrity = ReleaseIntegrity(project, all_plans, clients, Path(args.artifact_cache), required)
            if args.confirm_run or args.check_definition:
                integrity.freeze()
                for plan in plans:
                    integrity.validate_definition(clients[plan["tfsProject"]], plan)

        if args.check_definition:
            checked_plans = [
                {**plan, "live": checks[int(plan["definitionId"])]} for plan in plans
            ]
            if len(checked_plans) == 1:
                payload = {"action": "check", **checked_plans[0]}
            else:
                payload = {
                    "action": "check",
                    "standardName": project.get("standardName"),
                    "workflow": workflow_payload(checked_plans),
                }
            emit_result(payload, args)
            return 0

        if args.build_id or args.latest_successful:
            plan = plans[0]
            client = clients[str(plan["tfsProject"])]
            build = (client.get_build(args.build_id) if args.build_id else
                     client.latest_successful(int(plan["definitionId"]), str(plan["sourceBranch"])))
            live_definition_id = int((build.get("definition") or {}).get("id") or 0)
            if live_definition_id != int(plan["definitionId"]):
                raise RuntimeError(
                    f"build {args.build_id} belongs to definition {live_definition_id}, "
                    f"not {plan['definitionId']}"
                )
            queue_action = "monitor-existing" if args.build_id else "latest-successful"
            print(f"monitoring build {build.get('id')}", file=sys.stderr, flush=True)
            build_id = int(build["id"])
            completed = wait_for_build(
                client, build_id, args.poll_seconds, args.timeout_seconds
            )
            artifacts = client.get_artifacts(build_id)
            result = completed_build_result(
                plan, args.base_url, queue_action, completed, artifacts
            )
            if integrity and args.deploy_14:
                integrity.restore(client, plan, completed)
                result["releaseIntegrity"] = integrity.verify_delivery(client, plan, completed, Path(args.artifact_cache))
                result["releaseIntegrityRequired"] = True
            attach_deployment14(result, project, plan, client, completed, args)
            emit_result(result, args)
            return build_result_exit_code(result)

        stage_outputs: list[dict[str, Any]] = []
        for stage_number in sorted({int(plan["stage"]) for plan in plans}):
            stage_plans = [plan for plan in plans if int(plan["stage"]) == stage_number]
            if integrity:
                for plan in stage_plans:
                    if plan["purpose"] == "delivery":
                        plan["parameters"] = integrity.delivery_parameters()
            queued: list[
                tuple[dict[str, Any], TfsBuildClient, dict[str, Any], str]
            ] = []
            for plan in stage_plans:
                definition_id = int(plan["definitionId"])
                client = clients[str(plan["tfsProject"])]
                if integrity:
                    build, queue_action = guarded_build(client, plan, active_builds[definition_id], integrity, args)
                else:
                    build, queue_action = queue_or_reuse_build(client, plan, active_builds[definition_id])
                queued.append((plan, client, build, queue_action))

            stage_results: list[dict[str, Any]] = []
            for plan, client, build, queue_action in queued:
                build_id = int(build["id"])
                completed = wait_for_build(
                    client, build_id, args.poll_seconds, args.timeout_seconds
                )
                artifacts = client.get_artifacts(build_id)
                result = completed_build_result(plan, args.base_url, queue_action, completed, artifacts)
                if integrity and result["result"] == "succeeded":
                    if plan["purpose"] == "dependency":
                        integrity.receipt(client, plan, completed)
                        result["publicationVerified"] = True
                    else:
                        result["releaseIntegrity"] = integrity.verify_delivery(client, plan, completed, Path(args.artifact_cache))
                        result["releaseIntegrityRequired"] = True
                stage_results.append(result)
            stage_outputs.append({"stage": stage_number, "builds": stage_results})

            exit_codes = [build_result_exit_code(result) for result in stage_results]
            if any(code != 0 for code in exit_codes):
                payload = {
                    "action": "workflow-run",
                    "standardName": project.get("standardName"),
                    "result": "failed",
                    "stages": stage_outputs,
                    "deliveries": [
                        result
                        for stage in stage_outputs
                        for result in stage["builds"]
                        if result.get("purpose") == "delivery"
                    ],
                }
                emit_result(payload, args)
                return 3 if 3 in exit_codes else 4

        deliveries = [
            result
            for stage in stage_outputs
            for result in stage["builds"]
            if result.get("purpose") == "delivery"
        ]
        # Wait until the entire staged build workflow succeeds before any local deployment.
        for result in deliveries:
            plan = next(p for p in plans if p["definitionId"] == result["definitionId"])
            client = clients[str(plan["tfsProject"])]
            completed = client.get_build(int(result["buildId"]))
            attach_deployment14(result, project, plan, client, completed, args)
        deployment_failed = any(build_result_exit_code(result) == 5 for result in deliveries)
        payload = {
            "action": "workflow-run",
            "standardName": project.get("standardName"),
            "result": "failed" if deployment_failed else "succeeded",
            "stages": stage_outputs,
            "deliveries": deliveries,
        }
        emit_result(payload, args)
        return 5 if deployment_failed else 0
    except (ValueError, RuntimeError, TimeoutError, KeyError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
