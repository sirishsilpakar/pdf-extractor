# In your main.py
import threading
import json
import os
import pymupdf
from operator import itemgetter
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from job_runner import Job, _job_queue, _jobs, BUFFER_SIZE
import job_runner
import uuid
import asyncio
import logging

app = FastAPI()

class ExtractionRequest(BaseModel):
    input_directory: str
    output_dir: str = "extracted_files"
    force: bool = False
    no_ocr: bool = False
    fast: bool = False
    enable_page_ocr: bool = False
    page_ocr_workers: Optional[int] = None
    removeHeader: bool = False
    removeFooter: bool = False
    removePageNumber: bool = False
    removeNumerics: bool = False
    lemma: bool = False


# ── Helper: publish an event to a job's queue + buffer ──────────────────────
async def _publish(job: Job, payload: dict):
    # Write to rolling buffer for reconnecting subscribers (layer 5)
    job.event_buffer.append(payload)
    if len(job.event_buffer) > BUFFER_SIZE:
        job.event_buffer.pop(0)
    await job.event_queue.put(payload)


# ── The single global consumer loop (layer 4) ───────────────────────────────
async def _job_consumer():
    """
    Runs forever. Picks one job at a time from the queue,
    runs it to completion, then picks the next.
    Serializes all pipeline execution — no concurrent runs.
    """
    while True:
        job: Job = await _job_queue.get()
        loop = asyncio.get_running_loop()

        job.status = "running"
        job.started_at = datetime.utcnow().isoformat()
        await _publish(job, {"status": "started", "job_id": job.job_id})

        # Build progress callback that bridges sync thread → async queue
        def progress_callback(payload: dict):
            current, total = itemgetter("current", "total")(payload)
            percentage = round((current / total) * 100) if total > 0 else 0
            enriched = {**payload, "percentage": percentage, "job_id": job.job_id}
            asyncio.run_coroutine_threadsafe(_publish(job, enriched), loop)

        # Run the blocking pipeline in a thread so we don't block the event loop
        def worker():
            try:
                _apply_env(job.options)
                run_pipeline(
                    input_dir=job.input_directory,
                    output_dir=job.output_dir,
                    force=job.options["force"],
                    remove_header=job.options["removeHeader"],
                    remove_footer=job.options["removeFooter"],
                    remove_page_number=job.options["removePageNumber"],
                    remove_numerics=job.options["removeNumerics"],
                    lemma=job.options["lemma"],
                    progress_callback=progress_callback,
                )
                asyncio.run_coroutine_threadsafe(
                    _publish(job, {"status": "completed", "job_id": job.job_id}), loop
                )
            except Exception as e:
                logging.exception(e)
                asyncio.run_coroutine_threadsafe(
                    _publish(job, {"status": "error", "message": str(e), "job_id": job.job_id}), loop
                )

        # Block the consumer until this job's thread finishes
        # This is what serializes jobs — next job won't start until done_event is set
        done_event = asyncio.Event()

        def worker_with_signal():
            worker()
            loop.call_soon_threadsafe(done_event.set)

        threading.Thread(target=worker_with_signal, daemon=True).start()
        await done_event.wait()

        job.status = "completed" if job.error is None else "failed"
        job.finished_at = datetime.utcnow().isoformat()
        _job_queue.task_done()


def _apply_env(options: dict):
    """Applies all env-var side effects from job options."""
    if options.get("no_ocr"):
        os.environ["DISABLE_OCR"] = "true"
    else:
        os.environ.pop("DISABLE_OCR", None)

    if options.get("fast"):
        os.environ["OCR_DPI"] = "150"
        os.environ["TESSERACT_PAGE_TIMEOUT_SECONDS"] = "15"
        os.environ["OCR_ON_IMAGE_AREA_THRESHOLD"] = "0.30"

    os.environ.setdefault("OMP_THREAD_LIMIT", str(OMP_THREAD_LIMIT))
    os.environ.setdefault("OMP_NUM_THREADS", str(OMP_THREAD_LIMIT))

    if options.get("enable_page_ocr"):
        os.environ["ENABLE_PAGE_LEVEL_OCR_THREADS"] = "true"
    if options.get("page_ocr_workers"):
        os.environ["PAGE_LEVEL_OCR_MAX_WORKERS"] = str(max(1, options["page_ocr_workers"]))

    pymupdf.TOOLS.mupdf_display_errors(False)


# ── Start the consumer once on app startup ──────────────────────────────────
@app.on_event("startup")
async def startup():
    asyncio.create_task(_job_consumer())


# ── POST /extract  (layer 2 + 3) ─────────────────────────────────────────────
@app.post("/extract")
async def submit_job(request: ExtractionRequest):
    """
    Accepts a job, adds it to the FIFO queue, returns job_id immediately.
    Does NOT start streaming here — frontend uses job_id to subscribe via SSE.
    """
    if not os.path.isdir(request.input_directory):
        raise HTTPException(status_code=400, detail="Directory not found.")

    job = Job(
        job_id=str(uuid.uuid4()),
        input_directory=request.input_directory,
        output_dir=request.output_dir,
        options=request.model_dump(),
    )

    _jobs[job.job_id] = job              # register in global registry
    await _job_queue.put(job)            # enqueue — consumer picks it up

    queue_position = _job_queue.qsize()  # how many jobs ahead of this one

    return {
        "job_id": job.job_id,
        "status": job.status,            # "queued"
        "queue_position": queue_position,
        "submitted_at": job.submitted_at,
    }