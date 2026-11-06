"""Generate the three inactive Phase 10 workflow exports from editable logic."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'deploy/n8n/phase-10'
ERROR_ID='northmereP10Errors'


def node(name,kind,parameters,x,y=0,version=1,**extra):
    return {'id':name.lower().replace(' ','-'),'name':name,'type':'n8n-nodes-base.'+kind,
        'typeVersion':version,'position':[x,y],'parameters':parameters,**extra}


def code(name,file,ending,x,y=0):
    return node(name,'code',{'language':'javaScript','jsCode':(OUT/'logic'/file).read_text()+'\n'+ending,'mode':'runOnceForAllItems'},x,y,2)


def condition(name,field,x,y=0):
    return node(name,'if',{'conditions':{'options':{'caseSensitive':True,'leftValue':'','typeValidation':'strict','version':2},
        'conditions':[{'id':'check','leftValue':'={{ $json.'+field+' }}','rightValue':True,
        'operator':{'type':'boolean','operation':'true','singleValue':True}}],'combinator':'and'},'options':{}},x,y,2.2)


def http(name,path,x,y=0,post=False,body=None):
    params={'url':'http://invoice-api:8080/integrations/n8n/'+path,'authentication':'genericCredentialType','genericAuthType':'httpHeaderAuth',
        'options':{'timeout':300000,'response':{'response':{'fullResponse':True,'neverError':True,'responseFormat':'json'}}}}
    if post:params.update(method='POST',sendBody=True,specifyBody='json',jsonBody=body or '{}')
    return node(name,'httpRequest',params,x,y,4.2,onError='continueErrorOutput',retryOnFail=False)


def workflow(id,name,nodes):
    return {'id':id,'name':name,'nodes':nodes,'connections':{},'settings':{'executionOrder':'v1','timezone':'Asia/Kolkata',
        'saveExecutionProgress':False,'executionTimeout':900,'errorWorkflow':ERROR_ID},'active':False,'pinData':{}}


def connect(w,source,target,index=0):
    outputs=w['connections'].setdefault(source,{'main':[]})['main']
    while len(outputs)<=index:outputs.append([])
    outputs[index].append({'node':target,'type':'main','index':0})


def failure_path(w,source,index=1,x=1200,y=400):
    names={n['name'] for n in w['nodes']}
    if 'Manual or CLI run' not in names:
        w['nodes'] += [condition('Manual or CLI run','manual_invocation',x,y),
            node('Record safe failure','executeWorkflow',{'source':'database','workflowId':{'__rl':True,'value':ERROR_ID,'mode':'id'},
                'mode':'once','options':{'waitForSubWorkflow':True}},x+240,y,1.2),
            node('Stop and inspect saved state','stopAndError',{'errorType':'errorMessage','errorMessage':'Operation failed. Inspect the saved state and failure summary before a manual retry.'},x+480,y)]
        connect(w,'Manual or CLI run','Record safe failure');connect(w,'Record safe failure','Stop and inspect saved state')
        connect(w,'Manual or CLI run','Stop and inspect saved state',1)
    connect(w,source,'Manual or CLI run',index)


def build():
    manual=lambda:node('Run manually','manualTrigger',{},0)
    error=workflow(ERROR_ID,'Phase 10 - Safe failure handling',[
        node('Automatic workflow failure','errorTrigger',{},0),
        node('Failure from a manual run','executeWorkflowTrigger',{'inputSource':'passthrough'},0,220,1.1),
        code('Safe failure summary','failure.js','return [{json:safeFailure($input.first().json)}];',300,100)])
    error['settings'].pop('errorWorkflow')
    connect(error,'Automatic workflow failure','Safe failure summary');connect(error,'Failure from a manual run','Safe failure summary')
    intake=workflow('northmereP10Intake','Phase 10 - Invoice intake',[
        manual(),node('Every 15 minutes - DISABLED','scheduleTrigger',{'rule':{'interval':[{'field':'minutes','minutesInterval':15}]}},0,-200,1.2,disabled=True),
        http('Check invoice inbox','intake',250,post=True),
        code('Read safe intake result','normalize.js',"return [{json:normalize($input.first().json,'intake',$('Run manually').isExecuted?'manual':$execution.mode)}];",500),
        condition('Intake request succeeded','request_ok',750),condition('Any deliveries need attention','attention_required',1000),
        node('Review exceptions in invoice app','noOp',{},1250,-100),node('Intake complete - no pending exceptions','noOp',{},1250,150)])
    connect(intake,'Run manually','Check invoice inbox');connect(intake,'Every 15 minutes - DISABLED','Check invoice inbox')
    connect(intake,'Check invoice inbox','Read safe intake result');connect(intake,'Check invoice inbox','Read safe intake result',1)
    connect(intake,'Read safe intake result','Intake request succeeded');connect(intake,'Intake request succeeded','Any deliveries need attention')
    connect(intake,'Any deliveries need attention','Review exceptions in invoice app');connect(intake,'Any deliveries need attention','Intake complete - no pending exceptions',1)
    failure_path(intake,'Intake request succeeded',x=1000)
    reports=workflow('northmereP10Reports','Phase 10 - Monthly reporting',[
        manual(),node('Daily 09-00 - DISABLED','scheduleTrigger',{'rule':{'interval':[{'field':'days','daysInterval':1,'triggerAtHour':9,'triggerAtMinute':0}]}},0,-200,1.2,disabled=True),
        http('Read available report months','status',250),
        code('Read safe status','normalize.js',"return [{json:normalize($input.first().json,'status',$('Run manually').isExecuted?'manual':$execution.mode)}];",500),
        condition('Status request succeeded','request_ok',750),
        code('Prepare reporting months','months.js','return reportItems($input.first().json);',1000),
        condition('Any report month exists','has_month',1250),node('Nothing to report','noOp',{},1500,-200),
        node('One month at a time','splitInBatches',{'batchSize':1,'options':{}},1500,0,3),
        http('Refresh this report month','report',1750,200,post=True,body='={{ JSON.stringify({month:$json.month}) }}'),
        code('Read safe report result','normalize.js',"return [{json:normalize($input.first().json,'report',$('Run manually').isExecuted?'manual':$execution.mode)}];",2000,200),
        condition('Report read-back verified','request_ok',2250,200),
        code('Reporting complete','report-summary.js','return [{json:reportSummary($input.all().map(item=>item.json))}];',1750,-100)])
    connect(reports,'Run manually','Read available report months');connect(reports,'Daily 09-00 - DISABLED','Read available report months')
    connect(reports,'Read available report months','Read safe status');connect(reports,'Read available report months','Read safe status',1)
    connect(reports,'Read safe status','Status request succeeded');connect(reports,'Status request succeeded','Prepare reporting months')
    connect(reports,'Prepare reporting months','Any report month exists');connect(reports,'Any report month exists','One month at a time');connect(reports,'Any report month exists','Nothing to report',1)
    connect(reports,'One month at a time','Reporting complete');connect(reports,'One month at a time','Refresh this report month',1)
    connect(reports,'Refresh this report month','Read safe report result');connect(reports,'Refresh this report month','Read safe report result',1)
    connect(reports,'Read safe report result','Report read-back verified');connect(reports,'Report read-back verified','One month at a time')
    failure_path(reports,'Status request succeeded',x=1000,y=500);failure_path(reports,'Report read-back verified')
    for name,value in [('errors',error),('intake',intake),('reports',reports)]:
        (OUT/('phase-10-'+name+'.json')).write_text(json.dumps(value,indent=2)+'\n')
    return [error,intake,reports]


if __name__=='__main__':
    print(json.dumps({'generated':[w['name'] for w in build()],'active_workflows':0}))
