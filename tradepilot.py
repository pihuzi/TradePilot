#!/usr/bin/env python3
"""Evidence-first overseas B2B prospecting CLI. Standard library only."""
import argparse
import ipaddress

import json
import os
import re
import socket
import smtplib
import sqlite3
import ssl
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.message import EmailMessage
from html.parser import HTMLParser



def now():
    return datetime.now(timezone.utc).isoformat()


def fail(message):
    raise ValueError(message)


def require(value, label):
    if not value or (isinstance(value, str) and not value.strip()):
        fail(f"缺少必填字段: {label}")
    return value


def url(value):
    p = urllib.parse.urlparse(require(value, 'source_url'))
    if p.scheme not in ('http', 'https') or not p.hostname:
        fail('URL 必须为有效的 http(s) 地址')
    return value


def domain(value):
    host = urllib.parse.urlparse(url(value)).hostname.lower()
    return host[4:] if host.startswith('www.') else host


def public_url(value):
    value = url(value)
    host = urllib.parse.urlparse(value).hostname
    if host in ('localhost',) or host.endswith('.local'):
        fail('拒绝访问本地地址')
    try:
        addresses = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        fail(f'域名无法解析: {exc}')
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        fail('拒绝访问非公网地址')
    return value


def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def dump(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS suppliers(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS icp_profiles(id INTEGER PRIMARY KEY, supplier_id INTEGER NOT NULL REFERENCES suppliers(id), version INTEGER NOT NULL, data TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL, UNIQUE(supplier_id,version));
CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY, icp_id INTEGER NOT NULL REFERENCES icp_profiles(id), name TEXT NOT NULL, country TEXT, website TEXT NOT NULL, domain TEXT NOT NULL, industry TEXT, description TEXT, status TEXT NOT NULL DEFAULT 'discovered', score INTEGER, created_at TEXT NOT NULL, UNIQUE(icp_id,domain));
CREATE TABLE IF NOT EXISTS evidence_records(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), claim TEXT NOT NULL, excerpt TEXT NOT NULL, source_url TEXT NOT NULL, source_type TEXT NOT NULL, published_at TEXT, collected_at TEXT NOT NULL, status TEXT NOT NULL, confidence TEXT NOT NULL, verification TEXT NOT NULL, UNIQUE(company_id,source_url,excerpt));
CREATE TABLE IF NOT EXISTS contacts(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), name TEXT, job_title TEXT NOT NULL, email TEXT, source_url TEXT NOT NULL, verification TEXT NOT NULL DEFAULT 'unverified', do_not_contact INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS qualification_records(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), score INTEGER NOT NULL, rationale TEXT NOT NULL, decision TEXT NOT NULL, reviewer TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outreach_drafts(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contact_id INTEGER NOT NULL REFERENCES contacts(id), subject TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approval_records(id INTEGER PRIMARY KEY, object_type TEXT NOT NULL, object_id INTEGER NOT NULL, decision TEXT NOT NULL, reviewer TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS email_activities(id INTEGER PRIMARY KEY, draft_id INTEGER NOT NULL REFERENCES outreach_drafts(id), status TEXT NOT NULL, detail TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS followup_tasks(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), note TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conversion_events(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), event TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS data_source_logs(id INTEGER PRIMARY KEY, company_id INTEGER REFERENCES companies(id), provider TEXT NOT NULL, operation TEXT NOT NULL, cost REAL NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cost_records(id INTEGER PRIMARY KEY, company_id INTEGER REFERENCES companies(id), provider TEXT NOT NULL, amount REAL NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS system_feedback(id INTEGER PRIMARY KEY, company_id INTEGER REFERENCES companies(id), note TEXT NOT NULL, created_at TEXT NOT NULL);
'''


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def row(db, sql, args=()):
    result = db.execute(sql, args).fetchone()
    if result is None:
        fail('记录不存在或状态不允许该操作')
    return result


def icp_create(db, data):
    for key in ('supplier', 'product', 'application', 'countries', 'industries', 'roles'):
        require(data.get(key), key)
    for key in ('countries', 'industries', 'roles'):
        if not isinstance(data[key], list) or not all(isinstance(x, str) and x.strip() for x in data[key]):
            fail(f'{key} 必须是非空字符串数组')
    name = data['supplier'].strip()
    s = db.execute('SELECT id FROM suppliers WHERE name=?', (name,)).fetchone()
    sid = s['id'] if s else db.execute('INSERT INTO suppliers(name) VALUES(?)', (name,)).lastrowid
    version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM icp_profiles WHERE supplier_id=?', (sid,)).fetchone()[0]
    identifier = db.execute('INSERT INTO icp_profiles(supplier_id,version,data,created_at) VALUES(?,?,?,?)', (sid, version, json.dumps(data, ensure_ascii=False), now())).lastrowid
    db.commit()
    return {'icp_id': identifier, 'version': version, 'status': 'draft', 'product_profile': {'product': data['product'], 'application': data['application']}, 'market_profile': {'countries': data['countries'], 'industries': data['industries']}, 'target_role_profile': data['roles']}


def approve_icp(db, identifier, reviewer):
    rec = row(db, 'SELECT * FROM icp_profiles WHERE id=?', (identifier,))
    if rec['status'] != 'draft':
        fail('ICP 不是待审批状态')
    db.execute("UPDATE icp_profiles SET status='approved' WHERE id=?", (identifier,))
    db.execute('INSERT INTO approval_records(object_type,object_id,decision,reviewer,created_at) VALUES(?,?,?,?,?)', ('icp', identifier, 'approved', require(reviewer, 'reviewer'), now()))
    db.commit()
    return {'icp_id': identifier, 'status': 'approved'}


def add_evidence(db, company_id, item):
    source = url(item.get('source_url'))
    excerpt = require(item.get('excerpt'), 'excerpt').strip()
    status = item.get('status', 'observed')
    if status not in ('observed', 'inferred', 'unknown', 'contradicted'):
        fail('无效证据状态')
    if item.get('verification', 'unverified') != 'unverified':
        fail('证据只能通过 evidence-verify 人工核验')
    verification = 'unverified'
    confidence = item.get('confidence', 'low')
    if confidence not in ('low', 'medium', 'high'):
        fail('无效置信度')
    db.execute('''INSERT OR IGNORE INTO evidence_records(company_id,claim,excerpt,source_url,source_type,published_at,collected_at,status,confidence,verification) VALUES(?,?,?,?,?,?,?,?,?,?)''', (company_id, require(item.get('claim'), 'claim'), excerpt, source, item.get('source_type', 'public_web'), item.get('published_at'), now(), status, confidence, verification))


def verify_evidence(db, identifier, reviewer):
    e = row(db, 'SELECT status FROM evidence_records WHERE id=?', (identifier,))
    if e['status'] != 'observed':
        fail('仅直接观察到的证据可核验；推断不等于事实')
    with db:
        db.execute("UPDATE evidence_records SET verification='verified' WHERE id=?", (identifier,))
        db.execute('INSERT INTO approval_records(object_type,object_id,decision,reviewer,created_at) VALUES(?,?,?,?,?)', ('evidence', identifier, 'verified', require(reviewer, 'reviewer'), now()))
    return {'evidence_id': identifier, 'verification': 'verified'}


def discover(db, icp_id, companies):
    rec = row(db, 'SELECT status FROM icp_profiles WHERE id=?', (icp_id,))
    if rec['status'] != 'approved':
        fail('ICP 必须先由人工审批')
    if not isinstance(companies, list):
        fail('候选企业输入必须是数组')
    results = []
    with db:
        for item in companies:
            website = url(item.get('website'))
            source = url(item.get('source_url'))
            name = require(item.get('name'), 'name').strip()
            host = domain(website)
            db.execute('''INSERT INTO companies(icp_id,name,country,website,domain,industry,description,created_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(icp_id,domain) DO NOTHING''', (icp_id, name, item.get('country'), website, host, item.get('industry'), item.get('description'), now()))
            company = row(db, 'SELECT id FROM companies WHERE icp_id=? AND domain=?', (icp_id, host))
            if not db.execute("SELECT 1 FROM evidence_records WHERE company_id=? AND source_url=? AND source_type='discovery'", (company['id'], source)).fetchone():
                add_evidence(db, company['id'], {'claim': '公开来源提及此候选企业（尚未验证业务匹配）', 'excerpt': item.get('source_excerpt') or name, 'source_url': source, 'source_type': 'discovery', 'status': 'observed', 'confidence': 'low'})
            results.append({'company_id': company['id'], 'domain': host, 'source_url': source})
    return results


class PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'nav', 'footer'):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'nav', 'footer') and self.skip:
            self.skip -= 1

    def handle_data(self, text):
        if not self.skip and text.strip():
            self.parts.append(text.strip())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        fail('自动重定向已禁用，请人工核验目标地址')


def fetch_company(db, company_id):
    company = row(db, 'SELECT * FROM companies WHERE id=?', (company_id,))
    request = urllib.request.Request(public_url(company['website']), headers={'User-Agent': 'TradePilot/1.0 (+evidence research)'})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=12) as response:
        final_url = response.geturl()
        public_url(final_url)
        if domain(final_url) != company['domain']:
            fail('官网重定向到其他域名，请人工核实')
        if 'html' not in response.headers.get('Content-Type', '').lower():
            fail('官网返回非 HTML 内容')
        content = response.read(500_000)
    parser = PageText()
    parser.feed(content.decode('utf-8', errors='replace'))
    text = re.sub(r'\s+', ' ', ' '.join(parser.parts))[:15000]
    if not text:
        fail('官网无可读取文本')
    # Store an excerpt as observed webpage text, never as an inferred buying signal.
    with db:
        add_evidence(db, company_id, {'claim': '官网页面公开文本（不等于采购意向）', 'excerpt': text[:500], 'source_url': final_url, 'source_type': 'official_website', 'confidence': 'medium'})
    return {'company_id': company_id, 'source_url': final_url, 'excerpt': text[:500], 'note': '未自动推断采购意向；请核验并补充有针对性的证据'}


def search_candidates(db, icp_id, limit):
    icp = row(db, 'SELECT * FROM icp_profiles WHERE id=?', (icp_id,))
    if icp['status'] != 'approved':
        fail('ICP 必须先由人工审批')
    if not 1 <= limit <= 50:
        fail('limit 必须在 1 至 50 之间')
    data = json.loads(icp['data'])
    terms = f'"{data["industries"][0]}" "{data["countries"][0]}" manufacturer company -dictionary -translation'
    endpoint = 'https://www.bing.com/search?format=rss&q=' + urllib.parse.quote(terms)
    with urllib.request.build_opener(NoRedirect).open(urllib.request.Request(endpoint, headers={'User-Agent': 'TradePilot/1.0'}), timeout=15) as response:
        raw = response.read(500_000)
    root = ET.fromstring(raw)
    candidates = []
    for item in root.findall('.//item'):
        link = item.findtext('link')
        title = item.findtext('title')
        excerpt = item.findtext('description') or title
        if not link or not title:
            continue
        try:
            host = domain(link)
            if host == 'bing.com' or host.endswith('.bing.com') or host in ('linkedin.com', 'dictionary.cambridge.org', 'en.wikipedia.org') or host.startswith('dictionary.'):
                continue
        except ValueError:
            continue
        candidates.append({'title': title, 'url': link, 'snippet': excerpt, 'status': 'unverified_search_hit'})
        if len(candidates) >= limit:
            break
    return {'query': terms, 'warning': '搜索结果仅供人工甄别；不会写入企业库。核实公司身份、官网与来源后使用 discover 导入。', 'search_hits': candidates}


def assess(db, company_id):
    company = row(db, 'SELECT * FROM companies WHERE id=?', (company_id,))
    icp = json.loads(row(db, 'SELECT data FROM icp_profiles WHERE id=?', (company['icp_id'],))['data'])
    evidence = db.execute('SELECT * FROM evidence_records WHERE company_id=? ORDER BY id', (company_id,)).fetchall()
    country = bool(company['country'] and company['country'].casefold() in [x.casefold() for x in icp['countries']])
    industry = bool(company['industry'] and company['industry'].casefold() in [x.casefold() for x in icp['industries']])
    verified = [e for e in evidence if e['status'] == 'observed' and e['verification'] == 'verified' and e['source_type'] != 'discovery']
    score = 25 * country + 25 * industry + min(40, len(verified) * 20) + (10 if any(e['source_type'] == 'official_website' for e in verified) else 0)
    decision = 'pending_review' if score >= 60 and verified else 'researching'
    rationale = f"国家匹配={country}; 行业匹配={industry}; 已核验非发现证据={len(verified)}。公开信号不证明采购意向。"
    with db:
        db.execute('UPDATE companies SET score=?,status=? WHERE id=?', (score, decision, company_id))
        db.execute('INSERT INTO qualification_records(company_id,score,rationale,decision,created_at) VALUES(?,?,?,?,?)', (company_id, score, rationale, decision, now()))
    return {'company_id': company_id, 'icp_id': company['icp_id'], 'score': score, 'decision': decision, 'rationale': rationale, 'evidence_ids': [e['id'] for e in verified], 'next_step': '人工审核资格' if decision == 'pending_review' else '补充并核验非发现来源证据'}


def qualify(db, company_id, reviewer, decision):
    company = row(db, 'SELECT status,score FROM companies WHERE id=?', (company_id,))
    if company['status'] != 'pending_review':
        fail('仅待审核企业可人工确认资格')
    if decision not in ('qualified', 'rejected'):
        fail('decision 必须是 qualified 或 rejected')
    with db:
        db.execute('UPDATE companies SET status=? WHERE id=?', (decision, company_id))
        db.execute('INSERT INTO approval_records(object_type,object_id,decision,reviewer,created_at) VALUES(?,?,?,?,?)', ('qualification', company_id, decision, require(reviewer, 'reviewer'), now()))
    return {'company_id': company_id, 'status': decision}


def contact(db, company_id, data):
    row(db, 'SELECT id FROM companies WHERE id=?', (company_id,))
    source = url(data.get('source_url'))
    title = require(data.get('job_title'), 'job_title')
    email = data.get('email')
    if email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        fail('邮箱格式无效')
    with db:
        identifier = db.execute('INSERT INTO contacts(company_id,name,job_title,email,source_url) VALUES(?,?,?,?,?)', (company_id, data.get('name'), title, email, source)).lastrowid
    return {'contact_id': identifier, 'verification': 'unverified', 'note': '岗位不等于采购决策权限；发送前需人工核验地址及合规性'}


def verify_contact(db, identifier, reviewer):
    person = row(db, 'SELECT email,do_not_contact FROM contacts WHERE id=?', (identifier,))
    if not person['email'] or person['do_not_contact']:
        fail('联系人无邮箱或已停止联系')
    with db:
        db.execute("UPDATE contacts SET verification='verified' WHERE id=?", (identifier,))
        db.execute('INSERT INTO approval_records(object_type,object_id,decision,reviewer,created_at) VALUES(?,?,?,?,?)', ('contact', identifier, 'verified', require(reviewer, 'reviewer'), now()))
    return {'contact_id': identifier, 'verification': 'verified', 'note': '人工核验记录不等于技术邮箱投递验证'}


def draft(db, company_id, contact_id, subject, body):
    company = row(db, 'SELECT * FROM companies WHERE id=?', (company_id,))
    if company['status'] != 'qualified':
        fail('只有人工确认资格的企业可以生成触达草稿')
    person = row(db, 'SELECT * FROM contacts WHERE id=? AND company_id=?', (contact_id, company_id))
    if person['do_not_contact']:
        fail('联系人已停止联系')
    with db:
        identifier = db.execute('INSERT INTO outreach_drafts(company_id,contact_id,subject,body,created_at) VALUES(?,?,?,?,?)', (company_id, contact_id, require(subject, 'subject'), require(body, 'body'), now())).lastrowid
    return {'draft_id': identifier, 'status': 'pending', 'note': '正文由用户提供/审核，系统不自动编造企业事实'}


def approve_draft(db, identifier, reviewer):
    item = row(db, '''SELECT d.*, c.do_not_contact, x.status AS company_status FROM outreach_drafts d JOIN contacts c ON c.id=d.contact_id JOIN companies x ON x.id=d.company_id WHERE d.id=?''', (identifier,))
    if item['status'] != 'pending' or item['do_not_contact'] or item['company_status'] != 'qualified':
        fail('草稿不可审批')
    with db:
        db.execute("UPDATE outreach_drafts SET status='approved' WHERE id=?", (identifier,))
        db.execute('INSERT INTO approval_records(object_type,object_id,decision,reviewer,created_at) VALUES(?,?,?,?,?)', ('outreach', identifier, 'approved', require(reviewer, 'reviewer'), now()))
    return {'draft_id': identifier, 'status': 'approved'}


def send(db, identifier, daily_limit):
    item = row(db, '''SELECT d.*, c.email, c.verification AS contact_verification, c.do_not_contact, x.status AS company_status FROM outreach_drafts d JOIN contacts c ON c.id=d.contact_id JOIN companies x ON x.id=d.company_id WHERE d.id=?''', (identifier,))
    if item['status'] != 'approved' or item['company_status'] != 'qualified' or item['do_not_contact'] or not item['email'] or item['contact_verification'] != 'verified':
        fail('发送条件未满足（审批、资格、邮箱或停止联系）')
    if not db.execute("SELECT 1 FROM approval_records WHERE object_type='outreach' AND object_id=? AND decision='approved'", (identifier,)).fetchone():
        fail('缺少人工审批记录')
    count = db.execute("SELECT COUNT(*) FROM email_activities WHERE status='sent' AND created_at>=?", (datetime.now(timezone.utc).date().isoformat(),)).fetchone()[0]
    if count >= daily_limit:
        fail('已达到每日发送上限')
    if os.environ.get('TRADEPILOT_STOP') == '1':
        fail('全局停止开关已启用')
    for key in ('SMTP_HOST', 'SMTP_FROM'):
        require(os.environ.get(key), key)
    msg = EmailMessage()
    msg['From'] = os.environ['SMTP_FROM']
    msg['To'] = item['email']
    msg['Subject'] = item['subject']
    msg.set_content(item['body'])
    host = os.environ['SMTP_HOST']
    port = int(os.environ.get('SMTP_PORT', '465'))
    with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=20) as smtp:
        if os.environ.get('SMTP_USER'):
            smtp.login(os.environ['SMTP_USER'], require(os.environ.get('SMTP_PASSWORD'), 'SMTP_PASSWORD'))
        smtp.send_message(msg)
    with db:
        db.execute("UPDATE outreach_drafts SET status='sent' WHERE id=?", (identifier,))
        db.execute('INSERT INTO email_activities(draft_id,status,detail,created_at) VALUES(?,?,?,?)', (identifier, 'sent', 'SMTP accepted; delivery not guaranteed', now()))
    return {'draft_id': identifier, 'status': 'sent', 'note': 'SMTP 接受邮件，不代表实际送达'}


def feedback(db, company_id, event, note):
    row(db, 'SELECT id FROM companies WHERE id=?', (company_id,))
    if event not in ('reply_positive', 'reply_negative', 'opportunity', 'quote', 'won', 'lost', 'unsubscribe', 'bounce'):
        fail('无效反馈事件')
    with db:
        db.execute('INSERT INTO conversion_events(company_id,event,note,created_at) VALUES(?,?,?,?)', (company_id, event, note, now()))
        db.execute('INSERT INTO system_feedback(company_id,note,created_at) VALUES(?,?,?)', (company_id, f'{event}: {note}', now()))
        if event in ('unsubscribe', 'reply_negative', 'bounce'):
            db.execute('UPDATE contacts SET do_not_contact=1 WHERE company_id=?', (company_id,))
        if event == 'reply_positive':
            db.execute('INSERT INTO followup_tasks(company_id,note,created_at) VALUES(?,?,?)', (company_id, '人工处理正向回复并安排跟进', now()))
    return {'company_id': company_id, 'event': event, 'stopped': event in ('unsubscribe', 'reply_negative', 'bounce')}


def enrichment(db, company_id, provider, operation, cost, budget, approved):
    company = row(db, 'SELECT status,score FROM companies WHERE id=?', (company_id,))
    if company['status'] != 'qualified' or company['score'] < 60:
        fail('付费查询仅允许高匹配且已审核客户')
    if operation not in ('company', 'contact', 'email_verify') or cost < 0 or budget < 0:
        fail('无效查询类型或成本')
    if cost > budget or not require(approved, 'reviewer'):
        fail('费用超出预算或未获得人工审批')
    # No provider credentials or API adapter configured: record authorization, not a fictitious successful lookup.
    with db:
        db.execute('INSERT INTO approval_records(object_type,object_id,decision,reviewer,created_at) VALUES(?,?,?,?,?)', ('enrichment', company_id, 'approved', approved, now()))
        db.execute('INSERT INTO data_source_logs(company_id,provider,operation,cost,result,created_at) VALUES(?,?,?,?,?,?)', (company_id, provider, operation, 0, 'authorized_not_executed', now()))
    return {'status': 'authorized_not_executed', 'estimated_cost': cost, 'actual_cost': 0, 'note': '尚未接入供应商 API，未执行付费查询'}


def report(db, company_id):
    c = row(db, 'SELECT * FROM companies WHERE id=?', (company_id,))
    ev = [dict(x) for x in db.execute('SELECT * FROM evidence_records WHERE company_id=?', (company_id,))]
    people = [dict(x) for x in db.execute('SELECT * FROM contacts WHERE company_id=?', (company_id,))]
    for person in people:
        person['email'] = person['email'] if person['verification'] == 'verified' else None
    return {'company': dict(c), 'evidence': ev, 'contacts': people, 'qualification': [dict(x) for x in db.execute('SELECT * FROM qualification_records WHERE company_id=?', (company_id,))], 'next_step': '可准备人工审查的触达草稿' if c['status'] == 'qualified' else '核实官网与业务证据，完成资格审批'}


def stats(db):
    counts = {name: db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0] for name in ('companies', 'evidence_records', 'contacts', 'email_activities')}
    counts['qualified'] = db.execute("SELECT COUNT(*) FROM companies WHERE status='qualified'").fetchone()[0]
    counts['sent'] = db.execute("SELECT COUNT(*) FROM email_activities WHERE status='sent'").fetchone()[0]
    counts['positive_replies'] = db.execute("SELECT COUNT(*) FROM conversion_events WHERE event='reply_positive'").fetchone()[0]
    counts['opportunities'] = db.execute("SELECT COUNT(DISTINCT company_id) FROM conversion_events WHERE event='opportunity'").fetchone()[0]
    counts['spent'] = db.execute('SELECT COALESCE(SUM(amount),0) FROM cost_records').fetchone()[0]
    counts['reply_rate'] = round(counts['positive_replies'] / counts['sent'], 4) if counts['sent'] else None
    counts['source_breakdown'] = [dict(x) for x in db.execute('SELECT source_type,COUNT(*) AS count FROM evidence_records GROUP BY source_type')]
    counts['optimization_suggestion'] = '样本不足时仅提供建议，不自动修改 ICP；人工复核失效原因和各来源有效客户占比。'
    return counts


def main():
    parser = argparse.ArgumentParser(description='TradePilot 证据驱动海外 B2B 客户开发（无独立 Web UI）')
    parser.add_argument('--db', default='tradepilot.db', help='SQLite 路径')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('开始开发客户', aliases=['start'], help='启动/恢复交互式引导')
    p.add_argument('--new', action='store_true', help='新建一轮（默认恢复上次未完成轮次）')
    p.add_argument('--run-id', type=int, help='恢复指定轮次')
    p.add_argument('--output', default='outputs', help='报告输出目录')
    p = sub.add_parser('icp-create'); p.add_argument('file')
    p = sub.add_parser('icp-approve'); p.add_argument('id', type=int); p.add_argument('--reviewer', required=True)
    p = sub.add_parser('discover'); p.add_argument('icp_id', type=int); p.add_argument('file')
    p = sub.add_parser('search'); p.add_argument('icp_id', type=int); p.add_argument('--limit', type=int, default=10)
    p = sub.add_parser('fetch'); p.add_argument('company_id', type=int)
    p = sub.add_parser('evidence-add'); p.add_argument('company_id', type=int); p.add_argument('file')
    p = sub.add_parser('evidence-verify'); p.add_argument('id', type=int); p.add_argument('--reviewer', required=True)
    p = sub.add_parser('assess'); p.add_argument('company_id', type=int)
    p = sub.add_parser('qualify'); p.add_argument('company_id', type=int); p.add_argument('decision', choices=['qualified', 'rejected']); p.add_argument('--reviewer', required=True)
    p = sub.add_parser('contact-add'); p.add_argument('company_id', type=int); p.add_argument('file')
    p = sub.add_parser('contact-verify'); p.add_argument('id', type=int); p.add_argument('--reviewer', required=True)
    p = sub.add_parser('draft'); p.add_argument('company_id', type=int); p.add_argument('contact_id', type=int); p.add_argument('file')
    p = sub.add_parser('draft-approve'); p.add_argument('id', type=int); p.add_argument('--reviewer', required=True)
    p = sub.add_parser('send'); p.add_argument('id', type=int); p.add_argument('--daily-limit', type=int, default=20)
    p = sub.add_parser('feedback'); p.add_argument('company_id', type=int); p.add_argument('event'); p.add_argument('--note', default='')
    p = sub.add_parser('enrich-authorize'); p.add_argument('company_id', type=int); p.add_argument('provider'); p.add_argument('operation'); p.add_argument('--cost', type=float, required=True); p.add_argument('--budget', type=float, required=True); p.add_argument('--reviewer', required=True)
    p = sub.add_parser('report'); p.add_argument('company_id', type=int)
    sub.add_parser('stats')
    args = parser.parse_args()
    try:
        with connect(args.db) as db:
            cmd = args.command
            if cmd in ('开始开发客户', 'start'):
                from wizard import run
                run(db, new=args.new, run_id=args.run_id, output=args.output)
                return
            if cmd == 'icp-create': result = icp_create(db, load(args.file))
            elif cmd == 'icp-approve': result = approve_icp(db, args.id, args.reviewer)
            elif cmd == 'discover': result = discover(db, args.icp_id, load(args.file))
            elif cmd == 'search': result = search_candidates(db, args.icp_id, args.limit)
            elif cmd == 'fetch': result = fetch_company(db, args.company_id)
            elif cmd == 'evidence-add':
                row(db, 'SELECT id FROM companies WHERE id=?', (args.company_id,))
                with db: add_evidence(db, args.company_id, load(args.file))
                result = {'company_id': args.company_id, 'status': 'recorded'}
            elif cmd == 'evidence-verify':
                result = verify_evidence(db, args.id, args.reviewer)
            elif cmd == 'assess': result = assess(db, args.company_id)
            elif cmd == 'qualify': result = qualify(db, args.company_id, args.reviewer, args.decision)
            elif cmd == 'contact-add': result = contact(db, args.company_id, load(args.file))
            elif cmd == 'contact-verify': result = verify_contact(db, args.id, args.reviewer)
            elif cmd == 'draft':
                data = load(args.file)
                result = draft(db, args.company_id, args.contact_id, data.get('subject'), data.get('body'))
            elif cmd == 'draft-approve': result = approve_draft(db, args.id, args.reviewer)
            elif cmd == 'send': result = send(db, args.id, args.daily_limit)
            elif cmd == 'feedback': result = feedback(db, args.company_id, args.event, args.note)
            elif cmd == 'enrich-authorize': result = enrichment(db, args.company_id, args.provider, args.operation, args.cost, args.budget, args.reviewer)
            elif cmd == 'report': result = report(db, args.company_id)
            else: result = stats(db)
            dump(result)
    except (ValueError, KeyError, OSError, sqlite3.Error, ET.ParseError) as exc:
        print(f'错误: {exc}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
