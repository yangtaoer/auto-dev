# Java 提交检查与 TFS 合并校验

AutoDev 在提交前对登记仓库的 Java 变更运行 Checkstyle 和阿里巴巴 P3C。工作区文件与任务的目标分支基线使用同一组扫描规则。质量门禁记录新增问题和基线已有问题的数量，新增问题附带工具、规则、文件和行号。

Checkstyle 检查 80 行有效代码的方法上限、循环与判断最多嵌套 3 层、空异常处理、直接调用 `printStackTrace`、直接记录敏感变量和在 JDBC 调用中拼接 SQL 参数等写法。P3C 的规则用于补充 Java 编码规范检查。执行器需要可用的 JDK 和 Maven。扫描依赖下载到执行器数据目录，仓库的 Maven 和扫描配置由项目维护。

## 登记仓库和分支

在项目的 `quality_profile.java_code_checks` 中登记一个配置。`repository` 对应任务工作区的仓库名，`target_branch` 对应目标分支。

```json
{
  "java_code_checks": [
    {
      "repository": "webservice-scczp-sd",
      "target_branch": "dev_sd",
      "mode": "observe"
    }
  ]
}
```

- `observe` 记录预检结果。使用同一 PR 的正式扫描回执对照新增问题与规则编号。
- `enforce` 拦截新增的 Checkstyle 错误、新增扫描跳过设置及 `blocking_p3c_rules` 中列出的 P3C 规则。代码错误交给原有的有限次数修复流程，提交前校验暂存区 Java 文件与已扫快照一致。工具执行失败时暂停提交。
- 对需要独立跑 Maven 单测的仓库，可以配置 `unit_tests`，包括 `classes`（测试类选择器）、`pom`（仓库内相对路径，可选）和 `maven_profile`（可选）。AutoDev 从本次生成的 Surefire XML 确认实际执行了测试。

## 使用正式 TFS 构建作为自动合并条件

管理者用试点 PR 核对规则版本、质量配置、扫描范围及新增问题定义，并确认目标分支上已启用的必需构建。启用 `enforce` 时，可以在 `required_merge_builds` 列出已核实的 TFS 构建定义编号。这一配置适用于经 PR 合并的交付模式。

AutoDev 按 PR 的当前源提交与合并预览核对 TFS 策略，再确认构建定义、PR 合并引用和合并预览提交与成功构建记录一致。每个登记的构建通过后，才允许 AutoDev 开启自动完成。产品人工审核继续由审核人投票，四川自动审核模式使用既有审核账号。当前预览构建报出可修复的代码问题时，四川自动审核流程可沿用已有的 PR 修复，推送修复后的提交等待新的正式回执。

仓库预设将四川省调接口 `webservice-scczp-sd/dev_sd` 登记为观察试点。正式规则和构建定义可从 TFS 核实后再按项目启用阻断。
