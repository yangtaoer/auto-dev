# 四川网络发令 APP 开发与交付约定

平台项目为“网络发令APP”。`【成都网络下令APP】`、`【成都网络发令APP】` 均归入 APP，不隐式创建成都 PC 子任务。明确联合需求可用 `【网络发令APP】【成都网络发令】`，每个项目须有独立匹配证据；相同别名冲突时停止猜测。

## 仓库与开发

- 本机目录 `dcsd-app/dcsd-app-ui` → TFS DCS `dcsd-app-ui-sichuan`，目标 `dev`。
- 本机目录 `dcsd-app/dcsd-app-starter` → TFS DCS `dcsd-app-starter-sichuan`，目标 `dev`。
- 前端 Vue2/Vant/Vue CLI4；后端 Java8/Spring Boot/Maven。
- 成都页面复用 `src/views/nanchong`，成都条件为 `_sccd`。后端按当前接口的 `area=sccd`、`sichuanScope=1` 等条件隔离。区域值不能直接推广到其他地市。
- 先检查最新 dev 与前置需求实现；主工作区的未提交改动不混入隔离工作区。
- 沿 APP 实际调用链修改前后端，不能用 PC 后端未接入的新接口代替完整需求。
- 缺少必要仓库在准备阶段失败；研发过程中发现缺少业务信息进入待补充。普通风险记入 `risks`，未完成验收或需要授权的阻塞记入 `blocking_risks`。旧版未分级结果仍要求确认。

## 提交与打包

平台统一提交需求分支，按变更仓库创建 PR、关联需求并由四川审核账号投票。所有 PR 确认完成后，再获取最新目标分支构建；不以未合并 PR 的包代替正式交付。

只构建实际修改端：

- 前端 `nanchongydzt-build`、平台 `7`，校验移动中台 URL、appKey 与免密逻辑。交付 `ddyxzhyy.zip`，根目录 `ddyxzhyy/`。
- 后端 Maven `clean package -DskipTests`，保留版本化 JAR 文件名直接交付。打包命令不替代研发阶段的定向测试。

`wechart_client@2.0.2-fix-data` 和 `thpush-lib@1.0.8` 在当前 npm 源均不可用。构建从配置的 APP 主仓库中提取同版本已验证依赖，封装为 SHA256 寻址的本地缓存；不会复制整套可变 node_modules。只在构建阶段临时将内部依赖替换为本地 tgz，成功或失败均恢复 package.json/package-lock.json。缺失/版本不符则报错，不降级替换。缓存位于 `data/runner/build-dependencies/`，不会上传 GitHub。构建日志记录依赖版本及哈希；其他依赖继续按当前仓库的清单/lock 安装。

本机项目预设中的 `development_instructions`、`repository_expectations` 会同步到云端任务快照。已创建的错误项目任务不能通过更改全局配置直接放行，需要重新发起以重新归类。已经人工完成的需求应先核对 PR，避免重复开发。

TFS 需求描述或附件中的图片由本机执行器使用 TFS PAT 下载到隔离的数据目录，再把本机绝对路径交给 DevCore 逐张读取。研发任务不再依赖浏览器登录态直接访问附件 URL；下载失败会记录具体图片错误，不能用“认证限制未读取”替代图片核对。
