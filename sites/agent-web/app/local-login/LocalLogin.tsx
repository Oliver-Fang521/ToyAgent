'use client';
import {useState} from 'react';
import {useWorkspaceLocale} from '../components/WorkspaceLocale';
import LanguageMenu from '../components/LanguageMenu';
export default function LocalLogin(){
 const {locale}=useWorkspaceLocale(),en=locale==='en';const [busy,setBusy]=useState(false),[error,setError]=useState('');
 async function submit(event:React.FormEvent<HTMLFormElement>){
  event.preventDefault();if(busy)return;setBusy(true);setError('');
  const form=event.currentTarget;
  try{const body=new URLSearchParams();body.set('password',String(new FormData(form).get('password')||''));
   body.set('return_to',new URLSearchParams(location.search).get('return_to')||'/');
   const response=await fetch('/api/local-auth/login',{method:'POST',body});
   if(response.ok&&response.redirected){location.assign(response.url);return;}
   setError(response.status===429?(en?'Too many attempts. Try again later.':'尝试次数过多，请稍后再试。'):
    response.status===503?(en?'Run initialization before signing in.':'请先完成本地初始化。'):(en?'Unable to sign in. Check your password.':'登录失败，请检查密码。'));
  }catch{setError(en?'Connection failed. Please try again.':'连接失败，请重试。');}
  finally{setBusy(false);form.reset();}
 }
 return <main className="local-login"><div className="local-login-language"><LanguageMenu/></div><section>
  <a className="local-login-brand" href="/">ToyAgent</a><h1>{en?'Your local workspace':'你的本地工作区'}</h1>
  <form onSubmit={submit}><label htmlFor="local-password">{en?'Access password':'访问密码'}</label>
   <input id="local-password" name="password" type="password" autoComplete="current-password" required maxLength={1024} autoFocus/>
   {error&&<p role="alert">{error}</p>}<button type="submit" disabled={busy}>{busy?(en?'Signing in…':'正在登录…'):(en?'Sign in':'登录')}</button>
  </form></section></main>;
}
