from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from .schemas import OptionsResponse, StatsSummaryResponse, TaskCreateResponse, TaskStatusResponse

router = APIRouter()


@router.get('/api/options', response_model=OptionsResponse)
def get_options(request: Request):
    return request.app.state.task_service.list_options()


@router.post('/api/tasks', response_model=TaskCreateResponse)
async def create_task(
    request: Request,
    group_name: str = Form(...),
    group_password: str = Form(...),
    operator_name: str = Form(''),
    site: str = Form(...),
    fission_type: str = Form(...),
    model_id: str = Form(...),
    count: int = Form(...),
    notes: str = Form(''),
    product_image: UploadFile = File(...),
    reference_image: UploadFile = File(...),
):
    try:
        return request.app.state.task_service.create_task(
            group_name=group_name,
            group_password=group_password,
            operator_name=operator_name,
            site=site,
            fission_type=fission_type,
            model_id=model_id,
            count=count,
            notes=notes,
            product_image_bytes=await product_image.read(),
            product_image_name=product_image.filename or 'product.png',
            reference_image_bytes=await reference_image.read(),
            reference_image_name=reference_image.filename or 'reference.png',
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get('/api/tasks/{task_id}', response_model=TaskStatusResponse)
def get_task_status(task_id: str, request: Request):
    try:
        return request.app.state.task_service.get_task_status(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get('/api/tasks/{task_id}/download')
def download_task(task_id: str, request: Request):
    try:
        path = request.app.state.task_service.get_download_path(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, filename=f'{task_id}.zip', media_type='application/zip')


@router.get('/api/tasks/{task_id}/files/{relative_path:path}')
def read_task_file(task_id: str, relative_path: str, request: Request):
    try:
        path = request.app.state.task_service.get_task_file_path(task_id, relative_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path)


@router.get('/api/stats/summary', response_model=StatsSummaryResponse)
def get_stats_summary(request: Request):
    return request.app.state.task_service.stats_summary()
