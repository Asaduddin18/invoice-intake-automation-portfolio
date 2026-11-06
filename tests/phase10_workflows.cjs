const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),dir=path.join(root,'deploy/n8n/phase-10');
function load(file,func){const scope={};vm.createContext(scope);vm.runInContext(fs.readFileSync(path.join(dir,'logic',file),'utf8')+`;this.call=${func}`,scope);return (...args)=>JSON.parse(JSON.stringify(scope.call(...args)));}
const normalize=load('normalize.js','normalize'),failure=load('failure.js','safeFailure'),months=load('months.js','reportItems'),summary=load('report-summary.js','reportSummary');
const data={deliveries:5,counts:{verified:2,held:2,unsupported:1},duplicate_deliveries:1,review_policy:'automatic',new_extraction_budget:0,
  available_report_months:['2026-11','2026-08'],poll:{new_deliveries:0,already_recorded:5,more_pages:false},
  readiness:{xero_saved:true,google_saved:true,xero_configured:true,google_configured:true}};
const input={statusCode:200,body:{code:'INTAKE_COMPLETED',correlation_id:'a'.repeat(32),data:{...data,invoice:'private-fixture'}}};
let result=normalize(input,'intake','manual');assert.equal(result.request_ok,true);assert.equal(result.attention_required,true);assert.equal(result.new_extraction_budget,0);
assert.deepEqual(result.available_report_months,['2026-08','2026-11']);assert.equal(JSON.stringify(result).includes('private-fixture'),false);
const clear=structuredClone(input);clear.body.data.counts={verified:5};assert.equal(normalize(clear,'intake','manual').attention_required,false);
for(const code of ['UNAUTHORIZED','CONNECTION_REQUIRED','OPERATION_BUSY','OPERATION_FAILED']){
  result=normalize({statusCode:503,body:{code,data:{secret:'private-fixture'}}},'intake','manual');assert.equal(result.request_ok,false);assert.equal(result.code,code);assert.equal(result.automatic_retry,false);assert.equal(JSON.stringify(result).includes('private-fixture'),false);
}
assert.equal(normalize({error:{message:'secret private-fixture'}},'intake','cli').code,'TRANSPORT_FAILED');
for(const invalid of [-1,0.5,'2',NaN]){const copy=structuredClone(input);copy.body.data.counts.held=invalid;assert.equal(normalize(copy,'intake','manual').request_ok,false);}
const overspend=structuredClone(input);overspend.body.data.new_extraction_budget=1;assert.equal(normalize(overspend,'intake','manual').request_ok,false);
const status={statusCode:200,body:{code:'STATUS_READY',data:structuredClone(data)}};
assert.equal(normalize(status,'status','manual').request_ok,true);status.body.data.readiness.google_saved=false;assert.equal(normalize(status,'status','manual').code,'CONNECTION_REQUIRED');
assert.equal(normalize(input,'intake','trigger').manual_invocation,false);
assert.equal(normalize(input,'intake','cli').manual_invocation,true);
assert.equal(normalize(input,'intake','test').manual_invocation,true);
assert.equal(normalize(input,'intake','production').manual_invocation,false);
assert.equal(normalize({statusCode:200,body:{code:'private-fixture',data}},'intake','manual').code,'RESPONSE_INVALID');
const report=normalize({statusCode:200,body:{code:'REPORT_VERIFIED',data:{month:'2026-08',deliveries:4,report_status:'verified',spreadsheet_id:'private-fixture'}}},'report','cli');
assert.equal(report.request_ok,true);assert.equal(JSON.stringify(report).includes('private-fixture'),false);
assert.deepEqual(months({available_report_months:['2026-11','2026-08','2026-08']}).map(x=>x.json.month),['2026-08','2026-11']);
assert.equal(months({available_report_months:[]})[0].json.status,'nothing_to_report');
assert.throws(()=>months({available_report_months:['2026-99']}));
assert.equal(summary([report]).refreshed_months,1);assert.throws(()=>summary([report,report]));assert.throws(()=>summary([{...report,request_ok:false}]));
const clean=failure({workflow:{id:'northmereP10Intake',name:'private-fixture'},execution:{id:'123',error:{message:'private-fixture',stack:'private-fixture'}}});
assert.equal(clean.operation,'intake');assert.equal(clean.execution_ref,'123');assert.equal(clean.code,'WORKFLOW_FAILED');assert.equal(JSON.stringify(clean).includes('private-fixture'),false);
assert.equal(failure({operation:'report',code:'OPERATION_FAILED',correlation_id:'a'.repeat(32),untrusted:'private-fixture'}).correlation_id,'a'.repeat(32));
for(const name of ['errors','intake','reports']){
 const w=JSON.parse(fs.readFileSync(path.join(dir,`phase-10-${name}.json`),'utf8'));
 assert.equal(w.active,false);assert.equal(w.settings.timezone,'Asia/Kolkata');assert.deepEqual(w.pinData,{});
 for(const n of w.nodes){assert.equal(n.credentials,undefined);if(n.type.endsWith('code')){assert.equal(n.parameters.language,'javaScript');assert.equal(n.parameters.mode,'runOnceForAllItems');}if(n.type.endsWith('scheduleTrigger'))assert.equal(n.disabled,true);if(n.type.endsWith('httpRequest')){assert.equal(n.retryOnFail,false);assert.equal(n.onError,'continueErrorOutput');assert.equal(n.parameters.options.timeout,300000);}}
 const names=new Set(w.nodes.map(n=>n.name));for(const [source,value] of Object.entries(w.connections)){assert(names.has(source));for(const output of value.main)for(const target of output)assert(names.has(target.node));}
}
console.log('Phase 10 workflow logic and export controls passed.');
