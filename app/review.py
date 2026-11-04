"""Revision-bound local workflow policy. Synthetic demo, single server worker."""
import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from app.connection import ConnectionError
from app.extraction import ExtractedInvoice, financial_issues, critical
from app.extraction_runs import ExtractionRuns

DEMO_SUPPLIERS = {
    'Alder Packing Supplies Ltd': ('alder-packing', 'PORTFOLIO-ALDER-001', 'accounts@alder.example'),
    'Bracken Industrial Components Ltd': ('bracken-industrial', 'PORTFOLIO-BRACKEN-001', 'accounts@bracken.example'),
    'Cedar Facilities Services Ltd': ('cedar-facilities', 'PORTFOLIO-CEDAR-001', 'accounts@cedar.example'),
    'Dovetail Trade Supplies Ltd': ('dovetail-trade', 'PORTFOLIO-DOVETAIL-001', 'accounts@dovetail.example'),
}
DEFAULT_MAPPING = {'supplier': 'Alder Packing Supplies Ltd', 'supplier_key': 'alder-packing',
    'contact_number': 'PORTFOLIO-ALDER-001', 'email': 'accounts@alder.example',
    'account_code': '325', 'tax_type': 'INPUT2', 'tax_rate': '0.20'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class ReviewError(ConnectionError):
    pass


class ReviewStore:
    def __init__(self, directory):
        self.root = Path(directory)
        self.directory = self.root / 'review'
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.path = self.directory / 'workflow.sqlite3'
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS policy(tenant TEXT PRIMARY KEY, mode TEXT NOT NULL, epoch INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS mappings(tenant TEXT NOT NULL, supplier TEXT NOT NULL,
                    value TEXT NOT NULL, PRIMARY KEY(tenant,supplier));
                CREATE TABLE IF NOT EXISTS cases(tenant TEXT NOT NULL, id TEXT NOT NULL,
                    revision INTEGER NOT NULL, filename TEXT NOT NULL, source_sha256 TEXT NOT NULL,
                    candidate TEXT NOT NULL, unresolved TEXT NOT NULL, approval TEXT, outcome TEXT,
                    created REAL NOT NULL, updated REAL NOT NULL, PRIMARY KEY(tenant,id));
                CREATE TABLE IF NOT EXISTS revisions(tenant TEXT NOT NULL, case_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, candidate TEXT NOT NULL, unresolved TEXT NOT NULL,
                    reason TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(tenant,case_id,revision));
                CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant TEXT NOT NULL,case_id TEXT,kind TEXT NOT NULL,detail TEXT NOT NULL,created REAL NOT NULL);
            ''')
        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def gate(self):
        """Policy/edit/write exclusion. Never block an event loop waiting for a lock."""
        import fcntl
        fd = os.open(self.directory / 'workflow.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ReviewError('Another workflow operation is active. Wait, refresh and inspect the result.') from None
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def event(self, db, tenant, kind, detail, case_id=None):
        db.execute('INSERT INTO audit(tenant,case_id,kind,detail,created) VALUES(?,?,?,?,?)',
            (tenant, case_id, kind, json.dumps(detail, sort_keys=True), time.time()))

    def policy(self, tenant):
        with self.db() as db:
            row = db.execute('SELECT * FROM policy WHERE tenant=?', (tenant,)).fetchone()
        return dict(row) if row else {'tenant': tenant, 'mode': 'automatic', 'epoch': 0}

    def set_policy(self, tenant, mode, reason):
        if mode not in ('automatic', 'required') or len(reason.strip()) < 5:
            raise ReviewError('Choose a valid review mode and provide a reason of at least five characters.')
        with self.gate(), self.db() as db:
            old = self.policy(tenant)
            if mode == old['mode']:
                return
            db.execute('INSERT INTO policy VALUES(?,?,?) ON CONFLICT(tenant) DO UPDATE SET mode=excluded.mode,epoch=excluded.epoch',
                (tenant, mode, old['epoch'] + 1))
            self.event(db, tenant, 'policy_changed', {'before': old['mode'], 'after': mode, 'reason': reason})

    def mapping(self, tenant, supplier):
        with self.db() as db:
            row = db.execute('SELECT value FROM mappings WHERE tenant=? AND supplier=?', (tenant, supplier)).fetchone()
        if row:
            return json.loads(row['value'])
        return dict(DEFAULT_MAPPING) if supplier == DEFAULT_MAPPING['supplier'] else None

    def save_mapping(self, tenant, supplier, account_code, tax_type, tax_rate, reason):
        if supplier not in DEMO_SUPPLIERS or tax_rate not in ('0', '0.20') or not account_code or not tax_type or len(reason.strip()) < 5:
            raise ReviewError('Use one of the four fictional suppliers, an explicit mapping and a reason.')
        key, number, email = DEMO_SUPPLIERS[supplier]
        mapping = {'supplier': supplier, 'supplier_key': key, 'contact_number': number, 'email': email,
            'account_code': account_code, 'tax_type': tax_type, 'tax_rate': tax_rate}
        with self.gate(), self.db() as db:
            before = self.mapping(tenant, supplier)
            db.execute('INSERT INTO mappings VALUES(?,?,?) ON CONFLICT(tenant,supplier) DO UPDATE SET value=excluded.value',
                (tenant, supplier, json.dumps(mapping)))
            self.event(db, tenant, 'mapping_changed', {'before': before, 'after': mapping, 'reason': reason})
        return mapping

    def ensure_case(self, tenant, run_id):
        existing = self.get(tenant, run_id)
        if existing:
            return existing
        runs = ExtractionRuns(self.root)
        run = runs.load(run_id)
        if run.get('status') == 'processing':
            raise ReviewError('Extraction is still processing. Inspect before creating a workflow case.')
        runs.source(run_id)
        candidate = run.get('invoice') or {}
        # Financial issues are recomputed. Source/reading ambiguities need explicit resolution.
        unresolved = [issue for issue in run.get('issues', []) if not issue.startswith(
            ('missing:', 'invalid:', 'arithmetic:', 'unsupported:'))]
        if not candidate:
            unresolved = list(set(unresolved + ['No usable extraction; source-based manual entry required.']))
        now = time.time()
        with self.gate(), self.db() as db:
            db.execute('INSERT OR IGNORE INTO cases VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (tenant, run_id, 1, run['filename'], run['source_sha256'], json.dumps(candidate),
                 json.dumps(unresolved), None, json.dumps(run['downstream_result']) if run.get('downstream_result') else None, now, now))
            if db.execute('SELECT 1 FROM revisions WHERE tenant=? AND case_id=? AND revision=1', (tenant,run_id)).fetchone() is None:
                db.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?,?)',
                    (tenant, run_id, 1, json.dumps(candidate), json.dumps(unresolved), 'Original extraction imported without alteration.', now))
                self.event(db, tenant, 'imported', {'run_id': run_id, 'source_sha256': run['source_sha256']}, run_id)
        return self.get(tenant, run_id)

    def get(self, tenant, case_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM cases WHERE tenant=? AND id=?', (tenant, case_id)).fetchone()
        if not row:
            return None
        value = dict(row)
        for key in ('candidate', 'unresolved', 'outcome'):
            value[key] = json.loads(value[key]) if value[key] else ([] if key == 'unresolved' else None)
        value['financial_locked'] = bool(value.get('outcome') and value['outcome'].get('status') in ('verified','uncertain','processing'))
        ledger = self.root / 'ledger' / 'invoices.sqlite3'
        if ledger.exists():
            with sqlite3.connect(ledger) as accounting:
                linked = accounting.execute('''SELECT 1 FROM receipts r JOIN invoices i ON i.id=r.invoice_id
                    LEFT JOIN attempts a ON a.invoice_id=i.id WHERE r.tenant=?
                    AND (r.source_key LIKE ? OR r.source_key=?) AND (i.xero_id IS NOT NULL OR a.id IS NOT NULL) LIMIT 1''',
                    (tenant, 'review:'+case_id+':r%', 'extraction:'+case_id)).fetchone()
            value['financial_locked'] = value['financial_locked'] or bool(linked)
        return value

    def issues(self, tenant, case):
        invoice = case['candidate'] or {}
        if not invoice:
            return ['No structured invoice fields.'] + case['unresolved']
        issues = financial_issues(invoice) + case['unresolved']
        supplier = (invoice.get('supplier') or {}).get('name')
        mapping = self.mapping(tenant, supplier)
        if not mapping:
            issues.append('Supplier mapping is not configured.')
        else:
            from app.bills import validate
            try:
                validate(invoice, tax_rate=mapping['tax_rate'])
            except ConnectionError as exc:
                issues.append(str(exc))
        return list(dict.fromkeys(issues))

    def signature(self, tenant, case):
        mapping = self.mapping(tenant, (case['candidate'].get('supplier') or {}).get('name'))
        return digest({'tenant': tenant, 'case': case['id'], 'revision': case['revision'],
            'source_sha256': case['source_sha256'], 'candidate': case['candidate'],
            'unresolved': case['unresolved'], 'mapping': mapping, 'policy_epoch': self.policy(tenant)['epoch']})

    def decision(self, tenant, case):
        issues = self.issues(tenant, case)
        if issues:
            return 'held', issues
        if self.policy(tenant)['mode'] == 'required' and case['approval'] != self.signature(tenant, case):
            return 'awaiting_approval', ['Approval is required for the current invoice revision and mapping.']
        return 'ready', []

    def revise(self, tenant, case_id, expected_revision, candidate, reason, source_checked):
        if len(reason.strip()) < 5 or not source_checked:
            raise ReviewError('Check values against the source PDF and provide a correction reason.')
        try:
            candidate = ExtractedInvoice.model_validate(candidate).model_dump()
        except ValueError:
            raise ReviewError('Correction must match the invoice field structure. Missing values remain null.') from None
        with self.gate(), self.db() as db:
            case = self.get(tenant, case_id)
            if not case or case['revision'] != expected_revision:
                raise ReviewError('This page is stale. Reload the current revision before editing.')
            if case['financial_locked']:
                raise ReviewError('An accounting operation exists. Reconcile it before changing financial fields.')
            ExtractionRuns(self.root).source(case_id)
            version = case['revision'] + 1
            db.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?,?)',
                (tenant, case_id, version, json.dumps(candidate), '[]', reason, time.time()))
            db.execute('UPDATE cases SET revision=?,candidate=?,unresolved=?,approval=NULL,outcome=NULL,updated=? WHERE tenant=? AND id=?',
                (version, json.dumps(candidate), '[]', time.time(), tenant, case_id))
            self.event(db, tenant, 'corrected', {'revision': version, 'reason': reason,
                'source_checked': True, 'before': case['candidate'], 'after': candidate,
                'resolved_reading_issues': case['unresolved'], 'approval_invalidated': bool(case['approval'])}, case_id)
        return self.get(tenant, case_id)

    def approve(self, tenant, case_id, expected_revision, expected_signature):
        with self.gate(), self.db() as db:
            case = self.get(tenant, case_id)
            if not case or case['revision'] != expected_revision:
                raise ReviewError('Stale invoice revision. Reload before approving.')
            if self.issues(tenant, case):
                raise ReviewError('Resolve validation and mapping issues before approval.')
            ExtractionRuns(self.root).source(case_id)
            signature = self.signature(tenant, case)
            if expected_signature != signature:
                raise ReviewError('Policy or mapping changed. Reload the invoice before approving.')
            db.execute('UPDATE cases SET approval=?,updated=? WHERE tenant=? AND id=?',
                (signature, time.time(), tenant, case_id))
            self.event(db, tenant, 'approved', {'revision': case['revision'], 'signature': signature}, case_id)

    def authorize(self, tenant, invoice, source_sha256, authorization=None):
        """Caller holds gate through downstream accounting work."""
        if authorization:
            case = self.get(tenant, authorization['case_id'])
            if not case or case['revision'] != authorization['revision']:
                raise ReviewError('Invoice revision changed before processing. Reload and retry.')
            if critical(case['candidate']) != critical(invoice) or case['source_sha256'] != source_sha256:
                raise ReviewError('Approval or source does not match this invoice.')
            status, issues = self.decision(tenant, case)
            if status != 'ready':
                raise ReviewError('; '.join(issues))
        elif self.policy(tenant)['mode'] == 'required':
            raise ReviewError('Approval is required. Open this invoice in the Phase 5 workflow.')

    def outcome(self, tenant, case_id, result):
        with self.db() as db:
            db.execute('UPDATE cases SET outcome=?,updated=? WHERE tenant=? AND id=?',
                (json.dumps(result), time.time(), tenant, case_id))
            self.event(db, tenant, 'accounting_result', result, case_id)

    def history(self, tenant, case_id=None):
        with self.db() as db:
            if case_id:
                return [dict(r) for r in db.execute('SELECT * FROM audit WHERE tenant=? AND case_id=? ORDER BY id DESC', (tenant,case_id))]
            return [dict(r) for r in db.execute('SELECT * FROM audit WHERE tenant=? ORDER BY id DESC LIMIT 100', (tenant,))]

    def all(self, tenant):
        with self.db() as db:
            ids = [r['id'] for r in db.execute('SELECT id FROM cases WHERE tenant=? ORDER BY updated DESC', (tenant,))]
        return [self.get(tenant, case_id) for case_id in ids]
