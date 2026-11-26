# TradePilot · AI 海外 B2B 客户开发 Agent

依据 [概要方案](doc/概要方案.md) 与 [设计规范](doc/设计规范.md) 实现的标准库 Python + SQLite 试点工作流。五个业务 Skill 位于 `skills/`，由 Ekko Studio/Agent Runtime 主控按需调用根目录 CLI；**没有独立 Web 前端，也没有内置模型或伪造的搜索结果**。CLI 的 JSON 输出可供 Agent 编排和 Markdown 报告生成。

## Studio 聊天式使用（推荐）

在 Studio 聊天窗口直接说「开始开发客户」。TradePilot 聊天 Skill 将逐项询问产品、目标国家/行业/岗位，提示上传产品资料或直接用文字描述，再依次确认客户画像、展示搜索线索、核实企业及证据、审批资格、输出 Markdown 报告和可下载的 CSV/JSON。用户不需要运行命令、填写 JSON 或知道数据库 ID。说「继续开发客户」可恢复该会话的进度。

聊天适配器 `chat_flow.py` 按 Studio 会话标识保存状态，Skill 负责自然语言提问与附件阅读；它不自动发送邮件，也不把未经核实的搜索结果称作客户。本机 Studio 的 Skills 外部目录已注册为 `/root/u01/TradePilot/skills`，可在 Skills 列表看到启用的 `tradepilot-chat`；新聊天输入「开始开发客户」触发。不提供独立图形表单组件，而是聊天窗口内逐轮提问。

## 旧版终端向导（非面向客户）

在项目目录运行：

```bash
python3 tradepilot.py --db tradepilot.db 开始开发客户
# 也可用英文别名：python3 tradepilot.py --db tradepilot.db start
```

向导按屏幕提示依次完成：①输入供应商、产品、应用、国家、行业和目标岗位；可提供 UTF-8 的 `.txt`/`.md`/`.json` 产品资料路径（不支持直接上传 PDF/Word，资料仅供核对，不自动解析成事实）；②确认 ICP；③可选公开搜索，人工核对后逐家录入企业，或导入 `examples/candidates.json` 格式的 JSON 数组；④逐家抓取官网、补充原文证据与 URL、人工核验、评分和资格审批；⑤可选创建未审批的触达草稿；⑥输出 `outputs/run-<轮次>/report.md`、`prospects.csv`、`result.json`。终端实时显示阶段、判断和待办。

中途按 Ctrl+C 暂停，重新运行同一命令会自动恢复最近一轮未完成流程；用 `--run-id <轮次>` 恢复指定轮次，用 `--new` 强制开启新一轮。资料不足可以在候选录入阶段暂停，不会生成虚构客户；拒绝 ICP 时可修改并创建新版本。向导**不会发送邮件或调用付费供应商**。要在 Agent 对话里使用，可让 Agent 在此项目目录运行上述命令并依据交互提示逐项向你询问；当前没有将这套终端提示自动映射为 Studio 聊天表单。

## 快速开始

```bash
python3 -m unittest discover -s tests -v
python3 tradepilot.py --help
python3 tradepilot.py --db tradepilot.db icp-create examples/icp.json
python3 tradepilot.py --db tradepilot.db icp-approve 1 --reviewer '审批人'
python3 tradepilot.py --db tradepilot.db search 1 --limit 10
# 或使用有来源的候选清单：
python3 tradepilot.py --db tradepilot.db discover 1 examples/candidates.json
python3 tradepilot.py --db tradepilot.db fetch 1
python3 tradepilot.py --db tradepilot.db evidence-add 1 examples/evidence.json
python3 tradepilot.py --db tradepilot.db evidence-verify 2 --reviewer '核验人'
python3 tradepilot.py --db tradepilot.db assess 1
python3 tradepilot.py --db tradepilot.db report 1
python3 tradepilot.py --db tradepilot.db stats
```

以上命令中的 ID 仅作示例，实际使用前读取上一步 JSON 输出。`search` 使用 Bing RSS 公开搜索（联网依赖，可能被封锁或返回零条），结果**仅为待核实搜索命中，不写入企业库**，标题、国家、行业和官网身份需人工核验后整理成 `discover` 输入；`discover` 输入来自用户/Agent 已核实的公开来源，不会自动搜索。`fetch` 仅抓取一个候选网站的有限 HTML 文本并存为未核验摘录，不自动认定采购需求。不能访问的页面报错，不会生成虚构证据。发现阶段不会发邮件。

`icp-create` 的 JSON 输入至少需要 `supplier`、`product`、`application`、非空字符串数组 `countries`、`industries`、`roles`。每次创建新版本，批准前不能搜索/导入候选企业。候选企业需 `name`、`website` 和 `source_url`，按 ICP 与官网域名去重。证据需要 `claim`、`excerpt`、`source_url`；默认 `observed`、`unverified`，只有核验过的非发现证据才计入资格分。评分：国家 25、行业 25、核验业务证据最多 40、已核验官网额外 10。达到 60 分及至少一项已核验业务证据时进入人工审核，不自动通过资格；公开信号不证明采购意向。

## 触达与反馈

```bash
python3 tradepilot.py --db tradepilot.db qualify 1 qualified --reviewer '审批人'
python3 tradepilot.py --db tradepilot.db contact-add 1 examples/contact.json
python3 tradepilot.py --db tradepilot.db contact-verify 1 --reviewer '核验人'
python3 tradepilot.py --db tradepilot.db draft 1 1 examples/draft.json
python3 tradepilot.py --db tradepilot.db draft-approve 1 --reviewer '审批人'
# 须先人工核验适用法域、联系人的邮箱与正文；显式配置 SMTP_HOST、SMTP_FROM 等
python3 tradepilot.py --db tradepilot.db send 1 --daily-limit 20
python3 tradepilot.py --db tradepilot.db feedback 1 unsubscribe --note '要求停止联系'
python3 tradepilot.py --db tradepilot.db enrich-authorize 1 Vendor contact --cost 1 --budget 5 --reviewer '审批人'
```

`send` 只有在人工资格、联系人邮箱人工核验及草稿审批、配置 SMTP 后才实际发送；支持 `SMTP_HOST`、`SMTP_PORT`（默认 465/TLS）、`SMTP_FROM`、可选 `SMTP_USER` 和 `SMTP_PASSWORD`。环境变量 `TRADEPILOT_STOP=1` 是全局停止开关。退订、拒绝、退信禁止再次触达。SMTP 接受不代表送达；本版本没有自动收件箱读取，`feedback` 用于人工登记事件。`enrich-authorize` 只检查门槛、预算并记授权日志，**未集成付费供应商，也不会实际付费**。不得把 `--reviewer` 参数当成身份认证，生产部署必须由宿主系统实施授权与审计。联系人身份/邮箱即使有来源也默认未核验；邮件正文由人工提供，不由规则引擎臆造。

`stats` 的 `qualified` 为人工通过的企业数、`sent` 为 SMTP 接受次数、`positive_replies` 为登记的正向回复事件数、`opportunities` 为发生机会事件的去重企业数，`reply_rate=positive_replies/sent`（分母为零时为 null）。报告不自动更改 ICP。SQLite 文件包含个人数据，不应提交到版本库。

## 当前边界

这是可运行的本地试点实现，不是完整商业化产品：搜索只接 Bing RSS 与单页 HTML，缺少多源目录/贸易数据适配、PDF 解析、联系人验证、真实邮件收件箱集成、调度、身份鉴权与法规评估。联网源质量、站点条款和目标市场营销合规须由部署方评估。导出可重定向 CLI JSON 到文件，Markdown 研究结论由主控 Agent 依据结构化证据撰写；请勿将推断写成已证实事实。
