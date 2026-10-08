import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:net';
import { apply } from '../plugins/dsh/living/index.js';

async function port() {
  const server=createServer();
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const selected=server.address().port;
  await new Promise(resolve=>server.close(resolve));
  return selected;
}

test('single DSH plugin serves bounded graph model node without an agent loop',async () => {
  const p=await port();
  process.env.LIVING_DSH_LISTEN='127.0.0.1';
  process.env.LIVING_DSH_PORT=String(p);
  process.env.LIVING_DSH_TOKEN='test-integration-token';
  process.env.LIVING_DSH_PROVIDER='fake';
  process.env.LIVING_DSH_MODEL='fake-model';
  let supplied;
  let dispose=()=>{};
  const ctx={
    on(event,fn){assert.equal(event,'dispose');dispose=fn;},
    llm:{
      async *stream(options){
        supplied=options;
        yield {type:'text-delta',index:0,text:'Graph '};
        yield {type:'text-delta',index:0,text:'response'};
        yield {type:'finish',reason:{kind:'stop'}};
      }
    }
  };
  apply(ctx);
  try{
    const url='http://127.0.0.1:'+p+'/v1/nodes/model';
    const body=JSON.stringify({question:'Why?',context:'Known source',model:'ignored'});
    let response;
    for(let i=0;i<30;i++){
      try{
        response=await fetch(url,{method:'POST',headers:{
          'content-type':'application/json',
          'authorization':'Bearer test-integration-token'
        },body});
        break;
      }catch(e){await new Promise(resolve=>setTimeout(resolve,10));}
    }
    assert.ok(response,'bridge listener never became reachable');
    assert.equal(response.status,200);
    assert.deepEqual(await response.json(),{text:'Graph response'});
    assert.equal(supplied.provider,'fake');
    assert.equal(supplied.model,'fake-model');
    assert.match(supplied.messages[0].content[0].text,/Known source/);
    const unauthorized=await fetch(url,{method:'POST',headers:{
      'content-type':'application/json'
    },body});
    assert.equal(unauthorized.status,401);
  }finally{
    await new Promise(resolve=>{
      // Cordis's dispose callback closes the HTTP server.
      dispose();
      setTimeout(resolve,50);
    });
  }
});
