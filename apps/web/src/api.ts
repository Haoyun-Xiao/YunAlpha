import {appPath} from './deployment';

export interface Environment {workspace:string;lake_directory_readable:boolean;lake_access:string;worker_running:boolean;task_database_ready:boolean}
export interface Task {id:string;status:'queued'|'running'|'succeeded'|'failed';steps:string[];result:{message:string;artifact:string;lake_writes:boolean}|null;error:string|null;created_at:string;updated_at:string}
export interface Entry {kind:'local'|'legacy';label:string;href:string}
export interface CatalogItem {
  id:string;name:string;groups:string[];description:string;
  status:'available'|'planned'|'unavailable';entry:Entry|null;
}
export interface ReportItem {
  id:string;fund:{code:string;name:string};title:string;
  date:string|null;date_kind:'published'|'as_of'|null;entry:Entry;
  source?:string;status?:'formal'|'draft';version?:string;
}
export async function request<T>(path:string, method='GET', signal?:AbortSignal):Promise<T> {
  const response = await fetch(appPath(`/api/v1${path}`),{method,signal});
  if(!response.ok) {const body = await response.json().catch(()=>({}));throw new Error(body.detail || `请求失败 (${response.status})`);}
  return response.json();
}
