import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:net';
import { apply } from '../plugins/dsh/living/index.js';

async function port(){
 const server=createServer();
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const selected=server.address().port;
 await new Promise(resolve=>server.close(resolve));
 return selected
}
test('one DSH plugin executes a guarded tool call but never selects a model',async()=>{
 const p=await port();
 process.env.LIVING_DSH_LISTEN='127.0.0.1';
 process.env.LIVING_DSH_PORT=String(p);
 process.env.LIVING_DSH_TOKEN='test-integration-token';
 let called,dispose=()=>{};
 apply({
   on(event,fn){assert.equal(event,'dispose');dispose=fn},
   tools:{async execute(options){
     called=options;
     return {ok:true,content:{"count":4}}
   }}
 });
 const url='http://127.0.0.1:'+p+'/v1/nodes/tool';
 const payload=JSON.stringify({name:'test.count',call_id:'node-1',arguments:{n:4}});
 try{
   let response;
   for(let i=0;i<40;i++){
     try{
       response=await fetch(url,{method:'POST',headers:{
         'content-type':'application/json',
         authorization:'Bearer test-integration-token'
       },body:payload});break
     }catch{await new Promise(resolve=>setTimeout(resolve,10))}
   }
   assert.ok(response);
   assert.equal(response.status,200);
   assert.deepEqual(await response.json(),{outcome:{ok:true,content:{count:4}}});
   assert.equal(called.name,'test.count');
   assert.equal(called.callId,'node-1');
   assert.equal(called.arguments.n,4);
   assert.equal((await fetch(url,{method:'POST',body:payload})).status,401);
   assert.equal((await fetch('http://127.0.0.1:'+p+'/v1/nodes/model',{method:'POST'})).status,404);
 }finally{
   dispose();
   await new Promise(resolve=>setTimeout(resolve,40))
 }
});
