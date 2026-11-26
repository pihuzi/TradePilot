---
name: 01-market-icp
description: Use when 产品和目标市场资料. 生成待审批 ICP，明确产品、应用、国家、行业、采购角色。
---
# 01-market-icp

## 调用条件
产品和目标市场资料。

## 执行流程
1. 确认输入字段和数据来源；调用项目根目录 `python3 tradepilot.py --help` 检查接口。
2. 使用 `python3 tradepilot.py --db tradepilot.db icp-create --help` 开始；按任务需要执行 `icp-approve` 和其他命令。
3. 保存 JSON 结果和企业/ICP ID，提供原始证据 URL 与下一步；未核验事实不得写成采购意向。
4. 人工确认审批节点；禁止把 CLI 参数 `--reviewer` 当作真实身份认证，生产环境应由宿主系统授权并审计。

## 停止条件
未有人类确认产品事实和目标范围时停止；未经审批不得发现客户。

## 接口
输入输出均为 JSON（带状态、UTC 时间和来源的 SQLite 记录）；参见 templates/ 和项目 README。运行时由 Ekko Studio 的主控 Agent 调用 CLI，不另建 Web 界面。
