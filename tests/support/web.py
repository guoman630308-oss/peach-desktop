"""HTTP 请求契约夹具；后台服务生命周期由集成用例验证。"""
from contextlib import contextmanager


@contextmanager
def route_client(app):
    """请求经过真实路由、中间件与依赖，只管理 HTTP 客户端的关闭。"""
    from fastapi.testclient import TestClient

    client = TestClient(app)
    try:
        yield client
    finally:
        client.close()
