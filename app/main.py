from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import downloads
from .schemas import DownloadRequest, InfoRequest

BASE = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    downloads.init_storage()
    downloads.start_worker()
    yield
    if downloads.worker_task:
        downloads.worker_task.cancel()


app = FastAPI(title="Streamline", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.get("/")
async def home():
    return HTMLResponse((BASE / "templates" / "index.html").read_text(encoding="utf-8"))


@app.post("/api/info")
async def video_info(payload: InfoRequest):
    try:
        return await asyncio.to_thread(downloads.fetch_info, payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@app.post("/api/downloads")
async def create_download(payload: DownloadRequest):
    try:
        return await downloads.create_job(payload.url, payload.quality)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@app.get("/api/downloads")
async def download_history():
    return downloads.list_jobs()


@app.post("/api/downloads/{job_id}/cancel")
async def cancel_download(job_id: str):
    if not await downloads.cancel_job(job_id):
        raise HTTPException(status_code=404, detail="Download not found.")
    return {"ok": True}


@app.post("/api/downloads/{job_id}/retry")
async def retry_download(job_id: str):
    job = downloads.get_job(job_id)
    if not job or job["status"] not in {"failed", "cancelled"}:
        raise HTTPException(status_code=404, detail="A failed or cancelled download was not found.")
    try:
        return await downloads.create_job(job["url"], job["quality"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@app.delete("/api/downloads/{job_id}")
async def delete_download(job_id: str):
    if not downloads.delete_job_file(job_id):
        raise HTTPException(status_code=404, detail="Download not found.")
    return {"ok": True}


@app.get("/api/downloads/{job_id}/file")
async def get_download_file(job_id: str):
    job = downloads.get_job(job_id)
    if not job or job["status"] != "completed":
        raise HTTPException(status_code=404, detail="Completed download not found.")
    try:
        path = downloads.safe_download_path(job)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="The download file is missing.") from None
    return FileResponse(path, filename=path.name.split("-", 1)[-1], media_type="application/octet-stream")


@app.exception_handler(HTTPException)
async def http_error(_, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(_, __: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": "The request is invalid. Check the submitted fields."})
