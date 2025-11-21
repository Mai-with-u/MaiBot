from fastapi import FastAPI, APIRouter
from typing import Optional
from uvicorn import Config, Server as UvicornServer
import asyncio
import os
from rich.traceback import install

install(extra_lines=3)


class Server:
    def __init__(self, host: Optional[str] = None, port: Optional[int] = None, app_name: str = "MaiMCore"):
        self.app = FastAPI(title=app_name)
        self._host: str = "127.0.0.1"
        self._port: int = 8080
        self._server: Optional[UvicornServer] = None
        self.set_address(host, port)

    def register_router(self, router: APIRouter, prefix: str = ""):
        """注册路由

        APIRouter 用于对相关的路由端点进行分组和模块化管理：
        1. 可以将相关的端点组织在一起，便于管理
        2. 支持添加统一的路由前缀
        3. 可以为一组路由添加共同的依赖项、标签等

        示例:
            router = APIRouter()

            @router.get("/users")
            def get_users():
                return {"users": [...]}

            @router.post("/users")
            def create_user():
                return {"msg": "user created"}

            # 注册路由，添加前缀 "/api/v1"
            server.register_router(router, prefix="/api/v1")
        """
        self.app.include_router(router, prefix=prefix)

    def set_address(self, host: Optional[str] = None, port: Optional[int] = None):
        """设置服务器地址和端口"""
        if host:
            self._host = host
        if port:
            self._port = port

    async def run(self):
        """启动服务器"""
        # 禁用 uvicorn 默认日志和访问日志
        config = Config(app=self.app, host=self._host, port=self._port, log_config=None, access_log=False)
        self._server = UvicornServer(config=config)
        try:
            await self._server.serve()
        except KeyboardInterrupt:
            await self.shutdown()
            raise
        except Exception as e:
            await self.shutdown()
            raise RuntimeError(f"服务器运行错误: {str(e)}") from e
        finally:
            await self.shutdown()

    async def shutdown(self):
        """安全关闭服务器"""
        if self._server:
            self._server.should_exit = True
            try:
                # 添加 3 秒超时，避免 shutdown 永久挂起
                await asyncio.wait_for(self._server.shutdown(), timeout=3.0)
            except asyncio.TimeoutError:
                # 超时就强制标记为 None，让垃圾回收处理
                pass
            except Exception:
                # 忽略其他异常
                pass
            finally:
                self._server = None

    def get_app(self) -> FastAPI:
        """获取 FastAPI 实例"""
        return self.app


global_server = None


def get_global_server() -> Server:
    """获取全局服务器实例"""
    global global_server
    if global_server is None:
        global_server = Server(host=os.environ["HOST"], port=int(os.environ["PORT"]))
    return global_server
