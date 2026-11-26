---
name: 05-acquisition-intelligence
description: Use when 企业、证据、触达和转化事件. 统计有明确口径的企业、回复及机会；反馈用于提出 ICP 建议，绝不自动改正式版本。
---
# 05-acquisition-intelligence

## 调用条件
企业、证据、触达和转化事件。

## 执行流程
1. 确认输入字段和数据来源；调用项目根目录 `python3 tradepilot.py --help` 检查接口。
2. 使用 `python3 tradepilot.py --db tradepilot.db report --help` 开始；按任务需要执行 `stats` 和其他命令。
3. 保存 JSON 结果和企业/ICP ID，提供原始证据 URL 与下一步；未核验事实不得写成采购意向。
4. 人工确认审批节点；禁止把 CLI 参数 `--reviewer` 当作真实身份认证，生产环境应由宿主系统授权并审计。

## 停止条件
样本不足时仅提出待审建议，不将候选数当有效客户数。

## 接口
输入输出均为 JSON（带状态、UTC 时间和来源的 SQLite 记录）；参见 templates/ 和项目 README。运行时由 Ekko Studio 的主控 Agent 调用 CLI，不另建 Web 界面。
