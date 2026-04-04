from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import shutil
import os
import sys
import json
import asyncio
import zipfile
import traceback
import subprocess
from pathlib import Path

from backend.timesheet_bot import process_timesheets, parse_excel, group_into_biweeks

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# ---------------------------------------------------------------------------
# win32print availability check (Windows-only)
# ---------------------------------------------------------------------------
def _check_win32print():
    try:
        import win32print
        return True
    except ImportError:
        return False

has_win32print = _check_win32print()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Shared state for progress reporting (keyed by a simple session token)
# ---------------------------------------------------------------------------
_progress_store = {}


def _send_progress(session_id, message, step=None, total=None, status="processing"):
    """Push a progress event into the store for a given session."""
    entry = {"message": message, "status": status}
    if step is not None:
        entry["step"] = step
    if total is not None:
        entry["total"] = total
    _progress_store.setdefault(session_id, []).append(entry)


# ---------------------------------------------------------------------------
# SSE Progress endpoint
# ---------------------------------------------------------------------------
@app.get("/api/progress/{session_id}")
async def progress_stream(session_id: str):
    """Server-Sent Events stream that pushes progress messages to the client."""
    async def event_generator():
        sent = 0
        while True:
            events = _progress_store.get(session_id, [])
            while sent < len(events):
                data = json.dumps(events[sent])
                yield f"data: {data}\n\n"
                if events[sent].get("status") in ("done", "error"):
                    # Clean up after final event
                    _progress_store.pop(session_id, None)
                    return
                sent += 1
            await asyncio.sleep(0.3)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Generate endpoint
# ---------------------------------------------------------------------------
@app.post("/api/generate")
async def generate_timesheets(
    file: UploadFile = File(...),
    initials: str = Form(...),
    hourly_rate: str = Form(...),
    headless: str = Form("true"),
    session_id: str = Form("default"),
):
    try:
        run_headless = headless.lower() == "true"
        rate = float(hourly_rate)

        _send_progress(session_id, "Received upload - saving file...", step=0)

        # Save uploaded file into "Excel Timesheets" directory
        timesheets_dir = os.path.abspath(os.path.join(os.getcwd(), "Excel Timesheets"))
        os.makedirs(timesheets_dir, exist_ok=True)

        file_path = os.path.join(timesheets_dir, file.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        _send_progress(session_id, f"File saved: {file.filename}")

        # PDF Outputs go to a temporary dir
        temp_dir = os.path.abspath("temp_processing")
        output_dir = os.path.join(temp_dir, "outputs")
        os.makedirs(output_dir, exist_ok=True)

        # Clear previous output files
        for f in os.listdir(output_dir):
            os.remove(os.path.join(output_dir, f))

        _send_progress(session_id, "Parsing Excel workbook...")

        # Pre-parse to count total biweeks for progress reporting
        from backend.timesheet_bot import parse_excel as _parse, group_into_biweeks as _group
        sheets_map = _parse(file_path, rate)
        total_pdfs = 0
        for entries in sheets_map.values():
            total_pdfs += len(_group(entries))

        if total_pdfs == 0:
            _send_progress(session_id, "No valid timesheet data found in the file.", status="error")
            return JSONResponse(status_code=400, content={"error": "No valid timesheet data found in the workbook."})

        _send_progress(
            session_id,
            f"Found {len(sheets_map)} sheet(s) with {total_pdfs} bi-weekly period(s) to process.",
            step=0,
            total=total_pdfs,
        )

        # Run the sync bot in a background thread
        _send_progress(session_id, "Launching Playwright browser (Sync API, background thread)...")

        def _run_bot():
            return process_timesheets(
                file_path, initials, rate, output_dir, run_headless,
                progress_callback=lambda msg, step=None, total=None: _send_progress(session_id, msg, step, total),
            )

        pdf_files = await asyncio.to_thread(_run_bot)

        if not pdf_files:
            _send_progress(session_id, "No PDFs were generated. Check server logs.", status="error")
            return JSONResponse(status_code=400, content={"error": "No PDFs were generated."})

        _send_progress(session_id, f"All {len(pdf_files)} PDF(s) generated successfully. Creating ZIP archive...")

        # Zip them up
        zip_path = os.path.join(temp_dir, "timesheets.zip")
        with zipfile.ZipFile(zip_path, "w") as zipf:
            for pdf in pdf_files:
                if os.path.isfile(pdf):
                    zipf.write(pdf, os.path.basename(pdf))
                else:
                    _send_progress(session_id, f"WARNING: Expected PDF not found on disk: {os.path.basename(pdf)}")

        zip_size = os.path.getsize(zip_path)
        _send_progress(
            session_id,
            f"ZIP archive ready ({zip_size // 1024} KB, {len(pdf_files)} file(s)). Sending download...",
            status="done",
        )

        return FileResponse(path=zip_path, filename="timesheets.zip", media_type="application/zip")

    except Exception as e:
        traceback.print_exc()
        _send_progress(session_id, f"ERROR: {str(e)}", status="error")
        return JSONResponse(status_code=500, content={"error": str(e)})


# ---------------------------------------------------------------------------
# win32print status & install endpoints
# ---------------------------------------------------------------------------
@app.get("/api/win32print-status")
async def win32print_status():
    """Returns whether win32print (pywin32) is currently installed."""
    available = _check_win32print()
    return JSONResponse(content={"available": available})


@app.post("/api/install-win32print")
async def install_win32print():
    """Attempt to install pywin32 via pip in a subprocess."""
    if sys.platform != "win32":
        return JSONResponse(
            status_code=400,
            content={"error": "win32print is only supported on Windows."}
        )
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "pip", "install", "pywin32"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            global has_win32print
            has_win32print = _check_win32print()
            return JSONResponse(content={
                "success": True,
                "message": "pywin32 installed successfully. Restart the server for full activation.",
                "available": has_win32print,
            })
        else:
            return JSONResponse(
                status_code=500,
                content={"error": result.stderr or "pip install failed."},
            )
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/print")
async def print_timesheets():
    """Send all PDFs in temp_processing/outputs to the default Windows printer."""
    if not _check_win32print():
        return JSONResponse(
            status_code=400,
            content={"error": "win32print is not installed. Use the 'Enable Windows Printing' button to install it."},
        )
    try:
        import win32print
        import win32api

        output_dir = os.path.abspath(os.path.join("temp_processing", "outputs"))
        pdfs = [os.path.join(output_dir, f) for f in os.listdir(output_dir) if f.endswith(".pdf")]

        if not pdfs:
            return JSONResponse(status_code=400, content={"error": "No PDFs found to print."})

        printer_name = win32print.GetDefaultPrinter()
        for pdf_path in pdfs:
            win32api.ShellExecute(0, "print", pdf_path, f'/d:"{printer_name}"', ".", 0)

        return JSONResponse(content={
            "success": True,
            "message": f"{len(pdfs)} PDF(s) sent to printer: {printer_name}",
        })
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


# ---------------------------------------------------------------------------
# Serve static files from React
# ---------------------------------------------------------------------------
frontend_dist = os.path.join(os.getcwd(), "frontend", "dist")

if os.path.isdir(os.path.join(frontend_dist, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="assets")


@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    if os.path.exists(frontend_dist):
        file_path = os.path.join(frontend_dist, full_path)
        if full_path and os.path.isfile(file_path):
            return FileResponse(file_path)

        index_path = os.path.join(frontend_dist, "index.html")
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())

    return JSONResponse(status_code=404, content={"error": "Frontend not found. Did you run npm run build?"})
