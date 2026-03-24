from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..config import bootstrap_runtime_environment
from ..services import FactoryTaskService
from .queue import WebTaskQueue
from .routes import router

bootstrap_runtime_environment()
STATIC_DIR = Path(__file__).with_name('static')


@asynccontextmanager
async def lifespan(app: FastAPI):
    task_service = FactoryTaskService()
    queue = WebTaskQueue(task_service)
    app.state.task_service = task_service
    app.state.task_queue = queue
    await queue.start()
    try:
        yield
    finally:
        await queue.stop()


app = FastAPI(title='TK 裂变素材工厂', version='0.1.0', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=str(STATIC_DIR)), name='static')
app.include_router(router)


@app.get('/')
def index():
    return FileResponse(STATIC_DIR / 'index.html')
