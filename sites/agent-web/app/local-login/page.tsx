import {notFound,redirect} from 'next/navigation';
import {getAuthMode,getAppUser} from '../auth';
import {WorkspaceLocale} from '../components/WorkspaceLocale';
import LocalLogin from './LocalLogin';
export const dynamic='force-dynamic';
export default async function Page(){
 if(getAuthMode()!=='local')notFound();
 if(await getAppUser())redirect('/');
 return <WorkspaceLocale><LocalLogin/></WorkspaceLocale>;
}
