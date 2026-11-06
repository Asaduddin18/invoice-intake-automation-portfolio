"""Verify local orchestration APIs against the five existing synthetic deliveries.

No model requests are permitted by the intake API. The verification refreshes
existing Sheets reports and checks accounting/model counters remain unchanged.
Run only with the local backend and previously verified demo state.
"""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import urllib.request
import urllib.error
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]


def verify():
    runtime=ROOT/'.runtime'
    token=dotenv_values(ROOT/'.env').get('INVOICE_N8N_API_TOKEN','')
    if len(token)<32:raise SystemExit('Prepare the dedicated API token first.')
    def counts():
        with sqlite3.connect('file:'+str(runtime/'ledger/invoices.sqlite3')+'?mode=ro',uri=True) as db:
            ledger={table:db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('invoices','attempts')}
        ledger['extractions']=len(list((runtime/'extractions').glob('*/result.json')))
        return ledger
    def request(path,body=None,authorized=True):
        headers={'Authorization':'Bearer '+token} if authorized else {}
        if body is not None:headers['Content-Type']='application/json'
        req=urllib.request.Request('http://localhost:8080/integrations/n8n/'+path,
            data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with urllib.request.urlopen(req,timeout=300) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as error:return error.code,json.load(error)
    before=counts()
    code,unauthorized=request('status',authorized=False)
    assert code==401 and unauthorized['code']=='UNAUTHORIZED'
    code,status=request('status')
    assert code==200 and status['data']['deliveries']==5,'Expected the five saved synthetic deliveries.'
    assert status['data']['counts']=={'held':2,'unsupported':1,'verified':2}
    assert status['data']['available_report_months']==['2026-08','2026-11']
    results=[]
    for _ in range(2):
        code,result=request('intake',{})
        if code!=200:raise SystemExit('Intake needs local inspection: '+result['code'])
        assert result['data']['poll']['new_deliveries']==0
        assert result['data']['new_extraction_budget']==0
        results.append(result)
    reports=[]
    for month in status['data']['available_report_months']:
        code,result=request('report',{'month':month})
        if code!=200:raise SystemExit('Report needs local inspection: '+result['code'])
        assert result['code']=='REPORT_VERIFIED'
        reports.append(result)
    after=counts()
    assert before==after,'Unexpected accounting or extraction counter change.'
    return {'phase':9,'verified_at':datetime.now(timezone.utc).isoformat(),
        'unauthorized_status_http':401,'status':status,'intake_runs':results,'report_runs':reports,
        'before':before,'after':after,'new_model_calls':0,'new_bill_creation_intents':0,
        'scope':'Five existing synthetic deliveries; repeated Gmail polling and live report read-back. No new document processing.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path)
    args=parser.parse_args();result=verify()
    if args.output:args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
