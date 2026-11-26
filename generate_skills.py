"""Generate the five portable business skill packages from a shared interface."""
import json
from pathlib import Path

ROOT = Path(__file__).parent / 'skills'
SPECS = [
    ('01-market-icp', '产品和目标市场资料', '生成待审批 ICP，明确产品、应用、国家、行业、采购角色。', 'icp-create', 'icp-approve', '未有人类确认产品事实和目标范围时停止；未经审批不得发现客户。'),
    ('02-prospect-discovery', '已批准 ICP 与公开候选来源', '记录有来源的候选企业并按 ICP+域名去重；搜索标题不等于事实。', 'discover', 'fetch', '来源缺失或 ICP 未批准时停止；不触达。'),
    ('03-prospect-research', '候选企业与可核验公开证据', '分别标记事实、推断和未知；人工核验证据、评分、资格审批。', 'evidence-add', 'assess', '未核验业务证据时不通过资格；岗位不等于决策权。'),
    ('04-outreach-followup', '已审核资格客户、联系人来源和邮件正文', '生成待审核草稿；审批后才能通过已配置 SMTP 发送；记录反馈和停止联系。', 'draft', 'feedback', '拒绝、退订、退信、缺少审批或违反适用法规时停止。'),
    ('05-acquisition-intelligence', '企业、证据、触达和转化事件', '统计有明确口径的企业、回复及机会；反馈用于提出 ICP 建议，绝不自动改正式版本。', 'report', 'stats', '样本不足时仅提出待审建议，不将候选数当有效客户数。'),
]
for name, trigger, purpose, start, end, stop in SPECS:
    base = ROOT / name
    for folder in ('references', 'scripts', 'templates', 'tests'):
        (base / folder).mkdir(parents=True, exist_ok=True)
    (base / 'SKILL.md').write_text(f'''---
name: {name}
description: Use when {trigger}. {purpose}
---
# {name}

## 调用条件
{trigger}。

## 执行流程
1. 确认输入字段和数据来源；调用项目根目录 `python3 tradepilot.py --help` 检查接口。
2. 使用 `python3 tradepilot.py --db tradepilot.db {start} --help` 开始；按任务需要执行 `{end}` 和其他命令。
3. 保存 JSON 结果和企业/ICP ID，提供原始证据 URL 与下一步；未核验事实不得写成采购意向。
4. 人工确认审批节点；禁止把 CLI 参数 `--reviewer` 当作真实身份认证，生产环境应由宿主系统授权并审计。

## 停止条件
{stop}

## 接口
输入输出均为 JSON（带状态、UTC 时间和来源的 SQLite 记录）；参见 templates/ 和项目 README。运行时由 Ekko Studio 的主控 Agent 调用 CLI，不另建 Web 界面。
''', encoding='utf-8')
    (base / 'references/rules.md').write_text(f'# 规则\n{stop}\n所有业务判断区分 observed/inferred/unknown/contradicted；联系人与企业分开管理。\n', encoding='utf-8')
    (base / 'references/methodology.md').write_text(f'# 方法\n{purpose}\n证据需保留原始 URL、摘要、采集时间、核验状态；公开业务活动不等于购买意图。\n', encoding='utf-8')
    (base / 'references/quality-criteria.md').write_text('# 质量门槛\n必须有来源；不可访问的来源标记未核验；缺失字段拒绝；审批、去重和费用边界须通过测试。\n', encoding='utf-8')
    (base / 'templates/input.json').write_text(json.dumps({'task_id': None, 'skill_name': name, 'supplier_id': None, 'icp_id': None, 'input_reference': [], 'approval_required': True}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (base / 'templates/output.json').write_text(json.dumps({'task_id': None, 'skill_name': name, 'task_status': 'pending', 'created_at': None, 'output_reference': [], 'source_url': None}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (base / 'tests/test_cases.json').write_text(json.dumps({'cases': ['valid_input', 'missing_input', 'invalid_input', 'unavailable_source', 'low_confidence', 'duplicate', 'interrupted', 'output_shape', 'approval_and_cost_gate']}, indent=2) + '\n', encoding='utf-8')
    (base / 'scripts/validate.py').write_text('''import json
import sys
from pathlib import Path

def main():
    data = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    required = ('task_id', 'skill_name', 'supplier_id', 'icp_id', 'input_reference', 'approval_required')
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(f'Missing fields: {missing}')
    print(json.dumps({'valid': True, 'skill_name': data['skill_name']}))

if __name__ == '__main__':
    main()
''', encoding='utf-8')
    (base / 'scripts/process.py').write_text(f'''import subprocess
import sys
from pathlib import Path

if __name__ == '__main__':
    root = Path(__file__).resolve().parents[3]
    raise SystemExit(subprocess.call([sys.executable, str(root / 'tradepilot.py'), *sys.argv[1:]]))
''', encoding='utf-8')
    (base / 'scripts/export.py').write_text('''import json
import sys
from pathlib import Path

if __name__ == '__main__':
    data = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    Path(sys.argv[2]).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\\n', encoding='utf-8')
''', encoding='utf-8')
print('generated', len(SPECS), 'skills')
