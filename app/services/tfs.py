from __future__ import annotations

import base64
import html
import mimetypes
import re
import subprocess
from datetime import UTC, datetime
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit

import httpx

from ..config import settings
from .process_env import sanitized_process_env


DELIVERY_ARTIFACTS_FIELD = "Custom.0bb5cb34-6fb8-4a13-9508-166612ddb2b8"
ACTUAL_DELIVERY_VERSION_FIELD = "Custom.adf4ae15-611e-4565-9f47-a9f24561efa9"
LICENSE_PROVINCE_FIELD = "Custom.e10bed6e-1009-4289-9f71-462a10698ab5"
LICENSE_PRODUCT_FIELD = "Custom.e6ec2a4e-66c5-4d4d-85fc-72a4cfb8abaf"
LICENSE_PRODUCT_LINE_FIELD = "Custom.ba3bada3-5db5-489c-8566-6ef7e5790912"
LICENSE_REGION_FIELD = "Custom.4662e0f8-1a77-4dea-babc-04ddef70fb46"
LICENSE_PURPOSE_FIELD = "Custom.979182e5-279b-40ad-92bf-8876c59b5c78"
LICENSE_REQUESTED_AT_FIELD = "Custom.19699ae0-2390-4bf6-9052-47a7649c9481"


@dataclass(slots=True)
class RepositoryInfo:
    project: str
    name: str
    id: str
    project_id: str


class TfsError(RuntimeError):
    pass


class TfsClient:
    REQUIREMENT_IMAGE_LIMIT = 30
    REQUIREMENT_IMAGE_BYTES = 20 * 1024 * 1024

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

    @staticmethod
    def _requirement_image_sources(work_item: dict) -> list[tuple[str, str]]:
        """Collect image references from rich-text fields and AttachedFile relations."""
        sources: list[tuple[str, str]] = []
        seen: set[str] = set()

        def add(source: str, name: str = "") -> None:
            normalized = html.unescape(str(source or "")).strip()
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            resolved_name = str(name or "").strip()
            if not resolved_name:
                resolved_name = unquote(parse_qs(urlsplit(normalized).query).get("fileName", [""])[0])
            sources.append((normalized, resolved_name))

        for field_name in ("description", "acceptance_criteria"):
            value = str(work_item.get(field_name) or "")
            for match in re.finditer(
                r"<img\b[^>]*?\bsrc\s*=\s*(['\"])(.*?)\1",
                value,
                flags=re.IGNORECASE | re.DOTALL,
            ):
                add(match.group(2))

        image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
        for relation in work_item.get("relations") or []:
            if not isinstance(relation, dict) or str(relation.get("rel") or "").casefold() != "attachedfile":
                continue
            attributes = relation.get("attributes") or {}
            name = str(attributes.get("name") or attributes.get("comment") or "").strip()
            url = str(relation.get("url") or "").strip()
            query_name = parse_qs(urlsplit(url).query).get("fileName", [""])[0]
            candidate = name or unquote(query_name)
            if Path(candidate).suffix.casefold() in image_extensions:
                add(url, candidate)
        return sources[: TfsClient.REQUIREMENT_IMAGE_LIMIT]

    @staticmethod
    def _image_extension(content: bytes, content_type: str, suggested_name: str) -> str:
        allowed = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if content.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if content.startswith((b"GIF87a", b"GIF89a")):
            return ".gif"
        if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            return ".webp"
        if content.startswith(b"BM"):
            return ".bmp"
        if content.startswith((b"II*\x00", b"MM\x00*")):
            return ".tiff"
        normalized = content_type.split(";", 1)[0].strip().casefold()
        mapped = mimetypes.guess_extension(normalized) if normalized.startswith("image/") else None
        if mapped == ".jpe":
            return ".jpg"
        if mapped in allowed:
            return ".jpg" if mapped == ".jpeg" else mapped
        suffix = Path(suggested_name).suffix.casefold()
        if normalized.startswith("image/") and suffix in allowed:
            return ".jpg" if suffix == ".jpeg" else suffix
        return ""

    def _download_requirement_image(self, source: str) -> tuple[bytes, str]:
        if source.casefold().startswith("data:image/"):
            header, separator, payload = source.partition(",")
            if not separator or ";base64" not in header.casefold():
                raise TfsError("内嵌需求图片不是受支持的 base64 格式")
            try:
                content = base64.b64decode(payload, validate=True)
            except ValueError as exc:
                raise TfsError("内嵌需求图片 base64 无效") from exc
            if len(content) > self.REQUIREMENT_IMAGE_BYTES:
                raise TfsError("内嵌需求图片超过 20 MB 限制")
            return content, header[5:].split(";", 1)[0]

        url = urljoin(self.base_url + "/", source)
        base_host = urlsplit(self.base_url).hostname
        if urlsplit(url).scheme not in {"http", "https"} or urlsplit(url).hostname != base_host:
            raise TfsError("需求图片地址不属于当前 TFS 服务")
        try:
            with self._client() as client:
                response = client.get(url, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise TfsError(f"TFS 需求图片下载失败：{exc}") from exc
        if response.is_error:
            raise TfsError(f"TFS 需求图片下载返回 {response.status_code}")
        if urlsplit(str(response.url)).hostname != base_host:
            raise TfsError("TFS 需求图片重定向到了非受信地址")
        if len(response.content) > self.REQUIREMENT_IMAGE_BYTES:
            raise TfsError("TFS 需求图片超过 20 MB 限制")
        return response.content, response.headers.get("content-type", "")

    def download_requirement_images(self, work_item: dict, destination: Path) -> dict[str, list[str]]:
        """Download TFS-protected requirement images for local Codex inspection."""
        paths: list[str] = []
        errors: list[str] = []
        destination.mkdir(parents=True, exist_ok=True)
        for index, (source, suggested_name) in enumerate(self._requirement_image_sources(work_item), 1):
            try:
                content, content_type = self._download_requirement_image(source)
                if not content:
                    raise TfsError("图片内容为空")
                extension = self._image_extension(content, content_type, suggested_name)
                if not extension:
                    raise TfsError("附件内容不是受支持的图片格式")
                stem = re.sub(r"[^0-9A-Za-z._-]+", "-", Path(suggested_name).stem).strip("-.")
                target = destination / f"{index:02d}-{stem or 'requirement-image'}{extension}"
                target.write_bytes(content)
                paths.append(str(target.resolve()))
            except (OSError, TfsError) as exc:
                errors.append(f"第 {index} 张需求图片：{exc}")
        return {"paths": paths, "errors": errors}

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

    def create_license_application(
        self,
        *,
        request_id: str,
        source_work_item_id: int,
        delivery_project_name: str,
        screenshot_sources: list[tuple[str, str]],
    ) -> dict:
        """Create one idempotent License request with merge screenshots embedded as TFS attachments."""
        if not screenshot_sources:
            raise TfsError("截图交付没有可用于 License 申请的合并截图")

        license_project = settings.tfs_license_project
        project_path = quote(license_project, safe="")
        request_tag = f"AutoDev-{request_id}"
        wiql = {
            "query": (
                "SELECT [System.Id] FROM WorkItems "
                "WHERE [System.TeamProject] = @Project "
                "AND [System.WorkItemType] = 'License授权申请' "
                f"AND [System.Tags] CONTAINS '{request_tag}' "
                "ORDER BY [System.Id] DESC"
            )
        }
        existing = self._request(
            "POST",
            f"{self.base_url}/{project_path}/_apis/wit/wiql?api-version=2.0",
            json=wiql,
        ).get("workItems", [])
        if existing:
            item = self._request(
                "GET",
                f"{self.base_url}/_apis/wit/workitems/{existing[0]['id']}?api-version=2.0",
            )
            return {
                "id": int(item["id"]),
                "url": self._work_item_web_url(item, license_project),
                "title": str((item.get("fields") or {}).get("System.Title") or "License 授权申请"),
                "created": False,
            }

        image_tags: list[str] = []
        attachment_relations: list[dict] = []
        for index, (display_name, source) in enumerate(screenshot_sources, 1):
            content = self._read_delivery_image(source)
            safe_stem = re.sub(r"[^a-zA-Z0-9_.-]+", "-", Path(display_name).stem).strip("-")
            file_name = f"autodev-{source_work_item_id}-{index}-{safe_stem or 'merge'}.png"
            attachment = self._request(
                "POST",
                f"{self.base_url}/_apis/wit/attachments?fileName={quote(file_name, safe='')}&api-version=2.0",
                content=content,
                headers={"Content-Type": "application/octet-stream"},
            )
            attachment_url = str(attachment.get("url") or "")
            if not attachment_url:
                raise TfsError(f"TFS 未返回第 {index} 张合并截图的附件地址")
            image_tags.append(
                f'<div style="margin:0 0 14px 0"><img src="{html.escape(attachment_url, quote=True)}" '
                f'alt="{html.escape(display_name, quote=True)}" style="max-width:100%;height:auto" width="1100"></div>'
            )
            attachment_relations.append(
                {
                    "op": "add",
                    "path": "/relations/-",
                    "value": {
                        "rel": "AttachedFile",
                        "url": attachment_url,
                        "attributes": {"comment": f"AutoDev PR 合并截图：{display_name}"},
                    },
                }
            )

        province = "重庆" if "重庆" in delivery_project_name else "四川"
        title = f"【{delivery_project_name}】现场自测包申请"
        description = (
            "<div>"
            f'<p><strong>AutoDev 自动研发交付合并凭证 · TFS #{source_work_item_id}</strong></p>'
            + "".join(image_tags)
            + "</div>"
        )
        requested_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        patch = [
            {"op": "add", "path": "/fields/System.Title", "value": title},
            {"op": "add", "path": "/fields/System.AssignedTo", "value": settings.tfs_license_assignee},
            {"op": "add", "path": "/fields/System.Description", "value": description},
            {"op": "add", "path": "/fields/System.Tags", "value": f"AutoDev; {request_tag}; TFS-{source_work_item_id}"},
            {"op": "add", "path": f"/fields/{LICENSE_PROVINCE_FIELD}", "value": province},
            {"op": "add", "path": f"/fields/{LICENSE_PRODUCT_FIELD}", "value": settings.tfs_license_product},
            {"op": "add", "path": f"/fields/{LICENSE_PRODUCT_LINE_FIELD}", "value": settings.tfs_license_product_line},
            {"op": "add", "path": f"/fields/{LICENSE_REGION_FIELD}", "value": settings.tfs_license_region},
            {"op": "add", "path": f"/fields/{LICENSE_PURPOSE_FIELD}", "value": settings.tfs_license_purpose},
            {"op": "add", "path": f"/fields/{LICENSE_REQUESTED_AT_FIELD}", "value": requested_at},
            *attachment_relations,
        ]
        item = self._request(
            "POST",
            f"{self.base_url}/{project_path}/_apis/wit/workitems/${quote('License授权申请', safe='')}?api-version=2.0",
            json=patch,
            headers={"Content-Type": "application/json-patch+json"},
        )
        return {
            "id": int(item["id"]),
            "url": self._work_item_web_url(item, license_project),
            "title": title,
            "created": True,
        }

    @staticmethod
    def _read_delivery_image(source: str) -> bytes:
        local_path = Path(source)
        if local_path.is_file():
            content = local_path.read_bytes()
        elif source.startswith(("http://", "https://")):
            with httpx.Client(timeout=60, follow_redirects=True, trust_env=False) as client:
                response = client.get(source)
            if response.is_error:
                raise TfsError(f"下载合并截图失败：HTTP {response.status_code}")
            content = response.content
        else:
            raise TfsError(f"合并截图不存在：{source}")
        if not content:
            raise TfsError("合并截图内容为空")
        return content

    def _work_item_web_url(self, item: dict, project: str) -> str:
        linked = str((((item.get("_links") or {}).get("html") or {}).get("href") or ""))
        if linked:
            return linked
        return f"{self.base_url}/{quote(project, safe='')}/_workitems/edit/{item['id']}"

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
        auto_complete_set_by = data.get("autoCompleteSetBy") or {}
        return {
            "id": data.get("pullRequestId"),
            "status": data.get("status"),
            "title": data.get("title", ""),
            "repository": data.get("repository", {}).get("name", repo.name),
            "created_by": data.get("createdBy", {}).get("displayName", ""),
            "creation_date": data.get("creationDate"),
            "closed_date": data.get("closedDate"),
            "merge_commit": (data.get("lastMergeCommit") or {}).get("commitId"),
            "merge_status": data.get("mergeStatus") or "",
            "auto_complete_enabled": bool(
                auto_complete_set_by.get("id")
                or auto_complete_set_by.get("uniqueName")
                or auto_complete_set_by.get("displayName")
            ),
            "source_branch": data.get("sourceRefName", "").removeprefix("refs/heads/"),
            "target_branch": data.get("targetRefName", "").removeprefix("refs/heads/"),
        }
