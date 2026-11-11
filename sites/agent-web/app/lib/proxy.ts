type Identity = { userId: string } | null;

export async function forwardAgentRequest(request: Request, path: string[], identity: Identity,
  settings: { base?: string; token?: string }, upstream: typeof fetch = fetch): Promise<Response> {
  const joined = path.join("/");
  const catalog=joined==="model-catalog"&&request.method==="GET";
  if (!identity&&!catalog) return Response.json({ error: "sign_in_required" }, { status: 401 });
  if (!/^(model-catalog(\/refresh)?|memory\/settings|memories(\/snapshot|\/search|\/maintenance|\/clear|\/(jobs|maintenance|barriers)\/[a-f0-9-]{36}\/(retry|restore)|\/[a-f0-9-]{36}\/(update|delete))?|credentials(\/(delete|migrate-model))?|credential-sessions|conversations|conversations\/[a-f0-9-]{36}|conversations\/[a-f0-9-]{36}\/(uploads|runs)|runs\/[a-f0-9-]{36}(\/(cancel|events))?)$/.test(joined))
    return Response.json({ error: "not_found" }, { status: 404 });
  if (!settings.base || !settings.token) return Response.json({ error: "service_not_configured" }, { status: 503 });
  if (request.method !== "GET" && request.headers.get("origin") !== new URL(request.url).origin)
    return Response.json({ error: "invalid_origin" }, { status: 403 });
  if (Number(request.headers.get("content-length") || 0) > 33 * 1024 * 1024)
    return Response.json({ error: "file_too_large" }, { status: 413 });
  if(joined==="model-catalog"&&!catalog)return Response.json({error:"not_found"},{status:404});
  const headers = new Headers({ authorization: `Bearer ${settings.token}`,...(identity?{"x-agent-user":identity.userId}:{}) });
  for (const key of ["content-type", "idempotency-key"])
    if (request.headers.has(key)) headers.set(key, request.headers.get(key)!);
  if (request.method === "POST" && ((path[0] === "conversations" && path.length === 3 && path[2] === "runs") || joined === "model-catalog/refresh") && request.headers.has("x-agent-credential-handle"))
    headers.set("x-agent-credential-handle", request.headers.get("x-agent-credential-handle")!);
  const offset = new URL(request.url).searchParams.get("offset");
  const paginated=(path[0]==="conversations"&&path.length===2)||(joined==="memories");
  let query = request.method === "GET" && paginated && offset && /^\d{1,9}$/.test(offset) ? `?offset=${offset}` : "";
  if(request.method==="GET"&&joined==="memories/search"){
    const source=new URL(request.url).searchParams,params=new URLSearchParams();
    for(const [name,max] of [["q",512],["cursor",4096],["limit",2]] as const){const value=source.get(name);if(value!==null){if(value.length>max)return Response.json({error:"memory_invalid"},{status:422});params.set(name,value);}}
    query=`?${params}`;
  }
  const streaming = request.method === "GET" && path[0] === "runs" && path[2] === "events";
  try {
    const response = await upstream(`${settings.base.replace(/\/$/, "")}/v1/${joined}${query}`, {
      method: request.method, headers, body: request.method === "GET" ? undefined : request.body,
      redirect: "manual", signal: streaming ? request.signal : AbortSignal.timeout(60_000),
      ...({ duplex: "half" } as Record<string, string>),
    });
    if(response.status>=300 && response.status<400) {
      await response.body?.cancel();
      return Response.json({error:"service_unreachable"},{status:503});
    }
    return new Response(response.body, { status: response.status,
      headers: { "content-type": streaming && response.ok ? "text/event-stream" : "application/json", "cache-control": "no-store", ...(streaming ? {"x-accel-buffering":"no"} : {}) } });
  } catch {
    return Response.json({ error: "service_unreachable" }, { status: 503 });
  }
}

export function safeSourceUrl(value: string): string | null {
  try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) ? url.href : null; }
  catch { return null; }
}
