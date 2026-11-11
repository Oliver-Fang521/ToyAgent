type Store = {put:(key:string,value:unknown)=>Promise<void>;take:(key:string)=>Promise<unknown>};
const lifetime=15*60*1000;

export async function saveSigninAttachment(store:Store,id:string,file:File,owner:string|null,now=Date.now()){
 if(file.size>32*1024*1024)throw new Error("file_too_large");
 await store.put(id,{blob:file,name:file.name,type:file.type,lastModified:file.lastModified,owner,createdAt:now});
}
export async function takeSigninAttachment(store:Store,id:string,owner:string,now=Date.now()):Promise<File|null>{
 const raw=await store.take(id);
 if(!raw||typeof raw!=="object")return null;
 const item=raw as {blob?:Blob;name?:string;type?:string;lastModified?:number;owner?:string|null;createdAt?:number};
 if(!(item.blob instanceof Blob)||typeof item.name!=="string"||typeof item.createdAt!=="number"||!Number.isFinite(item.createdAt)||now<item.createdAt||now-item.createdAt>lifetime||(item.owner!==null&&item.owner!==owner)||item.blob.size>32*1024*1024)return null;
 return new File([item.blob],item.name,{type:item.type||"",lastModified:item.lastModified});
}

export function browserAttachmentStore():Store{
 function open():Promise<IDBDatabase>{return new Promise((resolve,reject)=>{
  const request=indexedDB.open("toy-agent-signin-attachments",1);
  request.onupgradeneeded=()=>request.result.createObjectStore("drafts");
  request.onerror=()=>reject(request.error);request.onblocked=()=>reject(new Error("attachment_storage_unavailable"));
  request.onsuccess=()=>resolve(request.result);
 });}
 async function operation(key:string,value?:unknown):Promise<unknown>{
  const db=await open();
  return new Promise((resolve,reject)=>{
   const transaction=db.transaction("drafts","readwrite"), table=transaction.objectStore("drafts");
   let result:unknown;
   if(value!==undefined)table.put(value,key);
   else {const request=table.get(key);request.onsuccess=()=>{result=request.result;table.delete(key)};}
   const cursor=table.openCursor();cursor.onsuccess=()=>{const entry=cursor.result;if(!entry)return;const created=entry.value?.createdAt;if(typeof created!=="number"||Date.now()-created>lifetime||created>Date.now())entry.delete();entry.continue();};
   transaction.oncomplete=()=>{db.close();resolve(result)};
   transaction.onerror=()=>{db.close();reject(transaction.error)};
   transaction.onabort=()=>{db.close();reject(transaction.error||new Error("attachment_storage_unavailable"))};
  });
 }
 return {put:async(key,value)=>{await operation(key,value)},take:async key=>operation(key)};
}
