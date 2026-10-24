import type { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Context = { params: Promise<{ path: string[] }> };

async function forward(request: NextRequest, context: Context) {
  const { path } = await context.params;
  const base = process.env.API_BASE_URL;
  if (!base) {
    return Response.json({ detail: "API is not configured" }, { status: 503 });
  }
  const url = new URL(path.map(encodeURIComponent).join("/"), `${base}/`);
  url.search = request.nextUrl.search;
  const headers = new Headers();
  for (const name of ["accept", "content-type", "cookie", "origin", "x-csrf-token", "if-match"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers,
    cache: "no-store",
    // Cookies are forwarded explicitly above. Disable Fetch's automatic HTTP
    // authentication retry, which cannot replay a streamed body after a 401.
    credentials: "omit",
    redirect: "manual",
    signal: AbortSignal.timeout(120_000),
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = request.body;
    init.duplex = "half";
  }
  try {
    const upstream = await fetch(url, init);
    const responseHeaders = new Headers(upstream.headers);
    for (const name of ["connection", "keep-alive", "transfer-encoding", "content-encoding", "content-length", "set-cookie"]) {
      responseHeaders.delete(name);
    }
    for (const cookie of upstream.headers.getSetCookie()) responseHeaders.append("set-cookie", cookie);
    responseHeaders.set("cache-control", "no-store");
    return new Response(request.method === "HEAD" ? null : upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch (error) {
    // Log transport failures without request headers, credentials, or payloads.
    console.error("API proxy failure", error instanceof Error ? error.message : "Unknown transport error",
      error instanceof Error && error.cause instanceof Error ? error.cause.message : "");
    return Response.json({ detail: "API temporarily unavailable" }, {
      status: 503, headers: { "cache-control": "no-store" },
    });
  }
}

export { forward as GET, forward as HEAD, forward as POST, forward as PUT, forward as PATCH, forward as DELETE, forward as OPTIONS };
