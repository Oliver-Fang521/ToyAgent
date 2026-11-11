import {handle} from '../handler';
export const dynamic='force-dynamic';
export const POST=(request:Request)=>handle(request,'login');
