"""Bounded label polling. Every delivery is saved before any model or Xero call."""
import base64
import hashlib
import os
import time
from urllib.parse import quote

from app.extraction import PROMPT, ExtractionError, MAX_BYTES, pdf_reader
from app.extraction_runs import ExtractionRuns
from app.google_connection import GMAIL, GoogleError
from app.mail_store import MailStore
from app.pipeline import InvoicePipeline
from app.review import ReviewStore
from app.workflow import submit_case


def attachments(payload):
    """Named MIME parts and inline PDF parts, recursively, with bounded nesting/count."""
    result=[];visited=0
    def walk(part,depth=0):
        nonlocal visited
        visited+=1
        if depth>12 or visited>200:raise GoogleError('Message MIME structure exceeds the local limit.')
        if part.get('filename') or part.get('mimeType')=='application/pdf':result.append(part)
        for child in part.get('parts',[]):walk(child,depth+1)
    walk(payload)
    if len(result)>20:raise GoogleError('Message has more than 20 attachments. Split the test email.')
    return result


def decode_body(body):
    text=body.get('data','')
    if len(text)>14_000_000:raise GoogleError('Attachment exceeds the 10 MB input limit.')
    try:data=base64.b64decode(text+'='*(-len(text)%4),altchars=b'-_',validate=True)
    except (ValueError,TypeError):raise GoogleError('Attachment encoding is invalid.') from None
    if not data or len(data)>MAX_BYTES:raise GoogleError('Attachment is empty or exceeds 10 MB.')
    if body.get('size') is not None and body['size']!=len(data):raise GoogleError('Attachment size disagrees with Gmail metadata.')
    return data

class MailIntake:
    def __init__(self,connection,google,extraction_lock):
        self.connection=connection;self.google=google;self.lock=extraction_lock
        self.store=MailStore(connection.store.directory)
        self.runs=ExtractionRuns(connection.store.directory)
        self.review=ReviewStore(connection.store.directory)

    async def process(self,row,part,budget=None):
        rid=row['id'];tenant=row['tenant']
        if part.get('mimeType')!='application/pdf' and not row['filename'].lower().endswith('.pdf'):
            self.store.update(rid,'unsupported','Only PDF attachments are supported. No model call.');return
        body=part.get('body',{})
        if body.get('size',0)>MAX_BYTES:
            self.store.update(rid,'unsupported','Attachment exceeds 10 MB. No model call.');return
        try:
            if body.get('attachmentId'):
                body=await self.google.request('GET',GMAIL+'/messages/'+quote(row['message_id'],safe='')+'/attachments/'+quote(body['attachmentId'],safe=''))
            data=decode_body(body);pdf_reader(data)
            digest=hashlib.sha256(data).hexdigest()
            family=next((r for r in self.store.all(tenant,row['mailbox']) if r['id']!=rid and r['source_sha256']==digest),None)
            self.store.update(rid,'received','PDF source checked.',source_sha256=digest,duplicate_of=family['id'] if family else None)
            async with self.lock:
                cached=self.runs.cached(digest,hashlib.sha256(PROMPT.read_bytes()).hexdigest(),'deepseek-flash')
                if cached:
                    self.runs.source(cached['id'])
                    run=cached
                    if run['status']=='processing':
                        self.store.update(rid,'needs_inspection','Earlier extraction was interrupted. No paid retry.',run_id=run['id']);return
                else:
                    if budget is None or budget[0]<=0:
                        self.store.update(rid,'needs_inspection','New extraction budget exhausted. Explicitly resume this delivery in a later batch.');return
                    if not os.getenv('DEEPSEEK_API_KEY'):
                        self.store.update(rid,'needs_inspection','DeepSeek key is missing. Add it before an explicit resume.');return
                    budget[0]-=1
                    self.store.update(rid,'extracting','Extraction started. Interrupted calls are never replayed by polling.')
                    run=await self.runs.run(data,row['filename'],os.environ['DEEPSEEK_API_KEY'],model='deepseek-flash')
            self.store.update(rid,'extracted','Extraction source linked; applying current policy.',run_id=run['id'])
            case=self.review.ensure_case(tenant,run['id'])
            # Existing financial outcomes are read without resubmitting to Xero.
            outcome=case.get('outcome') or {}
            if outcome.get('status') not in ('uncertain','processing') and not (family and outcome.get('status')=='verified'):
                outcome=await submit_case(self.connection,case['id'],case['revision'])
            self.store.update(rid,outcome.get('status','held'),outcome.get('message') or 'Workflow outcome saved. Open the source case for details.')
        except (GoogleError,ExtractionError,ValueError,OSError) as exc:
            # Safe local messages only; no raw upstream responses, mail bodies or auth data.
            self.store.update(rid,'needs_inspection',str(exc) if isinstance(exc,(GoogleError,ExtractionError)) else 'Processing stopped. Inspect the source and saved workflow before resuming.')

    async def poll(self, *, max_new_extractions=3):
        if type(max_new_extractions) is not int or not 0<=max_new_extractions<=3:
            raise ValueError('Extraction budget must be between zero and three.')
        tenant=InvoicePipeline(self.connection).tenant();mailbox=self.google.mailbox
        label=os.getenv('GMAIL_INTAKE_LABEL','Invoices-Demo')
        with self.store.gate('intake'):
            profile=await self.google.request('GET',GMAIL+'/profile')
            if profile.get('emailAddress','').lower()!=mailbox:raise GoogleError('Mailbox identity mismatch.')
            labels=await self.google.request('GET',GMAIL+'/labels')
            matches=[x['id'] for x in labels.get('labels',[]) if x.get('name')==label]
            if len(matches)!=1:raise GoogleError('Create exactly one '+label+' label in the connected Gmail account.')
            # Persist cursor across bounded runs so older messages are not starved by a busy label.
            cursor_path=self.store.directory/'poll-cursor.json'
            import json
            cursor={}
            if cursor_path.exists():cursor=json.loads(cursor_path.read_text())
            bound=[tenant,mailbox,matches[0]]
            page_token=cursor.get('token') if cursor.get('binding')==bound else None
            params={'labelIds':matches[0],'maxResults':10,'includeSpamTrash':'false'}
            if page_token:params['pageToken']=page_token
            listing=await self.google.request('GET',GMAIL+'/messages',params=params)
            count=0;skipped=0;budget=[max_new_extractions]
            for item in listing.get('messages',[]):
                message=await self.google.request('GET',GMAIL+'/messages/'+quote(item['id'],safe=''),params={'format':'full'})
                # Labels can change between listing and retrieval.
                if matches[0] not in message.get('labelIds',[]):continue
                try:parts=attachments(message.get('payload',{}))
                except GoogleError as exc:
                    parts=[];structure_error=str(exc)
                else:structure_error=None
                if not parts:
                    row,new=self.store.receive(tenant,mailbox,message['id'],{'partId':'no-supported-parts'},int(message.get('internalDate',time.time()*1000))/1000)
                    if new:self.store.update(row['id'],'unsupported',structure_error or 'No named attachments or PDF parts. No model call.');count+=1
                    else:skipped+=1
                for part in parts:
                    row,new=self.store.receive(tenant,mailbox,message['id'],part,int(message.get('internalDate',time.time()*1000))/1000)
                    if not new:skipped+=1;continue
                    await self.process(row,part,budget);count+=1
            # Atomic owner-only JSON checkpoint. A crash before this only repeats receipt lookups.
            fd,tmp=__import__('tempfile').mkstemp(dir=self.store.directory)
            with os.fdopen(fd,'w') as stream:
                json.dump({'binding':bound,'token':listing.get('nextPageToken')},stream);stream.flush();os.fsync(stream.fileno())
            os.replace(tmp,cursor_path)
            return {'new_deliveries':count,'already_recorded':skipped,'more_pages':bool(listing.get('nextPageToken'))}

    async def resume(self,rid):
        tenant=InvoicePipeline(self.connection).tenant()
        with self.store.gate('intake'):
            row=self.store.get(rid)
            if not row or row['tenant']!=tenant or row['mailbox']!=self.google.mailbox:raise GoogleError('Delivery does not belong to this connection.')
            if row['status'] not in ('received','extracted','needs_inspection','extracting'):
                raise GoogleError('Use the linked workflow to correct, approve or reconcile this delivery.')
            message=await self.google.request('GET',GMAIL+'/messages/'+quote(row['message_id'],safe=''),params={'format':'full'})
            part=next((p for p in attachments(message.get('payload',{})) if str(p.get('partId',''))==row['part_id']),None)
            if not part:raise GoogleError('Original attachment identity was not found.')
            # Extracting without a run link might have consumed a paid request. Never resubmit automatically.
            if row['status']=='extracting' and not row['run_id']:
                cached=self.runs.cached(row['source_sha256'],hashlib.sha256(PROMPT.read_bytes()).hexdigest(),'deepseek-flash')
                if not cached:
                    raise GoogleError('Extraction may have been billed. Inspect saved extractions before any new paid attempt.')
            await self.process(row,part,[1])
