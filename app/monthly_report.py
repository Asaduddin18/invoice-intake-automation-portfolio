"""Rebuild a dedicated monthly report from saved state. Never invokes accounting."""
import re
from datetime import datetime,timezone,date
from decimal import Decimal,InvalidOperation
from urllib.parse import quote
from app.google_connection import SHEETS,GoogleError
from app.mail_store import MailStore
from app.pipeline import InvoicePipeline
from app.review import ReviewStore

HEADER=['Delivery ID','Received UTC','Filename','Invoice month','Month basis','Supplier','Invoice number',
        'Invoice date','Currency','Printed subtotal','Printed tax','Printed total','Workflow status',
        'Xero draft ID','Duplicate delivery of','Source SHA256','Reason','Counted draft total GBP']
ORDER=[6,5,7,12,8,9,10,11,17,16,1,2,0,13,14,15,3,4]
HEADER=[HEADER[i] for i in ORDER]

def numeric(value):
    if value in (None,''):return ''
    try:
        number=Decimal(str(value))
        if number.is_finite() and abs(number)<Decimal('1000000000000'):
            return float(number)
    except (InvalidOperation,ValueError):pass
    return value

class MonthlyReport:
    def __init__(self,connection,google):
        self.connection=connection;self.google=google
        self.store=MailStore(connection.store.directory)
        self.review=ReviewStore(connection.store.directory)

    def snapshot(self,tenant,month):
        rows=[];counted=set()
        for row in self.store.all(tenant,self.google.mailbox):
            case=self.review.get(tenant,row['run_id']) if row['run_id'] else None
            invoice=case['candidate'] if case else {}
            invoice=invoice or {};outcome=(case or {}).get('outcome') or {}
            received=datetime.fromtimestamp(row['received'],timezone.utc).isoformat()
            invdate=invoice.get('invoice_date')
            try:report_month=date.fromisoformat(invdate).strftime('%Y-%m');basis='invoice date'
            except (ValueError,TypeError):report_month=received[:7];basis='receipt date, invoice date missing/invalid'
            if report_month!=month:continue
            status=row['status'];reason=row['detail']
            if case:
                decision,issues=self.review.decision(tenant,case)
                status=outcome.get('status') if outcome.get('status') in ('verified','uncertain','processing') else decision
                reason='; '.join(issues) or outcome.get('message') or row['detail']
            xid=outcome.get('invoice_id','') if outcome.get('status')=='verified' else ''
            counted_total=invoice.get('displayed_total','') if xid and xid not in counted else ''
            if xid:counted.add(xid)
            rows.append([row['id'],received,row['filename'],report_month,basis,
                (invoice.get('supplier') or {}).get('name') or '',invoice.get('invoice_number') or '',invdate or '',
                invoice.get('currency') or '',invoice.get('subtotal') or '',invoice.get('tax') or '',
                invoice.get('displayed_total') or '',status,xid,row['duplicate_of'] or '',row['source_sha256'] or '',reason,counted_total or ''])
        prepared=[]
        for row in rows:
            for index in (9,10,11,17):row[index]=numeric(row[index])
            prepared.append([row[i] for i in ORDER])
        return [HEADER]+prepared

    async def adopt(self,month,spreadsheet_id):
        """Recover a creation response lost before its identifier was saved.

        Operator supplies the app-created report ID after inspecting Drive.
        """
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month) or not re.fullmatch(r'[A-Za-z0-9_-]{10,100}',spreadsheet_id):
            raise GoogleError('Use a valid month and the existing report spreadsheet ID.')
        tenant=InvoicePipeline(self.connection).tenant();mailbox=self.google.mailbox
        with self.store.gate('report'):
            old=self.store.report(tenant,mailbox,month)
            if not old or old['status']!='creating' or old['spreadsheet_id']:
                raise GoogleError('Only an interrupted report creation can adopt a sheet.')
            metadata=await self.google.request('GET',SHEETS+'/'+quote(spreadsheet_id,safe=''))
            titles=[s.get('properties',{}).get('title') for s in metadata.get('sheets',[])]
            if metadata.get('properties',{}).get('title')!='Northmere synthetic invoice report '+month or titles!=['Intake']:
                raise GoogleError('The sheet does not match the dedicated monthly report.')
            self.store.save_report(tenant,mailbox,month,'pending','Operator recovered the existing app report identifier. Refresh reporting separately.',spreadsheet_id)

    async def refresh(self,month):
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month):raise GoogleError('Use a reporting month such as 2026-08.')
        tenant=InvoicePipeline(self.connection).tenant();mailbox=self.google.mailbox
        with self.store.gate('report'):
            snapshot=self.snapshot(tenant,month)
            record=self.store.report(tenant,mailbox,month)
            if record and record['status']=='creating' and not record['spreadsheet_id']:
                raise GoogleError('Spreadsheet creation was interrupted. Inspect Google Drive before creating another report.')
            sid=(record or {}).get('spreadsheet_id')
            if not sid:
                self.store.save_report(tenant,mailbox,month,'creating','Creation intent saved. An uncertain creation is never repeated.')
                result=await self.google.request('POST',SHEETS,json={'properties':{'title':'Northmere synthetic invoice report '+month},
                    'sheets':[{'properties':{'title':'Intake','gridProperties':{'rowCount':2000,'columnCount':18,'frozenRowCount':1}}}]})
                sid=result['spreadsheetId']
                self.store.save_report(tenant,mailbox,month,'pending','Spreadsheet identifier saved before writing rows.',sid)
            old_rows=(record or {}).get('rows',0)
            size=max(len(snapshot),old_rows)
            if size>2000:raise GoogleError('Monthly report exceeds 1,999 deliveries. Split or extend the demo explicitly.')
            padded=snapshot+[['']*len(HEADER) for _ in range(size-len(snapshot))]
            span=quote('Intake!A1:R'+str(size),safe='')
            self.store.save_report(tenant,mailbox,month,'pending','Snapshot write started; retry reporting separately.',sid,rows=size)
            try:
                await self.google.request('PUT',SHEETS+'/'+quote(sid,safe='')+'/values/'+span,
                    params={'valueInputOption':'RAW'},json={'range':'Intake!A1:R'+str(size),'majorDimension':'ROWS','values':padded})
                await self.format(sid,size,snapshot)
                actual=await self.google.request('GET',SHEETS+'/'+quote(sid,safe='')+'/values/'+span,
                    params={'valueRenderOption':'UNFORMATTED_VALUE'})
                values=actual.get('values',[])
                normalized=[r+['']*(len(HEADER)-len(r)) for r in values]
                normalized+=[['']*len(HEADER) for _ in range(size-len(normalized))]
                if normalized!=padded:raise GoogleError('Report read-back differs from the saved snapshot. Retry reporting only.')
                self.store.save_report(tenant,mailbox,month,'verified','Report contents retrieved and matched. Accounting was not invoked.',sid,rows=size)
                return {'month':month,'spreadsheet_id':sid,'deliveries':len(snapshot)-1,'status':'verified'}
            except (GoogleError,KeyError,ValueError):
                self.store.save_report(tenant,mailbox,month,'pending','Report update or verification failed. Retry report only; no bill recreation.',sid,rows=size)
                raise

    async def format(self,sid,size,snapshot):
        metadata=await self.google.request('GET',SHEETS+'/'+quote(sid,safe=''))
        sheet=next((s['properties'] for s in metadata.get('sheets',[]) if s.get('properties',{}).get('title')=='Intake'),None)
        if not sheet:raise GoogleError('Reserved Intake tab is missing. Restore it before refreshing.')
        sheet_id=sheet['sheetId']
        grid={'sheetId':sheet_id,'startRowIndex':0,'endRowIndex':max(size,2),'startColumnIndex':0,'endColumnIndex':18}
        header={**grid,'endRowIndex':1}
        requests=[
            {'updateSheetProperties':{'properties':{'sheetId':sheet_id,'gridProperties':{'frozenRowCount':1,'frozenColumnCount':2}},'fields':'gridProperties.frozenRowCount,gridProperties.frozenColumnCount'}},
            {'repeatCell':{'range':grid,'cell':{'userEnteredFormat':{'textFormat':{'fontFamily':'Arial','fontSize':10},'verticalAlignment':'TOP','wrapStrategy':'WRAP'}},'fields':'userEnteredFormat'}},
            {'repeatCell':{'range':header,'cell':{'userEnteredFormat':{'backgroundColor':{'red':0.10,'green':0.19,'blue':0.29},'textFormat':{'bold':True,'foregroundColor':{'red':1,'green':1,'blue':1},'fontSize':10},'verticalAlignment':'MIDDLE','wrapStrategy':'WRAP'}},'fields':'userEnteredFormat'}},
            {'updateDimensionProperties':{'range':{'sheetId':sheet_id,'dimension':'ROWS','startIndex':0,'endIndex':1},'properties':{'pixelSize':48},'fields':'pixelSize'}},
            {'updateDimensionProperties':{'range':{'sheetId':sheet_id,'dimension':'ROWS','startIndex':1,'endIndex':max(size,2)},'properties':{'pixelSize':72},'fields':'pixelSize'}},
            {'repeatCell':{'range':{**grid,'startRowIndex':1,'startColumnIndex':5,'endColumnIndex':9},'cell':{'userEnteredFormat':{'numberFormat':{'type':'NUMBER','pattern':'#,##0.00'},'horizontalAlignment':'RIGHT'}},'fields':'userEnteredFormat.numberFormat,userEnteredFormat.horizontalAlignment'}},
            {'setBasicFilter':{'filter':{'range':grid}}}]
        widths=[145,235,110,150,95,110,95,110,150,340,200,200,360,300,360,400,115,270]
        for col,width in enumerate(widths):
            requests.append({'updateDimensionProperties':{'range':{'sheetId':sheet_id,'dimension':'COLUMNS','startIndex':col,'endIndex':col+1},'properties':{'pixelSize':width},'fields':'pixelSize'}})
        for index,row in enumerate(snapshot[1:],1):
            if row[3]=='verified':color={'red':0.88,'green':0.95,'blue':0.89}
            elif row[3] in ('held','unsupported','needs_inspection','uncertain'):color={'red':1,'green':0.94,'blue':0.80}
            else:continue
            requests.append({'repeatCell':{'range':{'sheetId':sheet_id,'startRowIndex':index,'endRowIndex':index+1,'startColumnIndex':3,'endColumnIndex':4},'cell':{'userEnteredFormat':{'backgroundColor':color}},'fields':'userEnteredFormat.backgroundColor'}})
        await self.google.request('POST',SHEETS+'/'+quote(sid,safe='')+':batchUpdate',json={'requests':requests})
