"""Terminal-guided, resumable prospecting session; no implicit outreach."""
import csv
import json

from pathlib import Path

import tradepilot as tp

STAGES = ('profile', 'approval', 'discovery', 'research', 'outreach', 'export', 'done')
LABELS = ('产品与目标市场', '确认 ICP', '发现候选企业', '逐家研究与资格审核', '触达草稿（可选）', '交付报告', '已完成')


def setup(db):
    db.execute('''CREATE TABLE IF NOT EXISTS guided_runs (
        id INTEGER PRIMARY KEY, stage TEXT NOT NULL, state TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
    db.commit()


def save(db, run):
    with db:
        db.execute('UPDATE guided_runs SET stage=?, state=?, updated_at=? WHERE id=?',
                   (run['stage'], json.dumps(run['state'], ensure_ascii=False), tp.now(), run['id']))


def create(db):
    setup(db)
    with db:
        identifier = db.execute('INSERT INTO guided_runs(stage,state,created_at,updated_at) VALUES(?,?,?,?)',
                                ('profile', '{}', tp.now(), tp.now())).lastrowid
    return {'id': identifier, 'stage': 'profile', 'state': {}}


def resume(db, identifier=None):
    setup(db)
    if identifier is None:
        record = db.execute("SELECT * FROM guided_runs WHERE stage!='done' ORDER BY id DESC LIMIT 1").fetchone()
    else:
        record = db.execute('SELECT * FROM guided_runs WHERE id=?', (identifier,)).fetchone()
    return {'id': record['id'], 'stage': record['stage'], 'state': json.loads(record['state'])} if record else None


def advance(db, run, stage):
    run['stage'] = stage
    save(db, run)


def ask(prompt, *, optional=False, default=None):
    while True:
        value = input(f'{prompt}{"（可留空）" if optional else ""}: ').strip()
        if value:
            return value
        if optional:
            return default
        print('此项必填；按 Ctrl+C 可暂停，下次继续。')


def yes(prompt, *, default=False):
    while True:
        value = input(f'{prompt} [{"Y/n" if default else "y/N"}]: ').strip().lower()
        if not value:
            return default
        if value in ('y', 'yes', '是', '确认'):
            return True
        if value in ('n', 'no', '否', '取消'):
            return False
        print('请输入 y 或 n。')


def document(path):
    p = Path(path).expanduser().resolve()
    if p.suffix.lower() not in ('.txt', '.md', '.json'):
        raise ValueError('当前只支持 .txt/.md/.json 产品资料；PDF/Word 请先转换成文本')
    if p.stat().st_size > 200_000:
        raise ValueError('产品资料不得超过 200 KB')
    data = p.read_text(encoding='utf-8').strip()
    if not data:
        raise ValueError('产品资料为空')
    return {'path': str(p), 'excerpt': data[:1000]}


def source_input():
    path = ask('已有候选企业 JSON 清单路径？无则输入“无”')
    if path == '无':
        return None
    data = tp.load(Path(path).expanduser())
    if not isinstance(data, list):
        raise ValueError('候选清单必须是 JSON 数组')
    if not data:
        raise ValueError('候选清单为空')
    return data


def manual_candidates():
    items = []
    while True:
        print('请填写已经核实的企业及发现来源；不要把搜索标题直接当企业名称。')
        items.append({'name': ask('企业名称'), 'website': ask('确认的官网 URL'),
                      'source_url': ask('发现来源 URL'),
                      'country': ask('国家', optional=True),
                      'industry': ask('行业', optional=True),
                      'source_excerpt': ask('发现来源摘录', optional=True)})
        if not yes('继续添加企业？'):
            return items


def profile(db, run):
    print('\n请准备：产品介绍（可选 .txt/.md/.json）、产品与应用、目标国家、行业、采购角色。')
    path = ask('产品资料文件路径；没有则输入“无”')
    doc = None if path == '无' else document(path)
    data = {'supplier': ask('供应商名称'), 'product': ask('产品/核心能力（仅写已确认事实）'),
            'application': ask('产品应用场景'),
            'countries': [x.strip() for x in ask('目标国家，多个用逗号分隔').split(',') if x.strip()],
            'industries': [x.strip() for x in ask('目标行业，多个用逗号分隔').split(',') if x.strip()],
            'roles': [x.strip() for x in ask('目标岗位，多个用逗号分隔').split(',') if x.strip()]}
    if doc:
        data['product_document'] = doc['path']
        data['product_excerpt_unreviewed'] = doc['excerpt']
        print('资料仅作为人工输入参考，未自动解析或认证其中的主张。')
    result = tp.icp_create(db, data)
    run['state']['icp_id'] = result['icp_id']
    print('ICP 草案：' + json.dumps(result, ensure_ascii=False, indent=2))
    advance(db, run, 'approval')


def approval(db, run):
    identifier = run['state']['icp_id']
    record = tp.row(db, 'SELECT status,data FROM icp_profiles WHERE id=?', (identifier,))
    if record['status'] != 'approved':
        print('请核对产品、国家、行业及岗位：\n' + json.dumps(json.loads(record['data']), ensure_ascii=False, indent=2))
        if not yes('这些信息已由你核实并确认用于搜索吗？'):
            if yes('是否修改 ICP 字段并创建新版本？'):
                data = json.loads(record['data'])
                for key, label in (('supplier', '供应商名称'), ('product', '产品/核心能力'), ('application', '应用场景')):
                    data[key] = ask(f'{label}（回车保持：{data[key]}）', optional=True, default=data[key])
                for key, label in (('countries', '国家'), ('industries', '行业'), ('roles', '岗位')):
                    entered = ask(f'{label}，逗号分隔（回车保持：{", ".join(data[key])}）', optional=True)
                    if entered is not None:
                        data[key] = [x.strip() for x in entered.split(',') if x.strip()]
                replacement = tp.icp_create(db, data)
                run['state']['icp_id'] = replacement['icp_id']
                save(db, run)
                print(f'已创建新 ICP 草案 #{replacement["icp_id"]}；原版本保留。')
            else:
                print('已暂停；旧草案不会用于发现。')
            return False
        reviewer = ask('审批人姓名（本地审计标签，不是身份认证）')
        tp.approve_icp(db, identifier, reviewer)
    advance(db, run, 'discovery')
    return True


def discovery(db, run):
    icp_id = run['state']['icp_id']
    print('可以先联网搜索参考线索，搜索结果不会自动入库。')
    if yes('尝试公开搜索？'):
        try:
            hits = tp.search_candidates(db, icp_id, 10)
            print(json.dumps(hits, ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f'搜索不可用：{exc}。可继续导入自己核实的清单。')
    print('请核实企业身份/官网及来源；可逐家录入，或导入 JSON 数组（格式见 examples/candidates.json）。')
    if not yes('现在添加候选企业？'):
        print('暂无可研究的真实客户；保留进度，下次继续。')
        return False
    mode = ask('输入方式：1 逐家填写 / 2 导入 JSON')
    if mode not in ('1', '2'):
        raise ValueError('请选择 1 或 2')
    data = manual_candidates() if mode == '1' else source_input()
    if data is None:
        print('未提供清单，保留进度。')
        return False
    found = tp.discover(db, icp_id, data)
    ids = list(dict.fromkeys(item['company_id'] for item in found))
    run['state']['company_ids'] = ids
    run['state']['research_index'] = 0
    print(f'已导入 {len(ids)} 家去重候选企业。')
    advance(db, run, 'research')
    return True


def research(db, run):
    state = run['state']
    ids = state['company_ids']
    while state['research_index'] < len(ids):
        cid = ids[state['research_index']]
        company = tp.row(db, 'SELECT * FROM companies WHERE id=?', (cid,))
        print(f'\n研究企业 {state["research_index"] + 1}/{len(ids)}：{company["name"]} / {company["website"]}')
        if yes('抓取该官网首页文本？'):
            try:
                print(json.dumps(tp.fetch_company(db, cid), ensure_ascii=False, indent=2))
            except Exception as exc:
                print(f'无法抓取：{exc}。请人工提供公开证据。')
        print('请核对官网及其他来源。公开信号不代表已准备采购。')
        while yes('添加一条公开证据？'):
            item = {'claim': ask('证据陈述（仅写来源直接展示的内容）'),
                    'excerpt': ask('来源原文摘录'), 'source_url': ask('原始来源 URL'),
                    'source_type': ask('来源类型，例如 official_website / hiring')}
            tp.add_evidence(db, cid, item)
            db.commit()
        evidence = db.execute("SELECT id,claim,excerpt,source_url,source_type FROM evidence_records WHERE company_id=? AND status='observed' AND verification='unverified' AND source_type!='discovery' ORDER BY id", (cid,)).fetchall()
        for item in evidence:
            print(f'待核验 #{item["id"]}: {item["claim"]} | {item["excerpt"][:150]} | {item["source_url"]}')
            if yes('你已打开原始来源并确认该摘录准确？'):
                tp.verify_evidence(db, item['id'], ask('核验人姓名'))
        result = tp.assess(db, cid)
        print('资格判断：' + json.dumps(result, ensure_ascii=False, indent=2))
        if result['decision'] == 'pending_review' and yes('人工审核通过该客户资格？'):
            tp.qualify(db, cid, ask('资格审批人姓名'), 'qualified')
        state['research_index'] += 1
        save(db, run)
        print('该企业研究已保存。')
    advance(db, run, 'outreach')
    return True


def outreach(db, run):
    state = run['state']
    index = state.get('outreach_index', 0)
    for position in range(index, len(state['company_ids'])):
        cid = state['company_ids'][position]
        company = tp.row(db, 'SELECT name,status FROM companies WHERE id=?', (cid,))
        if company['status'] != 'qualified':
            state['outreach_index'] = position + 1
            save(db, run)
            continue
        if not yes(f'为 {company["name"]} 创建待审批触达草稿？'):
            state['outreach_index'] = position + 1
            save(db, run)
            continue
        print('请先确认目标市场营销法规与联系人信息来源；本向导不自动发送邮件。')
        person = tp.contact(db, cid, {'name': ask('联系人姓名', optional=True),
                                      'job_title': ask('岗位名称（不代表实际决策权）'),
                                      'email': ask('邮箱', optional=True), 'source_url': ask('联系人来源 URL')})
        draft = tp.draft(db, cid, person['contact_id'], ask('邮件主题'), ask('邮件正文（只包含已核实事实）'))
        print(f'已保存草稿 #{draft["draft_id"]}，未审批、未发送。联系人的邮箱默认未核验。')
        state['outreach_index'] = position + 1
        save(db, run)
    advance(db, run, 'export')


def export(db, run, folder, *, persist=True):
    target = Path(folder).expanduser().resolve() / f'run-{run["id"]}'
    target.mkdir(parents=True, exist_ok=True)
    entries = [tp.report(db, cid) for cid in run['state'].get('company_ids', [])]
    payload = {'run_id': run['id'], 'icp_id': run['state']['icp_id'], 'generated_at': tp.now(),
               'prospects': entries, 'stats': tp.stats(db)}
    (target / 'result.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with (target / 'prospects.csv').open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(('company_id', 'name', 'country', 'website', 'status', 'score', 'verified_evidence_count', 'next_step'))
        for entry in entries:
            c = entry['company']
            writer.writerow((c['id'], c['name'], c['country'], c['website'], c['status'], c['score'],
                             sum(e['verification'] == 'verified' and e['source_type'] != 'discovery' for e in entry['evidence']), entry['next_step']))
    lines = [f'# 客户开发报告 · 第 {run["id"]} 轮', '', f'生成时间：{payload["generated_at"]}',
             f'ICP ID：{payload["icp_id"]}', '', f'候选企业：{len(entries)}；已人工通过：{sum(e["company"]["status"] == "qualified" for e in entries)}。',
             '公开信号不证明采购意向；以下证据状态请逐项查看。', '']
    for entry in entries:
        c = entry['company']
        qualification = entry['qualification'][-1] if entry['qualification'] else None
        lines.extend((f'## {c["name"]}（#{c["id"]}）', f'- 国家/行业：{c["country"] or "未知"} / {c["industry"] or "未知"}',
                      f'- 官网（待核验）：{c["website"]}', f'- 评分/资格：{c["score"] if c["score"] is not None else "未评分"} / {c["status"]}',
                      f'- 推荐依据：{qualification["rationale"] if qualification else "尚未评估"}',
                      f'- 下一步：{entry["next_step"]}', '- 公开证据：'))
        for ev in entry['evidence']:
            lines.append(f'  - [{ev["status"]}/{ev["verification"]}] {ev["claim"]}；摘录：{ev["excerpt"][:250]}；来源：{ev["source_url"]}')
        drafts = db.execute('SELECT id, status FROM outreach_drafts WHERE company_id=? ORDER BY id', (c['id'],)).fetchall()
        for draft in drafts:
            lines.append(f'- 触达草稿 #{draft["id"]}：{draft["status"]}（向导未发送）')
        lines.append('')
    (target / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    run['state']['output_dir'] = str(target)
    if persist:
        advance(db, run, 'done')
    return target


def run(db, *, new=False, run_id=None, output='outputs'):
    if new and run_id is not None:
        raise ValueError('不能同时指定新建和已有轮次')
    session = create(db) if new else resume(db, run_id)
    if session is None:
        session = create(db) if run_id is None else None
    if session is None:
        raise ValueError('未找到该引导轮次')
    print(f'\nTradePilot 客户开发引导 · 第 {session["id"]} 轮。输入中途可 Ctrl+C 暂停；重新执行开始开发客户即可恢复。')
    while session['stage'] != 'done':
        stage = session['stage']
        print(f'\n[{STAGES.index(stage)+1}/6] {LABELS[STAGES.index(stage)]}')
        try:
            if stage == 'profile': profile(db, session)
            elif stage == 'approval':
                if not approval(db, session): break
            elif stage == 'discovery':
                if not discovery(db, session): break
            elif stage == 'research': research(db, session)
            elif stage == 'outreach': outreach(db, session)
            elif stage == 'export': print(f'已生成交付文件：{export(db, session, output)}')
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            print(f'输入或操作出错：{exc}。进度保留在当前阶段，修正后重新运行。')
            break
        except (KeyboardInterrupt, EOFError):
            print('\n已暂停，重新运行即可恢复。')
            break
    if session['stage'] == 'done':
        print(f'流程完成。报告目录：{session["state"]["output_dir"]}；邮件未由向导发送。')
    return session
