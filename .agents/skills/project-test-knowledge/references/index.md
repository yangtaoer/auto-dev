# 项目测试知识索引

更新时间：2026-09-18（Asia/Shanghai）。已在用户授权下完成一次成都 PC 操作票浏览器纯流程验证；尚未接入平台自动部署或浏览器测试流水线，不能将单次验证等同于完整自动回归能力。

| 范围 / 项目键 | 输入名称 | 资料 | 状态 |
| --- | --- | --- | --- |
| 网络发令通用 | 网络发令、网络下令 | [账号与动态岗位](network-command-common.md) | 用户提供命名与交接班规则 |
| 四川主配一体 | 主网、配网、主配、调度管辖、areano | [管辖关系与数据范围](sichuan-jurisdiction.md) | 用户确认原则；各地 schema、编码需对应项目核实 |
| `chengdu-network-command` | 成都网络发令、成都网络下令；PC 端 | [成都测试档案](projects/chengdu-network-command.md)、[操作票实测流程](projects/chengdu-operation-ticket-flow.md) | 正值、值长、受令三会话已验证；一张纯流程票已归档，副值未独立登录 |
| 测试环境 `101-14` | 101.14、192.168.101.14 | [只读盘点快照](environments/101-14.md) | 2026-09-18 快照，运行状态不可视为永久配置 |

其他地市和 APP 暂无独立完整测试档案。收到对应经验时新增 `projects/<project-key>.md` 并更新本索引，不复制成都账号、数据库 schema 或具体单位到其他项目。

维护入口：[知识更新与 GitHub 同步](maintenance.md)。
