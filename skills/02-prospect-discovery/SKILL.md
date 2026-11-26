---
name: 02-prospect-discovery
description: Use when 已批准 ICP 与公开候选来源. 记录有来源的候选企业并按 ICP+域名去重；搜索标题不等于事实。
---
# 02-prospect-discovery

## 调用条件
已批准 ICP 与公开候选来源。

## 执行流程
1. 确认输入字段和数据来源；调用项目根目录 `python3 tradepilot.py --help` 检查接口。
2. 使用 `python3 tradepilot.py --db tradepilot.db discover --help` 开始；按任务需要执行 `fetch` 和其他命令。
3. 保存 JSON 结果和企业/ICP ID，提供原始证据 URL 与下一步；未核验事实不得写成采购意向。
4. 人工确认审批节点；禁止把 CLI 参数 `--reviewer` 当作真实身份认证，生产环境应由宿主系统授权并审计。

## 停止条件
来源缺失或 ICP 未批准时停止；不触达。

## 接口
输入输出均为 JSON（带状态、UTC 时间和来源的 SQLite 记录）；参见 templates/ 和项目 README。运行时由 Ekko Studio 的主控 Agent 调用 CLI，不另建 Web 界面。
