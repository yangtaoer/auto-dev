from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from urllib.parse import quote, unquote

import httpx

from ..config import settings
from .process_env import sanitized_process_env


DELIVERY_ARTIFACTS_FIELD = "Custom.0bb5cb34-6fb8-4a13-9508-166612ddb2b8"
ACTUAL_DELIVERY_VERSION_FIELD = "Custom.adf4ae15-611e-4565-9f47-a9f24561efa9"


@dataclass(slots=True)
class RepositoryInfo:
    project: str
    name: str
    id: str
    project_id: str


class TfsError(RuntimeError):
    pass


class TfsClient:
    def __init__(self, base_url: str, pat: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.pat = pat if pat is not None else settings.tfs_pat

    def _client(self, pat: str | None = None) -> httpx.Client:
        token = self.pat if pat is None else pat
        if not token:
            raise TfsError("未配置 TFS_PAT")
        return httpx.Client(auth=httpx.BasicAuth("", token), timeout=30, trust_env=False)

    def _request(self, method: str, url: str, *, pat: str | None = None, **kwargs):
        with self._client(pat) as client:
            response = client.request(method, url, **kwargs)
        if response.is_error:
            detail = response.text[:1000]
            raise TfsError(f"TFS {method} {url} 返回 {response.status_code}: {detail}")
        return response.json()

    def get_work_item(self, work_item_id: int) -> dict:
        url = f"{self.base_url}/_apis/wit/workitems/{work_item_id}?$expand=relations&api-version=2.0"
        item = self._request("GET", url)
        fields = item.get("fields", {})
        return {
            "id": item.get("id"),
            "revision": item.get("rev"),
            "title": fields.get("System.Title", ""),
            "state": fields.get("System.State", ""),
            "work_item_type": fields.get("System.WorkItemType", ""),
            "area_path": fields.get("System.AreaPath", ""),
            "iteration_path": fields.get("System.IterationPath", ""),
            "description": fields.get("System.Description", ""),
            "acceptance_criteria": fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", ""),
            "relations": item.get("relations", []),
        }

    def update_delivery_artifacts(self, work_item_id: int, html_value: str) -> None:
        patch = [{"op": "add", "path": f"/fields/{DELIVERY_ARTIFACTS_FIELD}", "value": html_value}]
        self._request(
            "PATCH",
            f"{self.base_url}/_apis/wit/workitems/{work_item_id}?api-version=2.0",
            json=patch,
            headers={"Content-Type": "application/json-patch+json"},
        )

    def complete_delivery(
        self,
        work_item_id: int,
        html_value: str,
        *,
        actual_version: str = "V1.0",
        resolved_state: str = "已解决",
    ) -> dict:
        """Close the delivery loop on TFS with one atomic work-item update."""
        work_item = self.get_work_item(work_item_id)
        previous_state = str(work_item.get("state") or "")
        patch = [
            {"op": "add", "path": f"/fields/{DELIVERY_ARTIFACTS_FIELD}", "value": html_value},
            {"op": "add", "path": f"/fields/{ACTUAL_DELIVERY_VERSION_FIELD}", "value": actual_version},
        ]
        if previous_state != resolved_state:
            patch.append(
                {"op": "replace", "path": "/fields/System.State", "value": resolved_state}
            )
        updated = self._request(
            "PATCH",
            f"{self.base_url}/_apis/wit/workitems/{work_item_id}?api-version=2.0",
            json=patch,
            headers={"Content-Type": "application/json-patch+json"},
        )
        fields = updated.get("fields", {})
        return {
            "previous_state": previous_state,
            "state": fields.get("System.State", resolved_state),
            "actual_version": fields.get(ACTUAL_DELIVERY_VERSION_FIELD, actual_version),
        }

    @staticmethod
    def parse_origin(repo_path: str) -> tuple[str, str]:
        result = subprocess.run(
            ["git", "-C", repo_path, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=sanitized_process_env(),
            check=False,
        )
        if result.returncode:
            raise TfsError(f"无法读取仓库 origin: {result.stderr.strip()}")
        origin = result.stdout.strip().removesuffix(".git")
        match = re.search(r"/DefaultCollection/([^/]+)/_git/([^/]+)$", origin, re.IGNORECASE)
        if not match:
            raise TfsError(f"仓库不是可识别的 TFS Git 地址: {origin}")
        return unquote(match.group(1)), unquote(match.group(2))

    def repository_info(self, repo_path: str) -> RepositoryInfo:
        project, repo_name = self.parse_origin(repo_path)
        url = f"{self.base_url}/{quote(project, safe='')}/_apis/git/repositories?api-version=2.0"
        data = self._request("GET", url)
        for repo in data.get("value", []):
            if repo.get("name", "").casefold() == repo_name.casefold():
                return RepositoryInfo(
                    project=project,
                    name=repo_name,
                    id=repo["id"],
                    project_id=repo["project"]["id"],
                )
        raise TfsError(f"TFS 项目 {project} 中未找到仓库 {repo_name}")

    def create_pull_request(
        self,
        repo_path: str,
        source_branch: str,
        target_branch: str,
        title: str,
        work_item_id: int,
        description: str,
    ) -> dict:
        repo = self.repository_info(repo_path)
        project_path = quote(repo.project, safe="")
        source_ref = f"refs/heads/{source_branch}"
        target_ref = f"refs/heads/{target_branch}"
        collection = f"{self.base_url}/{project_path}/_apis/git/repositories/{repo.id}/pullRequests"
        active = self._request(
            "GET",
            collection,
            params={
                "searchCriteria.status": "active",
                "searchCriteria.sourceRefName": source_ref,
                "searchCriteria.targetRefName": target_ref,
                "api-version": "2.0",
            },
        ).get("value", [])
        if active:
            pr = self._request(
                "PATCH",
                f"{collection}/{active[0]['pullRequestId']}?api-version=2.0",
                json={"title": title, "description": description},
            )
        else:
            pr = self._request(
                "POST",
                f"{collection}?api-version=2.0",
                json={
                    "sourceRefName": source_ref,
                    "targetRefName": target_ref,
                    "title": title,
                    "description": description,
                },
            )

        creator_id = (pr.get("createdBy") or {}).get("id")
        if not creator_id:
            raise TfsError("PR 响应缺少 createdBy.id，无法启用自动完成")
        pr = self._request(
            "PATCH",
            f"{collection}/{pr['pullRequestId']}?api-version=2.0",
            json={
                "autoCompleteSetBy": {"id": creator_id},
                "completionOptions": {"deleteSourceBranch": True},
            },
        )
        if not pr.get("autoCompleteSetBy"):
            raise TfsError("TFS 未确认启用 PR 自动完成")

        linked = self._ensure_work_item_link(repo, int(pr["pullRequestId"]), work_item_id)
        web_url = (
            f"{self.base_url}/{quote(repo.project, safe='')}/_git/"
            f"{quote(repo.name, safe='')}/pullrequest/{pr['pullRequestId']}"
        )
        return {
            "PullRequestId": pr["pullRequestId"],
            "Status": pr.get("status"),
            "Title": pr.get("title"),
            "SourceBranch": source_branch,
            "TargetBranch": target_branch,
            "Project": repo.project,
            "Repository": repo.name,
            "WorkItemId": work_item_id,
            "WorkItemLinked": linked,
            "AutoCompleteEnabled": True,
            "DeleteSourceBranch": True,
            "WebUrl": web_url,
            "ApiUrl": pr.get("url"),
        }

    def _ensure_work_item_link(self, repo: RepositoryInfo, pr_id: int, work_item_id: int) -> bool:
        project_path = quote(repo.project, safe="")
        pr_work_items_url = (
            f"{self.base_url}/{project_path}/_apis/git/repositories/{repo.id}"
            f"/pullRequests/{pr_id}/workitems?api-version=2.0"
        )
        existing = self._request("GET", pr_work_items_url).get("value", [])
        if any(str(item.get("id")) == str(work_item_id) for item in existing):
            return True

        artifact_url = f"vstfs:///Git/PullRequestId/{repo.project_id}%2F{repo.id}%2F{pr_id}"
        work_item_url = f"{self.base_url}/_apis/wit/workitems/{work_item_id}?$expand=relations&api-version=2.0"
        work_item = self._request("GET", work_item_url)
        if any(relation.get("url") == artifact_url for relation in work_item.get("relations", [])):
            return True
        patch = [
            {
                "op": "add",
                "path": "/relations/-",
                "value": {
                    "rel": "ArtifactLink",
                    "url": artifact_url,
                    "attributes": {"name": "Pull Request"},
                },
            }
        ]
        self._request(
            "PATCH",
            f"{self.base_url}/_apis/wit/workitems/{work_item_id}?api-version=2.0",
            json=patch,
            headers={"Content-Type": "application/json-patch+json"},
        )
        return True

    def approve_pull_request(self, repo_path: str, pr_id: int) -> None:
        if not settings.tfs_reviewer_pat or not settings.tfs_reviewer_id:
            raise TfsError("四川自动审核需要配置 TFS_REVIEWER_PAT 和 TFS_REVIEWER_ID")
        repo = self.repository_info(repo_path)
        url = (
            f"{self.base_url}/{quote(repo.project, safe='')}/_apis/git/repositories/{repo.id}"
            f"/pullRequests/{pr_id}/reviewers/{settings.tfs_reviewer_id}?api-version=2.0"
        )
        self._request("PUT", url, pat=settings.tfs_reviewer_pat, json={"vote": 10})

    def get_pull_request(self, repo_path: str, pr_id: int) -> dict:
        repo = self.repository_info(repo_path)
        url = f"{self.base_url}/{quote(repo.project, safe='')}/_apis/git/repositories/{repo.id}/pullRequests/{pr_id}?api-version=2.0"
        data = self._request("GET", url)
        return {
            "id": data.get("pullRequestId"),
            "status": data.get("status"),
            "title": data.get("title", ""),
            "repository": data.get("repository", {}).get("name", repo.name),
            "created_by": data.get("createdBy", {}).get("displayName", ""),
            "creation_date": data.get("creationDate"),
            "closed_date": data.get("closedDate"),
            "merge_commit": (data.get("lastMergeCommit") or {}).get("commitId"),
            "source_branch": data.get("sourceRefName", "").removeprefix("refs/heads/"),
            "target_branch": data.get("targetRefName", "").removeprefix("refs/heads/"),
        }
