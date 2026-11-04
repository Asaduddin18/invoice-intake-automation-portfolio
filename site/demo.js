'use strict';
let cases=[],selected=0,step=0;
const labels=['Email receipt','Extraction & checks','Accounting decision','Monthly report'];
const el=id=>document.getElementById(id);
const text=(id,value)=>{el(id).textContent=value;};
function render(){
 const c=cases[selected];
 document.querySelectorAll('#scenarios button').forEach((b,i)=>{b.classList.toggle('active',i===selected);b.setAttribute('aria-pressed',String(i===selected));});
 text('case-name',c.name);text('supplier',c.supplier||'No invoice fields');text('number',c.number||'Missing');text('total',c.total?'£'+c.total:'Not applicable');
 const badge=el('badge');badge.textContent=step===4?(c.outcome==='verified'?'Verified draft':c.outcome==='duplicate'?'Duplicate linked':c.outcome==='held'?'Held':'Unsupported'):'Replay · step '+step+'/4';badge.className='badge '+(['held','unsupported'].includes(c.outcome)?'held':'');
 el('items').replaceChildren();
 for(const item of c.items){const row=document.createElement('tr');for(const value of [item.description,item.quantity,'£'+item.unit_price,'£'+item.line_amount]){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}el('items').append(row);}
 el('source').hidden=!c.source;if(c.source)el('source').href=c.source;
 el('flow').replaceChildren();labels.forEach((label,i)=>{const li=document.createElement('li');li.textContent=(i+1)+'. '+label;li.className=i<step?'done':i===step?'current':'';el('flow').append(li);});
 const descriptions=[
 'Start the replay to see how this synthetic arrival is handled.',
 'A durable delivery receipt is recorded before extraction. Message and MIME part identify the source.',
 c.outcome==='unsupported'?'Unsupported attachment: no PDF extraction or model request.':'Printed fields are extracted and checked. The model does not decide supplier mappings or whether a bill may be created.',
 c.reason,
 c.outcome==='verified'||c.outcome==='duplicate'?'Report result: the original and duplicate share one verified £95.16 obligation. Repeated reporting does not create another bill.':'Report result: this exception stays visible. It contributes nothing to the counted draft total.'
 ];text('outcome',descriptions[step]);el('outcome').className='outcome '+(step>=3&&['held','unsupported'].includes(c.outcome)?'held':'');
 el('run').textContent=step===4?'Replay complete':'Step through the workflow →';el('run').disabled=step===4;
}
fetch('data.json').then(r=>{if(!r.ok)throw Error();return r.json();}).then(data=>{cases=data;data.forEach((c,i)=>{const b=document.createElement('button');b.type='button';b.textContent=c.name;b.addEventListener('click',()=>{selected=i;step=0;render();});el('scenarios').append(b);});el('run').addEventListener('click',()=>{step=Math.min(4,step+1);render();});el('reset').addEventListener('click',()=>{step=0;render();});render();}).catch(()=>text('case-name','Serve this folder over HTTP to load the replay.'));
