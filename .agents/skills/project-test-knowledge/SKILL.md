---
name: project-test-knowledge
description: Maintain and consult project-specific testing knowledge for AutoDev and Sichuan network-command systems. Use when the user supplements test accounts, shift positions, unit jurisdiction, main/distribution-grid rules, or regression experience, and before designing or running those projects' automated tests.
---

# 项目测试知识库

保存用户逐次补充的业务测试经验，让后续测试规划、自动测试开发和实际回归读取同一份持久化知识。此技能不是部署器，也不是已经执行成功的测试报告。

## 阅读入口

先读 [项目索引](references/index.md)，再按当前项目选择资料：

- 网络发令项目：读 [账号与岗位通用约定](references/network-command-common.md)。
- 四川主配一体、单位管辖或 `areano`：同时读 [四川管辖关系](references/sichuan-jurisdiction.md)。仅对存在该业务模式的项目适用。
- 成都 PC 网络发令：读 [成都项目档案](references/projects/chengdu-network-command.md)。不能默认套用到网络发令 APP 或其他地市。
- 使用 101.14 环境时：读 [环境盘点快照](references/environments/101-14.md)，对易变化状态重新检查。
- 新增或修订知识时：读 [维护与 GitHub 同步](references/maintenance.md)。不要因为只需查询知识而提交文件。

## 使用知识

1. 匹配项目及业务端，保留资料中的适用范围、来源、日期和待确认项。找不到项目档案时可使用明确适用的通用规则，但不得编造当地账号、schema 或岗位权限。
2. 区分「用户确认的业务事实」「带日期的只读观察」「建议测试场景」「待确认信息」。用户指定了账号，不代表已经验证登录；页面返回 200，不代表业务回归通过。
3. 测试身份是账号、当前岗位、当前单位、业务范围和票据状态的组合。交接班可能改变岗位，不能把账号名当成固定权限。
4. 管辖关系按有方向的单位关系理解，不能反向使用、固定两层，或仅凭层级推导未授权的跨级权限。`areano` 的业务含义与实际存储编码分别核对。
5. 规划时将使用的知识条目 ID、知识库 Git 提交和待确认项传给测试执行阶段。后续实现应冻结本轮知识版本，不把知识档案当成已接入平台的自动能力。
6. 只记录用户补充时不连接服务器、不登录业务系统、不执行 SQL、不交接班。实际测试的读写范围服从当轮请求。
7. 实际登录需要口令时再读取本机凭据引用，不将口令展开进 Git、测试报告或长期业务知识正文。维护方法见同步说明。

## 用户继续补充时

将事实更新到对应项目档案，跨项目规则必须带适用条件。保留历史修订的来源和边界；新的明确更正可以替代旧结论，含糊冲突只标记待确认。用户已要求后续补充同步 GitHub：按维护说明完成本次有范围的提交与推送，不扩大为自动部署或后台定时任务。
