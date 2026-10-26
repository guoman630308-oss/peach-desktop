"""HTTP 入口只负责探活和导航到固定 HTTPS origin。"""
from urllib.parse import quote, urlencode, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse


def create_redirect_app(origin: str) -> FastAPI:
    target = urlsplit(origin)
    if (target.scheme != "https" or not target.hostname or target.username
            or target.password or target.path not in ("", "/")
            or target.query or target.fragment):
        raise ValueError("redirect origin must be an HTTPS origin")
    origin = origin.rstrip("/")
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.api_route("/healthz", methods=["GET", "HEAD"])
    def healthz():
        return {"ok": True, "service": "peach-redirect", "scheme": "http"}

    @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    def redirect(request: Request, path: str):
        # 写请求不能因 307/308 在另一个 origin 自动重发口令或正文。
        if request.method not in {"GET", "HEAD"}:
            return JSONResponse({"error": "HTTPS required"}, status_code=426,
                                headers={"Cache-Control": "no-store"})
        # 固定 origin，不使用来访 Host；不转发查询串中的口令。
        query = request.query_params.multi_items()
        query = urlencode([(key, value) for key, value in query if key != "t"])
        location = origin + "/" + quote(path, safe="/@:")
        if query:
            location += "?" + query
        return RedirectResponse(location, status_code=302,
                                headers={"Cache-Control": "no-store"})

    return app
