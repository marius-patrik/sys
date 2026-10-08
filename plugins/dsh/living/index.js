// One DSH capability bridge. Model inventory/inference are owned by LiteLLM,
// reached by livingd using PostgreSQL-stored gateway configuration.
import { createServer } from 'node:http';
import { randomUUID } from 'node:crypto';
export const name='living-tool-bridge';
export const inject=['tools'];
export function apply(ctx){
  const host=process.env.LIVING_DSH_LISTEN||'127.0.0.1';
  const port=Number(process.env.LIVING_DSH_PORT||'8090');
  const token=process.env.LIVING_DSH_TOKEN;
  if(!token||token.length<12)throw new Error('DSH bridge bootstrap token is required');
  const server=createServer(async(req,res)=>{
    const send=(status,value)=>{
      const body=JSON.stringify(value);
      res.writeHead(status,{'content-type':'application/json','content-length':Buffer.byteLength(body)});
      res.end(body);
    };
    if(req.method!=='POST'||req.url!=='/v1/nodes/tool')return send(404,{error:'not found'});
    if(req.headers.authorization!=='Bearer '+token)return send(401,{error:'unauthorized'});
    try{
      let raw='';
      for await(const part of req){
        raw+=part;
        if(raw.length>32768)throw new Error('request too large');
      }
      const data=JSON.parse(raw);
      if(typeof data.name!=='string'||!data.name||
        !data.arguments||typeof data.arguments!=='object'||Array.isArray(data.arguments))
        throw new Error('invalid tool invocation');
      const callId=typeof data.call_id==='string'&&data.call_id.length<=128
        ?data.call_id:randomUUID();
      const output=await ctx.tools.execute({
        callId,name:data.name,arguments:data.arguments,
        signal:AbortSignal.timeout(30000)
      });
      send(200,{outcome:output});
    }catch(err){send(400,{error:String(err.message||err)})}
  });
  server.listen(port,host);
  ctx.on('dispose',()=>server.close());
}
