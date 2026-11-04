"""Durable local invoice/receipt ledger. Contains synthetic invoice data, never tokens."""
import hashlib
import json
import os
import sqlite3
import time
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

class Ledger:
    def __init__(self, directory):
        self.directory=Path(directory)
        self.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        os.chmod(self.directory,0o700)
        self.path=self.directory/'invoices.sqlite3'
        with self.db() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone():
                versions=[r['version'] for r in db.execute('SELECT version FROM schema_version')]
                if any(v>2 for v in versions):raise ValueError('Ledger was created by a newer app; do not downgrade.')
            db.executescript('''
            CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO schema_version VALUES(1);
            CREATE TABLE IF NOT EXISTS invoices(
              id TEXT PRIMARY KEY,tenant TEXT NOT NULL,supplier_key TEXT NOT NULL,number_key TEXT NOT NULL,
              invoice_number TEXT NOT NULL,fingerprint TEXT NOT NULL,expected_json TEXT NOT NULL,
              state TEXT NOT NULL,contact_id TEXT,xero_id TEXT,message TEXT,created REAL NOT NULL,updated REAL NOT NULL,
              UNIQUE(tenant,supplier_key,number_key));
            CREATE TABLE IF NOT EXISTS receipts(
              id TEXT PRIMARY KEY,tenant TEXT NOT NULL,source_key TEXT NOT NULL,filename TEXT NOT NULL,
              sha256 TEXT NOT NULL,invoice_id TEXT REFERENCES invoices(id),outcome TEXT NOT NULL,
              message TEXT,created REAL NOT NULL,submitted_json TEXT, UNIQUE(tenant,source_key));
            CREATE INDEX IF NOT EXISTS receipt_hash ON receipts(tenant,sha256);
            CREATE TABLE IF NOT EXISTS attempts(
              id TEXT PRIMARY KEY,invoice_id TEXT NOT NULL REFERENCES invoices(id),kind TEXT NOT NULL,
              idempotency_key TEXT NOT NULL UNIQUE,payload_json TEXT NOT NULL,state TEXT NOT NULL,
              xero_id TEXT,created REAL NOT NULL,updated REAL NOT NULL,UNIQUE(invoice_id,kind));
            CREATE TABLE IF NOT EXISTS events(
              id INTEGER PRIMARY KEY AUTOINCREMENT,invoice_id TEXT REFERENCES invoices(id),
              receipt_id TEXT REFERENCES receipts(id),kind TEXT NOT NULL,message TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS suppliers(
              tenant TEXT NOT NULL,supplier_key TEXT NOT NULL,state TEXT NOT NULL,contact_id TEXT,
              idempotency_key TEXT NOT NULL,PRIMARY KEY(tenant,supplier_key));
            ''')
            # Additive v1 -> v2 migration preserves earlier local test receipts.
            columns={r['name'] for r in db.execute('PRAGMA table_info(receipts)')}
            if 'submitted_json' not in columns:
                db.execute('ALTER TABLE receipts ADD COLUMN submitted_json TEXT')
            db.execute('DELETE FROM schema_version')
            db.execute('INSERT INTO schema_version VALUES(2)')
        os.chmod(self.path,0o600)
        (self.directory/'documents').mkdir(exist_ok=True,mode=0o700)
        (self.directory/'locks').mkdir(exist_ok=True,mode=0o700)

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback();raise
        finally:db.close()

    def blob(self,content):
        digest=hashlib.sha256(content).hexdigest()
        path=self.directory/'documents'/(digest+'.pdf')
        fd,temp=tempfile.mkstemp(dir=self.directory/'documents',prefix='incoming-')
        try:
            with os.fdopen(fd,'wb') as f:
                f.write(content);f.flush();os.fsync(f.fileno())
            try:os.link(temp,path)
            except FileExistsError:
                if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Stored PDF integrity failure')
        finally:
            os.unlink(temp)
        return digest

    def event(self,db,kind,message,invoice_id=None,receipt_id=None):
        db.execute('INSERT INTO events(invoice_id,receipt_id,kind,message,created) VALUES(?,?,?,?,?)',(invoice_id,receipt_id,kind,message,time.time()))

    def receive(self,tenant,source_key,filename,content,invoice,supplier_key,error=None):
        digest=self.blob(content)
        now=time.time();receipt_id=str(uuid.uuid4());invoice_id=None;outcome='received';message='New invoice received.'
        fields=['supplier','invoice_number','invoice_date','due_date','currency','line_items','subtotal','tax','displayed_total']
        # Ignore fixture filenames/layout labels/expected answers; compare business facts only.
        business={k:invoice.get(k) for k in fields}
        if isinstance(business['supplier'],dict):business['supplier']={'name':business['supplier'].get('name')}
        encoded=json.dumps(business,sort_keys=True,separators=(',',':'))
        fp=hashlib.sha256(encoded.encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM receipts WHERE tenant=? AND source_key=?',(tenant,source_key)).fetchone()
            if old:
                if old['sha256']!=digest:raise ValueError('Source identity reused for different file bytes')
                if old['submitted_json'] and json.dumps(json.loads(old['submitted_json']),sort_keys=True)!=json.dumps(invoice,sort_keys=True):
                    raise ValueError('Source identity reused with different submitted values')
                if old['invoice_id'] and not error:
                    existing=db.execute('SELECT fingerprint FROM invoices WHERE id=?',(old['invoice_id'],)).fetchone()
                    if existing['fingerprint']!=fp:raise ValueError('Source identity reused with different parsed values')
                return dict(old)
            if error:
                outcome='held_invalid';message=error
            else:
                number=invoice['invoice_number'].strip()
                row=db.execute('SELECT * FROM invoices WHERE tenant=? AND supplier_key=? AND number_key=?',(tenant,supplier_key,number.casefold())).fetchone()
                if row:
                    invoice_id=row['id']
                    if row['fingerprint']!=fp:
                        outcome='held_conflict';message='Same supplier/invoice identity has different parsed values. Original invoice was not changed.'
                    else:
                        identical=db.execute('SELECT 1 FROM receipts WHERE tenant=? AND sha256=? AND invoice_id=?',(tenant,digest,invoice_id)).fetchone()
                        outcome='duplicate_file' if identical else 'duplicate_invoice'
                        message='Linked to the existing invoice; no second obligation.'
                else:
                    # Same bytes cannot safely represent another financial invoice.
                    same=db.execute('SELECT invoice_id FROM receipts WHERE tenant=? AND sha256=? AND invoice_id IS NOT NULL',(tenant,digest)).fetchone()
                    if same:
                        invoice_id=same['invoice_id'];outcome='held_conflict';message='Identical file has conflicting invoice identity.'
                    else:
                        invoice_id=str(uuid.uuid4())
                        db.execute('INSERT INTO invoices(id,tenant,supplier_key,number_key,invoice_number,fingerprint,expected_json,state,message,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(invoice_id,tenant,supplier_key,number.casefold(),number,fp,json.dumps(invoice),'received',message,now,now))
            db.execute('INSERT INTO receipts(id,tenant,source_key,filename,sha256,invoice_id,outcome,message,created,submitted_json) VALUES(?,?,?,?,?,?,?,?,?,?)',(receipt_id,tenant,source_key,filename,digest,invoice_id,outcome,message,now,json.dumps(invoice,sort_keys=True)))
            self.event(db,outcome,message,invoice_id,receipt_id)
            return dict(db.execute('SELECT * FROM receipts WHERE id=?',(receipt_id,)).fetchone())

    def invoice(self,invoice_id):
        with self.db() as db:
            row=db.execute('SELECT * FROM invoices WHERE id=?',(invoice_id,)).fetchone()
            return dict(row) if row else None

    def state(self,invoice_id,state,message,**fields):
        if not set(fields).issubset({'contact_id','xero_id'}):raise ValueError('Invalid ledger update')
        with self.db() as db:
            values={'state':state,'message':message,'updated':time.time(),**fields}
            db.execute('UPDATE invoices SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),invoice_id))
            self.event(db,state,message,invoice_id)

    def attempt(self,invoice_id):
        with self.db() as db:
            row=db.execute('SELECT * FROM attempts WHERE invoice_id=? AND kind=?',(invoice_id,'bill')).fetchone()
            return dict(row) if row else None

    def intent(self,invoice_id,payload):
        key=hashlib.sha256(('bill:'+invoice_id).encode()).hexdigest();now=time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),invoice_id,'bill',key,json.dumps(payload,sort_keys=True),'uncertain',None,now,now))
            db.execute('UPDATE invoices SET state=?,message=?,updated=? WHERE id=?',('uncertain','Creation intent persisted before Xero request.',now,invoice_id))
            self.event(db,'creation_intent','Payload and idempotency key saved before network write.',invoice_id)
        return key

    def attempt_found(self,invoice_id,xero_id):
        with self.db() as db:
            db.execute('UPDATE attempts SET state=?,xero_id=?,updated=? WHERE invoice_id=?',('found',xero_id,time.time(),invoice_id))

    def snapshot(self,tenant):
        with self.db() as db:
            return {name:[dict(r) for r in db.execute(query,(tenant,))] for name,query in {
                'invoices':'SELECT id,invoice_number,supplier_key,state,xero_id,message,created,updated FROM invoices WHERE tenant=? ORDER BY created DESC',
                'receipts':'SELECT * FROM receipts WHERE tenant=? ORDER BY created DESC',
                'events':'SELECT e.* FROM events e LEFT JOIN invoices i ON i.id=e.invoice_id LEFT JOIN receipts r ON r.id=e.receipt_id WHERE COALESCE(i.tenant,r.tenant)=? ORDER BY e.id DESC LIMIT 100',
                'attempts':'SELECT a.id,a.invoice_id,a.kind,a.state,a.xero_id,a.created,a.updated FROM attempts a JOIN invoices i ON i.id=a.invoice_id WHERE i.tenant=? ORDER BY a.created DESC'
            }.items()}

    @contextmanager
    def lock(self,key):
        """Nonblocking OS lock; released by the OS even after worker termination."""
        import fcntl
        path=self.directory/'locks'/hashlib.sha256(key.encode()).hexdigest()
        fd=os.open(path,os.O_CREAT|os.O_RDWR,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                yield False;return
            try:yield True
            finally:fcntl.flock(fd,fcntl.LOCK_UN)
        finally:os.close(fd)
