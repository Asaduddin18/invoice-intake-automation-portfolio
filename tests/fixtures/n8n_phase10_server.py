"""Private-network synthetic API stand-in for n8n failure/recovery tests.

No provider credentials, host-published port, invoice documents or real writes.
"""
from http.server import BaseHTTPRequestHandler,HTTPServer
import json

TRACE=[]
FAILED_ONCE=False
COUNTS={'verified':2,'held':2,'unsupported':1}


def response(code,data):return {'status':'completed' if code in ('INTAKE_COMPLETED','REPORT_VERIFIED') else 'ok','code':code,'correlation_id':'b'*32,'data':data}


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):self.handle_request()
    def do_POST(self):self.handle_request()
    def handle_request(self):
        global FAILED_ONCE
        if self.path=='/trace':return self.send(200,{'requests':TRACE})
        pieces=self.path.strip('/').split('/')
        if len(pieces)!=2:return self.send(404,{})
        scenario,operation=pieces
        body=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or '{}')
        TRACE.append({'scenario':scenario,'operation':operation,'month':body.get('month'),'authorization_present':bool(self.headers.get('Authorization'))})
        if self.headers.get('Authorization'):return self.send(400,response('INVALID_REQUEST',{}))
        if scenario=='unauthorized':return self.send(401,response('UNAUTHORIZED',{'private':'do-not-copy-fixture-error'}))
        months=[] if scenario=='empty' else ['2026-08','2026-09','2026-11']
        data={'deliveries':5,'counts':COUNTS,'duplicate_deliveries':1,'review_policy':'automatic','new_extraction_budget':0,
            'available_report_months':months,'poll':{'new_deliveries':0,'already_recorded':5,'more_pages':False},
            'readiness':{k:True for k in ('xero_saved','google_saved','xero_configured','google_configured')},'private':'do-not-copy-fixture-payload'}
        if scenario=='clear':data.update(deliveries=2,counts={'verified':2},duplicate_deliveries=1)
        if operation=='status':return self.send(200,response('STATUS_READY',data))
        if operation=='intake':return self.send(200,response('INTAKE_COMPLETED',data))
        if operation=='report':
            if body.get('month') not in months:return self.send(400,response('UNKNOWN_REPORT_MONTH',{}))
            if scenario=='recovery' and body['month']=='2026-09' and not FAILED_ONCE:
                FAILED_ONCE=True;return self.send(503,response('OPERATION_FAILED',{'private':'do-not-copy-fixture-error'}))
            return self.send(200,response('REPORT_VERIFIED',{'month':body['month'],'deliveries':1,'report_status':'verified','private':'do-not-copy-fixture-payload'}))
        return self.send(404,{})
    def send(self,status,value):
        raw=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)


if __name__=='__main__':HTTPServer(('0.0.0.0',8080),Handler).serve_forever()
