const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function element(){return {textContent:'',children:[],attributes:{},listeners:{},appendChild(child){this.children.push(child);return child;},setAttribute(key,value){this.attributes[key]=value;},addEventListener(name,fn){this.listeners[name]=fn;}};}
const ids=Object.fromEntries(['node-buttons','node-purpose','node-config','node-input','node-output','node-business'].map(id=>[id,element()]));
const document={querySelector(selector){assert(ids[selector.slice(1)],selector);return ids[selector.slice(1)];},createElement:element,createTextNode(text){return {textContent:text};}};
const scope={document,fetch(){throw Error('Public walkthrough must not access a network.');},XMLHttpRequest(){throw Error('Public walkthrough must not access a network.');}};
vm.createContext(scope);vm.runInContext(fs.readFileSync(path.join(__dirname,'../site/n8n-demo.js'),'utf8'),scope);
const buttons=ids['node-buttons'].children;assert.equal(buttons.length,6);
assert.deepEqual(JSON.parse(ids['node-input'].textContent),[]);assert.deepEqual(JSON.parse(ids['node-output'].textContent),[{}]);
for(let i=0;i<buttons.length;i++){
 buttons[i].listeners.click();assert.equal(buttons.filter(b=>b.attributes['aria-pressed']==='true').length,1);assert.equal(buttons[i].attributes['aria-pressed'],'true');
 const input=JSON.parse(ids['node-input'].textContent),output=JSON.parse(ids['node-output'].textContent);
 if(i===1){assert.equal(output[0].body.data.deliveries,5);assert.equal(output[0].statusCode,200);}
 if(i===2){assert.equal(input[0].body.code,'INTAKE_COMPLETED');assert.equal(output[0].request_ok,true);assert.equal(output[0].attention_required,true);assert.equal(output[0].new_extraction_budget,0);}
 if(i===3||i===4){assert.deepEqual(output.true_branch,input);assert.deepEqual(output.false_branch,[]);}
 if(i===5){assert.deepEqual(output,input);assert.equal(output[0].counts.held,2);assert(ids['node-business'].textContent.includes('does not open a page'));}
}
buttons[0].listeners.click();assert.deepEqual(JSON.parse(ids['node-output'].textContent),[{}]);
console.log('All six n8n walkthrough steps, routing and reset selection passed without provider access.');
