"""One-message-at-a-time chat workflow for Studio Skills. JSON in/out, no stdin prompts."""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

import tradepilot as tp
import wizard

QUESTIONS = {
    'supplier': '你公司的名称是什么？',
    'product': '请介绍要开发海外客户的产品和已确认的核心能力。可直接用文字描述，也可以先在聊天中上传产品资料，让助手提取已确认信息后请你核对。',
    'application': '产品主要用于什么场景？',
    'countries': '希望开发哪些国家或地区？多个请用逗号分开。',
    'industries': '目标客户属于哪些行业？多个请用逗号分开。',
    'roles': '希望联系客户企业的哪些岗位？例如采购经理、工程负责人。多个请用逗号分开。',
    'icp_approval': '这是你的客户画像。请回复「确认」进入找客户，或回复「修改」重新填写；不会擅自扩大产品适用范围。',
    'candidate_choice': '要开始找客户吗？回复「搜索」获取公开搜索参考，或直接发送已核实的企业名称、官网和发现来源 URL；没有资料可回复「暂停」。',
    'candidate_import': '请在聊天中提供已核实的企业名称、官网 URL、发现来源 URL（可附国家、行业、来源摘录）。也可以逐家提供；回复「完成」进入研究。搜索网页标题不能直接当企业事实。',
    'evidence_choice': '需要继续补充该企业的业务证据吗？请提供原始页面 URL、原文摘录、它直接证明了什么；或回复「评估」。公开活动不等于采购意向。',
    'evidence_review': '请打开所列原始页面核对摘录。回复「确认核验」记录人工核验，或「跳过」保持未核验；不要把推测当作事实。',
    'qualification_review': '该客户达到资格待审门槛。回复「通过」或「拒绝」；通过仅表示值得开发，不证明其准备采购。',
    'next_company': '该企业已研究完毕。回复「继续」研究下一家，或「完成」生成报告。',
    'draft_choice': '要为已通过资格的客户准备开发邮件草稿吗？回复「草稿」或「跳过」。不会自动发信。',
    'draft_input': '请提供联系人岗位、信息来源 URL、邮件主题和正文；可附联系人姓名与邮箱。若暂时没有，回复「跳过」。草稿仅保存，不审批、不发送。',
}


def setup(db):
    db.execute('''CREATE TABLE IF NOT EXISTS chat_runs (
      id INTEGER PRIMARY KEY, conversation TEXT NOT NULL UNIQUE, stage TEXT NOT NULL,
      state TEXT NOT NULL, updated_at TEXT NOT NULL)''')
    db.commit()


def get(db, conversation):
    setup(db)
    row = db.execute('SELECT * FROM chat_runs WHERE conversation=?', (conversation,)).fetchone()
    if row is None:
        return None
    return {'id': row['id'], 'conversation': row['conversation'], 'stage': row['stage'], 'state': json.loads(row['state'])}


def store(db, run):
    with db:
        db.execute('UPDATE chat_runs SET stage=?,state=?,updated_at=? WHERE id=?',
                   (run['stage'], json.dumps(run['state'], ensure_ascii=False), tp.now(), run['id']))


def start(db, conversation):
    setup(db)
    existing = get(db, conversation)
    if existing and existing['stage'] != 'done':
        return existing
    if existing:
        with db:
            db.execute('UPDATE chat_runs SET conversation=? WHERE id=?',
                       (f'{conversation}#archived-{existing["id"]}', existing['id']))
    with db:
        identifier = db.execute('INSERT INTO chat_runs(conversation,stage,state,updated_at) VALUES(?,?,?,?)',
                                (conversation, 'supplier', '{}', tp.now())).lastrowid
    return get(db, conversation)


def answer(run, extras=None):
    stage = run['stage']
    result = {'run_id': run['id'], 'stage': stage, 'question': QUESTIONS.get(stage),
              'progress': {'supplier': '1/5 客户画像', 'product': '1/5 客户画像',
                           'application': '1/5 客户画像', 'countries': '1/5 客户画像',
                           'industries': '1/5 客户画像', 'roles': '1/5 客户画像',
                           'icp_approval': '1/5 客户画像', 'candidate_choice': '2/5 企业发现',
                           'candidate_import': '2/5 企业发现', 'evidence_choice': '3/5 客户研究',
                           'evidence_review': '3/5 客户研究', 'qualification_review': '3/5 客户研究',
                           'next_company': '3/5 客户研究', 'draft_choice': '4/5 触达草稿',
                           'draft_input': '4/5 触达草稿', 'done': '5/5 报告交付'}[stage],
              'done': stage == 'done'}
    if extras:
        result.update(extras)
    return result


def next_research(db, run):
    state = run['state']
    pending = [cid for cid in state['company_ids'] if cid not in state.get('reviewed_ids', [])]
    if pending:
        state['current_company_id'] = pending[0]
        run['stage'] = 'evidence_choice'
        company = tp.row(db, 'SELECT name,website FROM companies WHERE id=?', (pending[0],))
        return f'现在研究：{company["name"]}（{company["website"]}）。'
    run['stage'] = 'draft_choice'
    return '企业研究已完成。'


def complete(db, run, output):
    state = run['state']
    # Reuse report export without the interactive wizard.
    export_run = {'id': run['id'], 'stage': 'export', 'state': {'icp_id': state['icp_id'], 'company_ids': state.get('company_ids', [])}}
    folder = wizard.export(db, export_run, output, persist=False)
    state['output_dir'] = str(folder)
    run['stage'] = 'done'
    return f'报告已生成：{folder}。没有自动发信。'


def step(db, conversation, payload, output='outputs'):
    if not isinstance(conversation, str) or not conversation.strip():
        raise ValueError('需要非空的 Studio 会话标识；不同用户不要复用同一标识')
    if not isinstance(payload, dict):
        raise ValueError('输入必须为 JSON 对象')
    action = payload.get('action', 'status')
    if action == 'start':
        run = start(db, conversation)
        return answer(run, {'notice': '从第一步开始；每次只需回答一个问题，可以随时暂停或查看进度。'})
    run = get(db, conversation)
    if run is None:
        raise ValueError('尚未开始；请先发送「开始开发客户」')
    if action == 'status':
        return answer(run)
    if action != 'reply':
        raise ValueError('action 必须是 start/status/reply')
    stage, state = run['stage'], run['state']
    value = payload.get('value')
    if isinstance(value, dict) and stage not in ('candidate_choice', 'candidate_import', 'evidence_choice', 'draft_input'):
        raise ValueError('本步骤请使用文字或确认选项回答')
    notice = None
    if stage in ('supplier', 'product', 'application', 'countries', 'industries', 'roles'):
        if not isinstance(value, str) or not value.strip():
            raise ValueError('请提供具体内容；如不确定可向助手描述现状，不必填写代码或 JSON')
        state[stage] = [x.strip() for x in re.split(r'[,，;；、]', value) if x.strip()] if stage in ('countries', 'industries', 'roles') else value.strip()
        order = ('supplier', 'product', 'application', 'countries', 'industries', 'roles', 'icp_approval')
        run['stage'] = order[order.index(stage)+1]
        if run['stage'] == 'icp_approval':
            result = tp.icp_create(db, state.copy())
            state['icp_id'] = result['icp_id']
            notice = f'客户画像草案：{json.dumps(result, ensure_ascii=False)}'
    elif stage == 'icp_approval':
        if value == '修改':
            run['stage'] = 'supplier'
            notice = '原草案保留；请重新填写，确认后创建新版本。'
        elif value == '确认':
            tp.approve_icp(db, state['icp_id'], 'Studio 用户确认（会话操作；非身份认证）')
            run['stage'] = 'candidate_choice'
            notice = '客户画像已确认。'
        else:
            raise ValueError('请回复「确认」或「修改」')
    elif stage == 'candidate_choice':
        if value == '暂停':
            notice = '已保存进度。下次发送「继续开发客户」即可接着做。'
        elif value == '搜索':
            hits = tp.search_candidates(db, state['icp_id'], 10)
            run['stage'] = 'candidate_import'
            notice = '搜索命中仅供参考，不自动入库。' + json.dumps(hits, ensure_ascii=False)
        else:
            run['stage'] = 'candidate_import'
            if not isinstance(value, dict):
                notice = '请提供企业名称、核实过的官网和发现来源 URL。'
    if run['stage'] == 'candidate_import' and stage in ('candidate_import', 'candidate_choice') and value != '搜索' and value != '暂停':
        if value == '完成':
            if not state.get('company_ids'):
                raise ValueError('尚无已核实企业；请先提供一家企业的名称、官网和发现来源')
            notice = next_research(db, run)
        elif isinstance(value, dict):
            item = {k: value.get(k) for k in ('name', 'website', 'source_url', 'country', 'industry', 'source_excerpt')}
            found = tp.discover(db, state['icp_id'], [item])
            ids = state.setdefault('company_ids', [])
            if found[0]['company_id'] not in ids:
                ids.append(found[0]['company_id'])
            notice = f'已保存企业 #{found[0]["company_id"]}，目前 {len(ids)} 家；可以再提供一家，或回复「完成」。'
        elif notice is None:
            raise ValueError('请提供企业信息，或回复「完成」')
    elif stage == 'evidence_choice':
        cid = state['current_company_id']
        if value == '评估':
            result = tp.assess(db, cid)
            if result['decision'] == 'pending_review':
                run['stage'] = 'qualification_review'
            else:
                state.setdefault('reviewed_ids', []).append(cid)
                run['stage'] = 'next_company'
            notice = json.dumps(result, ensure_ascii=False)
        elif isinstance(value, dict):
            tp.add_evidence(db, cid, value)
            db.commit()
            item = db.execute('SELECT id,claim,source_url FROM evidence_records WHERE company_id=? ORDER BY id DESC LIMIT 1', (cid,)).fetchone()
            state['evidence_id'] = item['id']
            run['stage'] = 'evidence_review'
            notice = f'待核验证据 #{item["id"]}：{item["claim"]}，来源：{item["source_url"]}'
        else:
            raise ValueError('请提供证据的 claim/excerpt/source_url/source_type，或回复「评估」')
    elif stage == 'evidence_review':
        if value == '确认核验':
            tp.verify_evidence(db, state['evidence_id'], 'Studio 用户核验（会话操作；非身份认证）')
            notice = '已记录人工核验；公开信号不等于采购意向。'
        elif value == '跳过':
            notice = '证据保持未核验，不计入资格评分。'
        else:
            raise ValueError('请回复「确认核验」或「跳过」')
        run['stage'] = 'evidence_choice'
    elif stage == 'qualification_review':
        if value not in ('通过', '拒绝'):
            raise ValueError('请回复「通过」或「拒绝」')
        cid = state['current_company_id']
        tp.qualify(db, cid, 'Studio 用户审批（会话操作；非身份认证）', 'qualified' if value == '通过' else 'rejected')
        state.setdefault('reviewed_ids', []).append(cid)
        run['stage'] = 'next_company'
        notice = f'企业 #{cid} 资格：{value}。'
    elif stage == 'next_company':
        if value not in ('继续', '完成'):
            raise ValueError('请回复「继续」或「完成」')
        if value == '完成':
            remaining = [cid for cid in state['company_ids'] if cid not in state.get('reviewed_ids', [])]
            if remaining:
                notice = f'还有 {len(remaining)} 家未研究，将在报告中标记未完成；'
                run['stage'] = 'draft_choice'
            else:
                run['stage'] = 'draft_choice'
                notice = '所有企业已研究；'
        if run['stage'] != 'draft_choice':
            notice = next_research(db, run)
    elif stage == 'draft_choice':
        if value == '草稿':
            qualified = db.execute("SELECT id,name FROM companies WHERE icp_id=? AND status='qualified'", (state['icp_id'],)).fetchall()
            if not qualified:
                notice = '暂无已通过资格企业，不能生成触达草稿。'
            else:
                run['stage'] = 'draft_input'
                notice = '可选企业：' + ', '.join(f'#{x["id"]} {x["name"]}' for x in qualified)
        elif value == '跳过':
            notice = complete(db, run, output)
        else:
            raise ValueError('请回复「草稿」或「跳过」')
    elif stage == 'draft_input':
        if value == '跳过':
            notice = complete(db, run, output)
        elif isinstance(value, dict):
            cid = value.get('company_id')
            if not isinstance(cid, int) or cid not in state.get('company_ids', []):
                raise ValueError('请指定本轮已研究的 company_id')
            person = tp.contact(db, cid, value)
            draft = tp.draft(db, cid, person['contact_id'], value.get('subject'), value.get('body'))
            notice = f'草稿 #{draft["draft_id"]} 已保存，未审批也未发送。' + complete(db, run, output)
        else:
            raise ValueError('请提供 company_id、job_title、source_url、subject、body 等信息，或回复「跳过」')
    elif stage == 'done':
        notice = '此轮已完成。发送「开始开发客户」可以新建一轮。'
    store(db, run)
    return answer(run, {'notice': notice, 'output_dir': state.get('output_dir')})


def main():
    parser = argparse.ArgumentParser(description='Studio 聊天适配器；输入输出 JSON，用户无需运行此脚本')
    parser.add_argument('--db', default='tradepilot.db')
    parser.add_argument('--conversation', required=True)
    parser.add_argument('--output', default='outputs')
    parser.add_argument('--json', help='JSON 对象字符串，勿放个人资料于命令历史；推荐 --input')
    parser.add_argument('--input', help='JSON 输入文件路径')
    args = parser.parse_args()
    try:
        if bool(args.json) == bool(args.input):
            raise ValueError('必须且只能使用 --json 或 --input')
        payload = json.loads(Path(args.input).read_text(encoding='utf-8')) if args.input else json.loads(args.json)
        with tp.connect(args.db) as db:
            print(json.dumps(step(db, args.conversation, payload, args.output), ensure_ascii=False, indent=2))
    except (ValueError, OSError, sqlite3.Error, KeyError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
