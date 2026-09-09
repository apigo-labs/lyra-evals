import readline from 'node:readline';
let pending = Promise.resolve();
const tool = {name:'swe_exec',description:'Run a bash command in /testbed with the official benchmark Python dependencies activated. The working tree is shared with /workspace. Commands time out after 60 seconds; output is bounded. Use this for repository tests and shell operations.',inputSchema:{type:'object',properties:{command:{type:'string'}},required:['command'],additionalProperties:false}};
readline.createInterface({input:process.stdin}).on('line',line=>{
 pending = pending.then(async()=>{
  const request=JSON.parse(line); if(request.id===undefined)return;
  let result;
  if(request.method==='initialize')result={protocolVersion:request.params.protocolVersion,capabilities:{tools:{}},serverInfo:{name:'official-swe-bridge',version:'1'}};
  else if(request.method==='tools/list')result={tools:[tool]};
  else if(request.method==='tools/call'&&request.params.name==='swe_exec'){
   const response=await fetch('http://swe-runtime:8091/exec',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(request.params.arguments),signal:AbortSignal.timeout(300000)});
   result={content:[{type:'text',text:await response.text()}],isError:!response.ok};
  } else if(request.method==='ping')result={};
  else throw Error('Unsupported method');
  process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:request.id,result})+'\n');
 }).catch(()=>{let r;try{r=JSON.parse(line);}catch{return;}if(r.id!==undefined)process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:r.id,error:{code:-32603,message:'Benchmark bridge failed'}})+'\n');});
});
