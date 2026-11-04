// Execute the replay against a small DOM test double. No browser or network API.
const fs=require('node:fs');const vm=require('node:vm');const assert=require('node:assert/strict');const path=require('node:path');
const root=path.resolve(__dirname,'..');const data=JSON.parse(fs.readFileSync(path.join(root,'site/data.json'),'utf8'));
class Element{
 constructor(){this.children=[];this.listeners={};this.textContent='';this.disabled=false;this.classList={toggle:()=>{}};}
 append(child){this.children.push(child);}replaceChildren(){this.children=[];}setAttribute(name,value){this[name]=value;}addEventListener(name,fn){this.listeners[name]=fn;}
}
const elements=new Map();const document={getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},createElement(){return new Element();},querySelectorAll(){return document.getElementById('scenarios').children;}};
const calls=[];const context=vm.createContext({document,fetch:async url=>{calls.push(url);assert.equal(url,'data.json');return{ok:true,json:async()=>data};}});
vm.runInContext(fs.readFileSync(path.join(root,'site/demo.js'),'utf8'),context);
setImmediate(()=>{
 try{
  const get=id=>document.getElementById(id);assert.equal(get('scenarios').children.length,5);
  for(let i=0;i<data.length;i++){
   get('scenarios').children[i].listeners.click();assert.equal(get('case-name').textContent,data[i].name);assert.equal(get('run').disabled,false);
   for(let j=0;j<3;j++)get('run').listeners.click();assert.equal(get('outcome').textContent,data[i].reason);
   get('run').listeners.click();assert.equal(get('run').disabled,true);assert.equal(get('flow').children.length,4);
   if(i===2)assert.equal(get('number').textContent,'Missing');
   if(i===3)assert.equal(get('total').textContent,'£553.13');
   if(i===4)assert.equal(get('source').hidden,true);
   get('reset').listeners.click();assert.equal(get('run').disabled,false);
  }
  assert.deepEqual(calls,['data.json']);console.log('Five replay scenarios, reset, held values and local-only data loading passed.');
 }catch(error){console.error(error);process.exitCode=1;}
});
