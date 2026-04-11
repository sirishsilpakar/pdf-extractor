import os
import pymupdf
import logging
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import asyncio
import threading
import json
from fastapi.responses import StreamingResponse
from operator import itemgetter
from job_runner import Job, _job_queue, _jobs, BUFFER_SIZE
import job_runner
import uuid

# Import your custom modules
from extractor import run_pipeline
from config import OMP_THREAD_LIMIT

# 1. Initialize the FastAPI App
app = FastAPI(
    title="PDF Extraction API",
    description="API for processing and extracting text/OCR from directories of PDFs."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows requests from any origin (e.g., your React app)
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods, including OPTIONS and POST
    allow_headers=["*"],  # Allows all headers, including Content-Type
)

# 2. Define the Request Payload (Replaces argparse)
class ExtractionRequest(BaseModel):
    # Original arguments
    input_directory: str = Field(..., description="Server path to the directory containing PDFs")
    output_dir: str = Field(default="extracted_files", description="Directory to save extracted files")
    workers: Optional[int] = None
    enable_page_ocr: bool = False
    page_ocr_workers: Optional[int] = None
    force: bool = False
    no_ocr: bool = False
    fast: bool = False
    
    # New boolean arguments
    removeHeader: bool = False
    removeFooter: bool = False
    removePageNumber: bool = False
    removeNumerics: bool = False
    lemma: bool = False

# 3. Create the API Endpoint
@app.post("/extract")
def trigger_extraction(request: ExtractionRequest):
    """
    Triggers the PDF extraction pipeline. 
    Expects a JSON payload with configuration parameters.
    """
    
    # Validate input directory
    if not os.path.isdir(request.input_directory):
        raise HTTPException(
            status_code=400, 
            detail=f"The directory '{request.input_directory}' was not found on the server."
        )

    # --- Apply Environment Variables ---
    if request.no_ocr:
        os.environ["DISABLE_OCR"] = "true"
    else:
        # Clear it just in case a previous request set it
        os.environ.pop("DISABLE_OCR", None) 

    if request.fast:
        os.environ["OCR_DPI"] = "150"
        os.environ["TESSERACT_PAGE_TIMEOUT_SECONDS"] = "15"
        os.environ["OCR_ON_IMAGE_AREA_THRESHOLD"] = "0.30"
        logging.info("Fast mode enabled: DPI=150, Timeout=15s, ImageThreshold=0.30")
    
    # Limit OpenMP threads
    os.environ.setdefault("OMP_THREAD_LIMIT", str(OMP_THREAD_LIMIT))
    os.environ.setdefault("OMP_NUM_THREADS", str(OMP_THREAD_LIMIT))

    # Export page-level OCR options
    if request.enable_page_ocr:
        os.environ["ENABLE_PAGE_LEVEL_OCR_THREADS"] = "true"
    if request.page_ocr_workers is not None:
        os.environ["PAGE_LEVEL_OCR_MAX_WORKERS"] = str(max(1, request.page_ocr_workers))

    # Disable mupdf errors
    pymupdf.TOOLS.mupdf_display_errors(False)

    # Worker override
    if request.workers is not None and request.workers > 0:
        os.environ["WORKERS_OVERRIDE"] = str(request.workers)

    # --- Run the Pipeline ---
    try:
        # Note: I am passing the new arguments into run_pipeline here.
        # You will need to update the run_pipeline function in extractor.py to accept them!
        run_pipeline(
            input_dir=request.input_directory,
            output_dir=request.output_dir,
            force=request.force,
            remove_header=request.removeHeader,
            remove_footer=request.removeFooter,
            remove_page_number=request.removePageNumber,
            remove_numerics=request.removeNumerics,
            lemma=request.lemma
        )
        
        return {
            "status": "success", 
            "message": f"Successfully processed files in {request.input_directory}",
            "output_directory": request.output_dir
        }
        
    except Exception as e:
        logging.exception(e)
        # Return a 500 Internal Server Error if the pipeline crashes
        raise HTTPException(status_code=500, detail=f"A critical error occurred: {str(e)}")
    

@app.post("/extract/stream")
async def extract_stream(request: ExtractionRequest):
    """
    Starts the extraction and streams progress back to the client via SSE.
    """
    if not os.path.isdir(request.input_directory):
        raise HTTPException(status_code=400, detail="Directory not found.")

    # 1. Setup Environment Variables
    if request.no_ocr: os.environ["DISABLE_OCR"] = "true"
    else: os.environ.pop("DISABLE_OCR", None)

    if request.fast:
        os.environ["OCR_DPI"] = "150"
        os.environ["TESSERACT_PAGE_TIMEOUT_SECONDS"] = "15"
        os.environ["OCR_ON_IMAGE_AREA_THRESHOLD"] = "0.30"
        
    os.environ.setdefault("OMP_THREAD_LIMIT", str(OMP_THREAD_LIMIT))
    os.environ.setdefault("OMP_NUM_THREADS", str(OMP_THREAD_LIMIT))

    if request.enable_page_ocr:
        os.environ["ENABLE_PAGE_LEVEL_OCR_THREADS"] = "true"
    if request.page_ocr_workers:
        os.environ["PAGE_LEVEL_OCR_MAX_WORKERS"] = str(max(1, request.page_ocr_workers))

    pymupdf.TOOLS.mupdf_display_errors(False)

    # 2. Setup an Async Queue to pass messages from the sync thread to the async stream
    queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    # 3. The Callback: Safely puts progress into the async queue from the background thread
    def progress_callback(payload: dict):
        current, total = itemgetter("current", "total")(payload)
        percentage = round((current / total) * 100) if total > 0 else 0
        payload = {**payload, "percentage": percentage}
        # message = {"status": "processing", "current": current, "total": total, "file_path": file_path, "elapsed": elapsed, "percentage": percentage}
        # Thread-safe way to talk to asyncio
        asyncio.run_coroutine_threadsafe(queue.put(payload), loop)

    # 4. The Background Worker
    def worker():
        try:
            run_pipeline(
                input_dir=request.input_directory,
                output_dir=request.output_dir,
                force=request.force,
                remove_header=request.removeHeader,
                remove_footer=request.removeFooter,
                remove_page_number=request.removePageNumber,
                remove_numerics=request.removeNumerics,
                lemma=request.lemma,
                progress_callback=progress_callback
            )
            # Signal that we are done
            asyncio.run_coroutine_threadsafe(queue.put({"status": "completed"}), loop)
        except Exception as e:
            logging.exception(e)
            asyncio.run_coroutine_threadsafe(queue.put({"status": "error", "message": str(e)}), loop)

    # Start the worker in a separate thread so it doesn't block the FastAPI stream
    threading.Thread(target=worker, daemon=True).start()

    # 5. The Async Generator that yields Server-Sent Events (SSE) formatting
    async def event_generator():
        while True:
            # Wait for the next message from the queue
            data = await queue.get()
            
            # SSE requires data to be formatted as: data: <json_string>\n\n
            yield f"data: {json.dumps(data)}\n\n"
            
            # Stop the stream if completed or error occurred
            if data["status"] in ["completed", "error"]:
                break

    # 6. Return the StreamingResponse with the specific SSE media type
    return StreamingResponse(event_generator(), media_type="text/event-stream")