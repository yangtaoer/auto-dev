from __future__ import annotations

from copy import deepcopy
from typing import Any


NETWORK_COMMON_INSTRUCTIONS = """
这是传统 PC/TBP 网络发令项目。开始修改前必须检查同一 TFS 需求的历史任务、PR、提交以及最新目标分支，避免重复开发；历史结论只能作为线索，必须用当前代码复核。多仓项目只修改实际涉及的仓库，禁止把其他地区的表名、区域字段和专属规则直接复制进来。每条验收标准都要建立验收项账本并映射仓库、文件、测试和证据，以便局部交付与精确回滚。涉及“保存成功但页面未变化”时，按显示字段、提交参数、持久化字段、查询关联键、刷新逻辑逐段核对，不能用额外刷新掩盖关联错误。涉及统计、状态、积分、排序或导出时必须验证数据来源时点、计算公式、显示字段、排序字段和导出字段的一致性。TBP/MiniUI 页面在 Grid 之间传递数据时只能复制业务字段，禁止复用 _id、_uid 等框架内部标识。新增 view.xml 必须同时给出菜单链接、权限绑定和部署操作；XML 内嵌 SQL 需要按 DM7 语法及开发库样例进行验证。前端修改必须通过真实路由、项目规定视口和截图验收，并核对构建资源哈希、部署目录层级、浏览器及 iframe 缓存。代码问题、数据问题和环境问题必须分开描述，数据库结论必须注明开发、测试或现场环境。
""".strip()


COMMON_QUALITY_PROFILE: dict[str, Any] = {
    "history_reuse": True,
    "require_acceptance_ledger": True,
    "business_invariants": {
        "required": False,
        "change_patterns": [
            "**/*stat*", "**/*score*", "**/*count*", "**/*report*",
            "**/*statistics*", "**/*rank*", "**/*export*",
        ],
    },
    "sql": {
        "migration_version_guard": True,
        "require_schema_placeholder": False,
        "require_changelog": False,
        "require_database_verification": True,
    },
    "visual": {
        "required_for_frontend": True,
        "frontend_patterns": ["**/*.jsp", "**/*.js", "**/*.css", "**/*.vue", "**/*.html", "**/*.view.xml"],
        "viewports": ["1280x720", "1440x900"],
        "deployment_checks": ["asset_manifest_checked", "directory_layout_checked", "cache_strategy_checked"],
    },
    "menu": {"require_binding_manifest": True},
    "analysis": {"require_environment_label": True, "compare_history": True},
}


NETWORK_ARTIFACT_POLICY = {
    "require_manifest": True,
    "require_packages": False,
    "allowed_changed_asset_kinds": ["sql", "config"],
    "allowed_user_facing_kinds": [
        "release_artifact", "merge_screenshot", "menu_link", "license_request",
        "delivery_manifest", "verification_report", "analysis_report",
    ],
    "forbidden_standalone_extensions": [],
}


PROJECT_SPECIFIC_INSTRUCTIONS = {
    "bazhong-self-developed": """
这是巴中自巡航自研单仓项目。前端构建要求 Node 18+，后端使用 Maven；业务表引用必须使用 ${BIZ_SCHEMA}，跨 Schema 来源必须明确写出 Schema。版本化 SQL 在合入最新 dev 后必须检查版本号冲突并同步 docs/sql-changelog.md。DM7 中不要向带聚集键的既有表直接增加 CLOB/BLOB；大字段优先使用独立数据表并验证迁移。绩效、工作量和统计页面必须用开发库代表性样例核对实时数据与快照数据的时间一致性、总分公式、排序、导出、未同步与重新同步状态。涉及云端目录删除或批量文件操作时先将物理文件移入可恢复暂存区，再提交数据库事务，失败时恢复文件，禁止先永久删除文件。
""".strip(),
    "nanchong-network-command": """
南充项目问题定位优先核对当前登录厂站、组织、监控单位、CZDW/AREANO 与 mxids 的完整数据链，并区分描述中的厂站与附件主数据中的实际映射。按钮可见条件和点击接口条件必须一致；空 ID、空接口响应和 JSP 空对象需要有明确防御。多条验收内容必须分别编号，后续部分回滚只能撤销对应验收项，不能整批回退。新增页面必须交付菜单路径和权限绑定说明。
""".strip(),
    "sichuan-dispatch-network-command": """
四川省调 MiniUI 页面中，查询 Grid 与已选 Grid 之间只传业务字段；历史数据使用目标 Grid 的 setData 初始化，并按业务主键去重。CAS 修改必须追踪真实会话来源：自建 CAS 使用 logout-complete-url，只有 DKY_CAS 使用 logout-self-callback-url，分别建立回归测试。优先检查最新 dev 是否已有同需求修复；已有实现时补回归证据，不重复覆盖运行代码。条件允许时使用项目本地部署地址完成真实登录与页面冒烟验证，但账号密码不得写入提示词、日志或仓库。
""".strip(),
    "chengdu-network-command": """
成都项目必须优先使用成都专属仓库和扩展点，不能把修改扩散到其他四川地区。遇到保存后状态未变化，必须核对写入键与查询键是否一致，例如业务范围、月计划号和设备名称等字段不能错配；数据关联错误时不得用重复刷新作为修复。任何成都专属条件都要有明确区域守卫和回归证据。
""".strip(),
}


APP_QUALITY_PROFILE = {
    **deepcopy(COMMON_QUALITY_PROFILE),
    "business_invariants": {
        "required": False,
        "change_patterns": ["**/*home*", "**/*list*", "**/*badge*", "**/*statistics*", "**/*score*"],
    },
    "visual": {
        # APP 功能改动优先使用生产构建、真实路由和自动断言作为验收证据。
        # 只有需求明确要求截图、设计稿还原或视觉效果时，才把真实页面截图提升为阻塞门禁。
        "required_for_frontend": False,
        "require_when_requirement_mentions": [
            "真实页面截图", "页面截图", "截图验收", "以截图为准", "以图片为准",
            "设计稿", "视觉效果", "页面效果", "界面效果", "像素级", "还原UI",
        ],
        "frontend_patterns": ["dcsd-app-ui/**/*.vue", "dcsd-app-ui/**/*.js", "dcsd-app-ui/**/*.css"],
        "viewports": ["390x844", "430x932"],
        "deployment_checks": ["asset_manifest_checked", "directory_layout_checked", "cache_strategy_checked"],
    },
    "menu": {"require_binding_manifest": False},
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def apply_project_experience(project: dict[str, Any]) -> dict[str, Any]:
    """Attach audited local-development experience to every runner project snapshot."""
    result = dict(project)
    key = str(result.get("project_key") or "")
    if key == "network-command-app":
        result["quality_profile"] = _deep_merge(APP_QUALITY_PROFILE, result.get("quality_profile") or {})
        result["artifact_policy"] = _deep_merge(
            {
                "require_manifest": False,
                "require_packages": True,
                "allowed_changed_asset_kinds": ["sql"],
                "allowed_package_extensions": [".zip", ".jar"],
                "allowed_user_facing_kinds": ["package", "sql"],
                "forbidden_standalone_extensions": [".xml"],
            },
            result.get("artifact_policy") or {},
        )
    elif key == "bazhong-self-developed":
        result["quality_profile"] = _deep_merge(
            COMMON_QUALITY_PROFILE,
            {
                "business_invariants": {"required": False},
                "sql": {
                    "migration_version_guard": True,
                    "require_schema_placeholder": True,
                    "require_changelog": True,
                    "changelog_path": "docs/sql-changelog.md",
                    "require_database_verification": True,
                },
            },
        )
        result["artifact_policy"] = _deep_merge(
            {
                "require_manifest": True,
                "require_packages": True,
                "allowed_changed_asset_kinds": ["sql", "config"],
                "allowed_package_extensions": [".zip"],
                "allowed_user_facing_kinds": [
                    "package", "sql", "config", "delivery_manifest", "verification_report", "analysis_report",
                ],
                "forbidden_standalone_extensions": [],
            },
            result.get("artifact_policy") or {},
        )
    else:
        result["quality_profile"] = _deep_merge(COMMON_QUALITY_PROFILE, result.get("quality_profile") or {})
        result["artifact_policy"] = _deep_merge(NETWORK_ARTIFACT_POLICY, result.get("artifact_policy") or {})

    instructions = []
    if key not in {"network-command-app", "bazhong-self-developed"}:
        instructions.append(NETWORK_COMMON_INSTRUCTIONS)
    if PROJECT_SPECIFIC_INSTRUCTIONS.get(key):
        instructions.append(PROJECT_SPECIFIC_INSTRUCTIONS[key])
    if str(result.get("development_instructions") or "").strip():
        instructions.append(str(result["development_instructions"]).strip())
    result["development_instructions"] = "\n\n".join(dict.fromkeys(instructions))
    if not str(result.get("verification_command") or "").strip() and str(result.get("build_command") or "").strip():
        result["verification_command"] = result["build_command"]
    return result
