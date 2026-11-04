"""Durable Gmail delivery identity and separate report checkpoints."""
import fcntl
import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from app.google_connection import GoogleError

class MailStore:
    def __init__(self,directory):
        self.directory=Path(directory)/'mail'
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        os.chmod(self.directory,0o700)
        self.path=self.directory/'intake.sqlite3'
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,mailbox TEXT NOT NULL,
                message_id TEXT NOT NULL,part_id TEXT NOT NULL,attachment_id TEXT NOT NULL,filename TEXT NOT NULL,
                received REAL NOT NULL,created REAL NOT NULL,status TEXT NOT NULL,detail TEXT NOT NULL,
                source_sha256 TEXT,run_id TEXT,duplicate_of TEXT,
                UNIQUE(tenant,mailbox,message_id,part_id,attachment_id));
            CREATE TABLE IF NOT EXISTS reports(tenant TEXT NOT NULL,mailbox TEXT NOT NULL,month TEXT NOT NULL,
                spreadsheet_id TEXT,status TEXT NOT NULL,detail TEXT NOT NULL,rows INTEGER NOT NULL DEFAULT 0,
                updated REAL NOT NULL,PRIMARY KEY(tenant,mailbox,month));
            ''')
            columns={r['name'] for r in db.execute('PRAGMA table_info(deliveries)')}
            if 'identity_key' not in columns:
                db.execute('ALTER TABLE deliveries ADD COLUMN identity_key TEXT')
                seen={}
                for row in db.execute('SELECT * FROM deliveries ORDER BY created,id').fetchall():
                    key=self.identity(row['tenant'],row['mailbox'],row['message_id'],row['part_id'])
                    if key in seen:
                        # Preserve early prototype receipts for audit, but expose one canonical delivery.
                        db.execute("UPDATE deliveries SET status='superseded',detail=?,duplicate_of=? WHERE id=?",
                            ('Rotating Gmail download locator created a prototype duplicate; canonical delivery retained.',seen[key],row['id']))
                    else:
                        seen[key]=row['id']
                        db.execute('UPDATE deliveries SET identity_key=? WHERE id=?',(key,row['id']))
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS delivery_identity ON deliveries(identity_key)')
        os.chmod(self.path,0o600)

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            yield db;db.commit()
        except BaseException:
            db.rollback();raise
        finally:db.close()

    @contextmanager
    def gate(self,name):
        fd=os.open(self.directory/(name+'.lock'),os.O_RDWR|os.O_CREAT,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise GoogleError('Another '+name+' operation is running. Inspect the result shortly.') from None
            yield
        finally:
            fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)

    @staticmethod
    def identity(tenant,mailbox,message,part_id):
        # Gmail attachmentId is a download locator and can rotate between GETs.
        return hashlib.sha256(json.dumps([tenant,mailbox,message,part_id],separators=(',',':')).encode()).hexdigest()

    def receive(self,tenant,mailbox,message,part,received):
        pid=str(part.get('partId',''))
        rid=self.identity(tenant,mailbox,message,pid)
        with self.db() as db:
            before=db.execute('SELECT * FROM deliveries WHERE identity_key=?',(rid,)).fetchone()
            if before:return dict(before),False
            cursor=db.execute('INSERT OR IGNORE INTO deliveries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (rid,tenant,mailbox,message,pid,str(part.get('body',{}).get('attachmentId','')),
                 Path(part.get('filename') or 'unnamed-attachment').name[:250],received,time.time(),
                 'received','Delivery recorded before extraction.',None,None,None,rid))
            row=db.execute('SELECT * FROM deliveries WHERE identity_key=?',(rid,)).fetchone()
        return dict(row),bool(cursor.rowcount)

    def update(self,rid,status,detail,**fields):
        if not set(fields).issubset({'source_sha256','run_id','duplicate_of'}):raise ValueError('Invalid fields')
        with self.db() as db:
            db.execute('UPDATE deliveries SET status=?,detail=?'+''.join(','+k+'=?' for k in fields)+' WHERE id=?',
                (status,detail,*fields.values(),rid))

    def get(self,rid):
        with self.db() as db:row=db.execute('SELECT * FROM deliveries WHERE id=?',(rid,)).fetchone()
        return dict(row) if row else None

    def all(self,tenant,mailbox):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM deliveries WHERE tenant=? AND mailbox=? AND identity_key IS NOT NULL ORDER BY created,id',(tenant,mailbox))]

    def report(self,tenant,mailbox,month):
        with self.db() as db:row=db.execute('SELECT * FROM reports WHERE tenant=? AND mailbox=? AND month=?',(tenant,mailbox,month)).fetchone()
        return dict(row) if row else None

    def save_report(self,tenant,mailbox,month,status,detail,spreadsheet_id=None,rows=0):
        with self.db() as db:
            db.execute('INSERT INTO reports VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(tenant,mailbox,month) DO UPDATE SET spreadsheet_id=COALESCE(excluded.spreadsheet_id,reports.spreadsheet_id),status=excluded.status,detail=excluded.detail,rows=MAX(excluded.rows,reports.rows),updated=excluded.updated',
                (tenant,mailbox,month,spreadsheet_id,status,detail,rows,time.time()))
