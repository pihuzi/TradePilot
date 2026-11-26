---
name: tradepilot-chat
description: Use when 用户说开始开发客户或继续开发客户. 在 Studio 聊天中逐步提问和交付报告.
---

# TradePilot · 聊天式客户开发

用户只需在 Studio 对话说「开始开发客户」，之后按问题用自然语言回答。不要要求用户打开终端、填 JSON、提供本机路径或自行操作数据库。聊天窗口显示当前进度，每轮只问一项。说「继续开发客户」恢复本会话；说「进度」读取状态。用户上传产品文档时先用 read_file 读取附件，区分原文、推断和未知；不能读取时请用户粘贴文字。**不要自动发送邮件或调用付费 API。**

## Agent 执行接口（用户不可见）

项目根目录 `/root/u01/TradePilot`，入口模块 `chat_flow.py`，SQLite `tradepilot.db`。按当前 Studio 会话的唯一 ID 调用 `chat_flow.step(db, conversation, payload, output)`，`payload` 为 `{"action":"start"}`、`{"action":"status"}` 或 `{"action":"reply","value":...}`。也可以用脚本的 `--input <临时JSON路径>` 接口，但私人资料不能出现在 shell 命令历史中。每个会话使用稳定唯一的 conversation；不要跨用户复用。`step` 返回 `progress`, `question`, `notice`, `stage`, `done`, `output_dir`。状态存于 SQLite，可跨轮恢复。执行结果报错则保留原阶段，请用户补充。

- 供应商、产品、应用、国家、行业、目标岗位：向用户逐项提问；从文件提取后也要让用户核对。产品描述不得凭空增加技术参数。
- ICP `确认/修改`：先展示画像再由用户明确确认；不能替用户确认。
- 发现阶段 `搜索` 返回未核实命中；Agent 可通过公开页面核实企业身份/官网，向用户展示来源，得到确认后把 `name,website,source_url,country,industry,source_excerpt` 整理为内部 dict 传入。用户不需要看 JSON。至少一家公司才能进入研究。
- 研究阶段为每家公司提供原文证据及 URL；把 `claim,excerpt,source_url,source_type` 传入。仅在实际核实或用户确认后执行 `确认核验`；否则 `跳过`。`评估` 后仅满足门槛的客户进入资格审批。`通过/拒绝` 必须由用户明确选择。公开信号不证明采购意向。
- 草稿是可选项，默认跳过；若用户要写，只创建未审批草稿。不发邮件。
- `done` 后检查 `output_dir` 中的 `report.md`、`prospects.csv`、`result.json` 是否存在，用本机绝对路径的 Markdown 链接交付，并用通俗语言解释客户为什么被推荐、证据及下一步。

本 Skill 需要在 Studio 的 Skills 中安装/启用才会在新聊天中自动触发；项目源文件本身不等于安装成功。依赖仅 Python 标准库。详情见项目 README。
