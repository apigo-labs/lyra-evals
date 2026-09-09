import readline from 'node:readline';
let pending = Promise.resolve();
const tool = {name:'tau_step',description:'Act in the benchmark environment. Send plain text as action to speak to the user; send a JSON string {"name":"tool_name","arguments":{...}} to invoke a domain tool. Tool schemas and policy are in the initial task. Stop when terminated is true.',inputSchema:{type:'object',properties:{action:{type:'string'}},required:['action'],additionalProperties:false}};
readline.createInterface({input:process.stdin}).on('line',line=>{
 pending = pending.then(async()=>{
  const request=JSON.parse(line); if(request.id===undefined)return;
  let result;
  if(request.method==='initialize')result={protocolVersion:request.params.protocolVersion,capabilities:{tools:{}},serverInfo:{name:'official-tau2-bridge',version:'1'}};
  else if(request.method==='tools/list')result={tools:[tool]};
  else if(request.method==='tools/call'&&request.params.name==='tau_step'){
   const response=await fetch('http://tau-runtime:8090/step',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(request.params.arguments),signal:AbortSignal.timeout(300000)});
   result={content:[{type:'text',text:await response.text()}],isError:!response.ok};
  } else if(request.method==='ping')result={};
  else throw Error('Unsupported method');
  process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:request.id,result})+'\n');
 }).catch(()=>{let r;try{r=JSON.parse(line);}catch{return;}if(r.id!==undefined)process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:r.id,error:{code:-32603,message:'Benchmark bridge failed'}})+'\n');});
});
