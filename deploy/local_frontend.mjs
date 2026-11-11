import {readFileSync} from 'node:fs';
import {dirname,join} from 'node:path';
import {execve} from 'node:process';
const path=process.env.AGENT_LOCAL_CONFIG||'/run/toyagent/runtime.json';
try{
 if(readFileSync(join(dirname(path),'.complete'),'utf8')!=='1\n')throw Error();
 const content=readFileSync(path);if(content.length>8192)throw Error();
 const config=JSON.parse(content.toString());
 if(config.version!==1||typeof config.service_token!=='string'||config.service_token.length<32||!config.password||!config.session_key||!config.owner_id)throw Error();
 const env={PATH:process.env.PATH,HOME:process.env.HOME,NODE_ENV:'production',
  AGENT_AUTH_MODE:'local',AGENT_LOCAL_CONFIG:path,AGENT_LOCAL_ORIGIN:process.env.AGENT_LOCAL_ORIGIN||'http://localhost:3000',
  AGENT_BACKEND_URL:'http://backend:8000',AGENT_SERVICE_TOKEN:config.service_token};
 execve(process.execPath,[process.execPath,'node_modules/vinext/dist/cli.js','start','--hostname','0.0.0.0','--port','3000'],env);
}catch{
 console.error('local_configuration: run initialization; preserve existing secrets');process.exit(1);
}
