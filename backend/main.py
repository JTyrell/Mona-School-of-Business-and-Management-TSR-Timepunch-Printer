from fastapi import FastAPI, UploadFile, File, Form, Request, BackgroundTasks
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
from backend.database import init_db, SessionLocal, TimesheetLog, TimesheetProgress, is_db_ready, cleanup_old_data

# Initialize database tables on startup
try:
    init_db()
except Exception as e:
    print(f"Database initialization failed: {e}")

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# ---------------------------------------------------------------------------
# Cross-platform print capability detection
# ---------------------------------------------------------------------------
def _check_win32print():
    """True only on Windows when pywin32 is installed."""
    if sys.platform != "win32":
        return False
    try:
        import win32print  # noqa: F401
        return True
    except ImportError:
        return False


def _check_cups():
    """True on Linux/Mac when the CUPS 'lp' command is on PATH."""
    if sys.platform == "win32":
        return False
    return shutil.which("lp") is not None


def _get_print_capability():
    """
    Returns a dict describing the current printing capability:
      platform  : 'windows' | 'linux' | 'darwin' | 'other'
      method    : 'win32print' | 'cups' | 'none'
      available : bool
      printer   : name of default printer, or None
    """
    if sys.platform == "win32":
        win32_ok = _check_win32print()
        printer = None
        if win32_ok:
            try:
                import win32print
                printer = win32print.GetDefaultPrinter()
            except Exception:
                pass
        return {
            "platform": "windows",
            "method": "win32print" if win32_ok else "none",
            "available": win32_ok,
            "printer": printer,
        }
    elif sys.platform == "darwin":
        cups_ok = _check_cups()
        printer = None
        if cups_ok:
            try:
                r = subprocess.run(["lpstat", "-d"], capture_output=True, text=True, timeout=5)
                line = r.stdout.strip()
                if ":" in line:
                    printer = line.split(":", 1)[1].strip()
            except Exception:
                pass
        return {
            "platform": "darwin",
            "method": "cups" if cups_ok else "none",
            "available": cups_ok,
            "printer": printer,
        }
    else:
        # Linux (including Cloud/Serverless environments)
        cups_ok = _check_cups()
        printer = None
        if cups_ok:
            try:
                r = subprocess.run(["lpstat", "-d"], capture_output=True, text=True, timeout=5)
                line = r.stdout.strip()
                if ":" in line:
                    printer = line.split(":", 1)[1].strip()
            except Exception:
                pass
        return {
            "platform": "linux",
            "method": "cups" if cups_ok else "none",
            "available": cups_ok,
            "printer": printer,
        }

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://msbm-timepunchcard.vercel.app", 
        "http://localhost:5173" # For local development
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health_check():
    return {"status": "ok", "backend": "Fly.io + FastAPI + Playwright"}

# ---------------------------------------------------------------------------
# Shared state for progress reporting (fallback when DB is offline)
# ---------------------------------------------------------------------------
_progress_store = {}

def _send_progress(session_id, message, step=None, total=None, status="processing", db=None):
    """
    Write a progress event to the database for a given session.
    If the database is unreachable, it logs to an in-memory dictionary.
    """
    if not is_db_ready():
        print(f"[DB_OFFLINE - fallback to memory] {session_id}: {message}")
        entry = {"message": message, "status": status}
        if step is not None:
            entry["step"] = step
        if total is not None:
            entry["total"] = total
        _progress_store.setdefault(session_id, []).append(entry)
        return
    
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    
    try:
        new_event = TimesheetProgress(
            session_id=session_id,
            message=message,
            step=step,
            total=total,
            status=status
        )
        db.add(new_event)
        db.commit()
    except Exception as e:
        print(f"Error logging progress to DB: {e}")
    finally:
        if close_db:
            db.close()


# ---------------------------------------------------------------------------
# SSE Progress endpoint
# ---------------------------------------------------------------------------
@app.get("/api/progress/{session_id}")
async def progress_stream(session_id: str):
    """Server-Sent Events stream that fetches progress from the database (or memory if offline)."""
    async def memory_generator():
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

    async def db_generator():
        last_id = 0
        while True:
            db = SessionLocal()
            try:
                # Query only new events since last_id
                events = db.query(TimesheetProgress).filter(
                    TimesheetProgress.session_id == session_id,
                    TimesheetProgress.id > last_id
                ).order_by(TimesheetProgress.id.asc()).all()
                
                should_stop = False
                for event in events:
                    data = json.dumps({
                        "message": event.message,
                        "step": event.step,
                        "total": event.total,
                        "status": event.status
                    })
                    yield f"data: {data}\n\n"
                    last_id = event.id
                    if event.status in ("done", "error"):
                        should_stop = True
                
                if should_stop:
                    return
            except Exception as e:
                print(f"SSE error: {e}")
                return
            finally:
                db.close()
            await asyncio.sleep(0.5)

    generator = db_generator() if is_db_ready() else memory_generator()
    
    return StreamingResponse(
        generator,
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
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    initials: str = Form(...),
    hourly_rate: str = Form(...),
    headless: str = Form("true"),
    session_id: str = Form("default"),
    ignore_mismatch: str = Form("false"),
    is_mobile: str = Form("false"),
):
    # Trigger an asynchronous cleanup of data older than 24h
    if is_db_ready():
        background_tasks.add_task(cleanup_old_data, SessionLocal())

    session = SessionLocal() if is_db_ready() else None
    try:
        run_headless = headless.lower() == "true"
        ignore_mismatch_bool = ignore_mismatch.lower() == "true"
        is_mobile_bool = is_mobile.lower() == "true"
        rate = float(hourly_rate)

        # Log the start of the process
        log_entry = None
        if session:
            log_entry = TimesheetLog(
                status="started",
                file_name=file.filename,
                total_pdfs=0
            )
            session.add(log_entry)
            session.commit()
            session.refresh(log_entry)

        _send_progress(session_id, "Received upload - saving file...", step=0, db=session)

        # Save uploaded file into "Excel Timesheets" directory
        import tempfile
        base_tmp = tempfile.gettempdir()
        timesheets_dir = os.path.abspath(os.path.join(base_tmp, "Excel Timesheets"))
        os.makedirs(timesheets_dir, exist_ok=True)

        file_path = os.path.join(timesheets_dir, file.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        _send_progress(session_id, f"File saved: {file.filename}", db=session)

        # PDF Outputs go to a temporary dir
        temp_dir = os.path.abspath(os.path.join(base_tmp, "temp_processing"))
        output_dir = os.path.join(temp_dir, "outputs")
        os.makedirs(output_dir, exist_ok=True)

        # Clear previous output files
        for f in os.listdir(output_dir):
            os.remove(os.path.join(output_dir, f))

        _send_progress(session_id, "Parsing Excel workbook...", db=session)

        # Pre-parse to count total biweeks for progress reporting
        from backend.timesheet_bot import parse_excel as _parse, group_into_biweeks as _group, RateMismatchError
        try:
            sheets_map = _parse(file_path, rate, ignore_mismatch=ignore_mismatch_bool)
        except RateMismatchError as e:
            # Update log on rate mismatch
            if session and log_entry:
                log_entry.status = "error"
                log_entry.error_message = f"RateMismatchError: {str(e)}"
                session.commit()

            _send_progress(session_id, "ERROR: Hourly rate mismatch detected.", status="error", db=session)
            return JSONResponse(
                status_code=409, 
                content={"error": str(e), "mismatch": True}
            )

        total_pdfs = 0
        for entries in sheets_map.values():
            total_pdfs += len(_group(entries))

        if total_pdfs == 0:
            # Update log on no data
            if session and log_entry:
                log_entry.status = "no_data"
                log_entry.error_message = "No valid timesheet data found in the workbook."
                session.commit()

            _send_progress(session_id, "No valid timesheet data found in the file.", status="error", db=session)
            return JSONResponse(status_code=400, content={"error": "No valid timesheet data found in the workbook."})

        _send_progress(
            session_id,
            f"Found {len(sheets_map)} sheet(s) with {total_pdfs} bi-weekly period(s) to process.",
            step=0,
            total=total_pdfs,
            db=session
        )

        # Run the sync bot in a background thread
        _send_progress(session_id, "Launching Playwright browser (Sync API, background thread)...", db=session)

        def _run_bot():
            return process_timesheets(
                file_path, initials, rate, output_dir, run_headless,
                progress_callback=lambda msg, step=None, total=None: _send_progress(session_id, msg, step=step, total=total),
                ignore_mismatch=ignore_mismatch_bool,
                is_mobile=is_mobile_bool
            )

        pdf_files, tsr_name = await asyncio.to_thread(_run_bot)

        if not pdf_files:
            _send_progress(session_id, "No PDFs were generated. Check server logs.", status="error", db=session)
            return JSONResponse(status_code=400, content={"error": "No PDFs were generated."})

        _send_progress(session_id, f"All {len(pdf_files)} PDF(s) generated successfully. Creating ZIP archive...", db=session)

        # Zip them up: [Name] Timecards.zip
        safe_name = "".join(c for c in tsr_name if c.isalnum() or c in (' ', '_')).strip()
        zip_filename = f"{safe_name} Timecards.zip"
        zip_path = os.path.join(temp_dir, "timesheets.zip")
        with zipfile.ZipFile(zip_path, "w") as zipf:
            for pdf in pdf_files:
                if os.path.isfile(pdf):
                    zipf.write(pdf, os.path.basename(pdf))
                else:
                    _send_progress(session_id, f"WARNING: Expected PDF not found on disk: {os.path.basename(pdf)}", db=session)

        zip_size = os.path.getsize(zip_path)
        _send_progress(
            session_id,
            f"ZIP archive ready ({zip_size // 1024} KB, {len(pdf_files)} file(s)). Sending download...",
            status="done",
            db=session
        )

        # Update log on success
        if session and log_entry:
            log_entry.status = "success"
            log_entry.total_pdfs = len(pdf_files)
            session.commit()

        async def cleanup_files():
            import asyncio
            await asyncio.sleep(15)  # 15s buffer to ensure ZIP download finishes
            try:
                if os.path.exists(file_path): os.remove(file_path)
                if os.path.exists(zip_path): os.remove(zip_path)
            except Exception:
                pass

        async def cleanup_pdfs_delayed():
            # Automatically wipe generated PDFs after 30 minutes
            import asyncio
            import time
            await asyncio.sleep(1800)
            try:
                now = time.time()
                if os.path.exists(output_dir):
                    for f in os.listdir(output_dir):
                        fpath = os.path.join(output_dir, f)
                        if os.stat(fpath).st_mtime < now - 1500:
                            os.remove(fpath)
            except Exception:
                pass

        background_tasks.add_task(cleanup_files)
        background_tasks.add_task(cleanup_pdfs_delayed)

        return FileResponse(path=zip_path, filename=zip_filename, media_type="application/zip")

    except Exception as e:
        traceback.print_exc()
        # Update log on failure
        if session and log_entry:
            log_entry.status = "error"
            log_entry.error_message = str(e)
            session.commit()

        _send_progress(session_id, f"ERROR: {str(e)}", status="error", db=session)
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        if session:
            session.close()


# ---------------------------------------------------------------------------
# Manual Cleanup endpoint
# ---------------------------------------------------------------------------
@app.post("/api/cleanup")
async def force_cleanup():
    """Immediately wipe temp directories. Triggered when frontend restarts."""
    import tempfile
    import shutil
    base_tmp = tempfile.gettempdir()
    excel_dir = os.path.abspath(os.path.join(base_tmp, "Excel Timesheets"))
    temp_dir = os.path.abspath(os.path.join(base_tmp, "temp_processing", "outputs"))
    zip_path = os.path.abspath(os.path.join(base_tmp, "temp_processing", "timesheets.zip"))
    
    try:
        if os.path.exists(excel_dir): shutil.rmtree(excel_dir, ignore_errors=True)
        if os.path.exists(temp_dir): shutil.rmtree(temp_dir, ignore_errors=True)
        if os.path.exists(zip_path): os.remove(zip_path)
        os.makedirs(excel_dir, exist_ok=True)
        os.makedirs(temp_dir, exist_ok=True)
    except Exception:
        pass
    
    return JSONResponse(content={"success": True})


# ---------------------------------------------------------------------------
# Print status, install, and execute endpoints (cross-platform)
# ---------------------------------------------------------------------------
@app.get("/api/win32print-status")
async def win32print_status():
    """
    Returns full print capability info for the current platform.
    Kept at this URL for backwards-compatibility with the frontend.
    """
    cap = _get_print_capability()
    return JSONResponse(content=cap)


@app.post("/api/install-win32print")
async def install_win32print():
    """
    Windows: installs pywin32 via pip.
    Linux/Mac: CUPS is a system package — explains how to enable it.
    """
    if sys.platform == "win32":
        if _check_win32print():
            return JSONResponse(content={
                "success": True,
                "message": "pywin32 is already installed.",
                "available": True,
            })
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "pip", "install", "pywin32"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                cap = _get_print_capability()
                return JSONResponse(content={
                    "success": True,
                    "message": "pywin32 installed. Restart the server to activate direct printing.",
                    "available": cap["available"],
                })
            else:
                return JSONResponse(
                    status_code=500,
                    content={"error": result.stderr or "pip install pywin32 failed."},
                )
        except Exception as e:
            traceback.print_exc()
            return JSONResponse(status_code=500, content={"error": str(e)})
    else:
        # Linux / Mac — CUPS is a system package, not a pip package
        cups_ok = _check_cups()
        if cups_ok:
            return JSONResponse(content={
                "success": True,
                "message": "CUPS (lp) is already available on this system. Direct printing is enabled.",
                "available": True,
            })
        else:
            return JSONResponse(
                status_code=400,
                content={
                    "error": (
                        "CUPS is not installed in the Docker container. "
                        "Ensure 'cups-client' is added to the apt-get install step in the Dockerfile."
                    ),
                    "available": False,
                },
            )


@app.post("/api/print")
async def print_timesheets():
    """Send all PDFs in temp_processing/outputs to the system default printer."""
    cap = _get_print_capability()

    if not cap["available"]:
        platform_hint = (
            "Install pywin32 using the 'Enable Printing' button."
            if sys.platform == "win32"
            else "CUPS (lp) is not available on this server. Install it or download the ZIP."
        )
        return JSONResponse(
            status_code=400,
            content={"error": f"Direct printing is not available. {platform_hint}"},
        )

    import tempfile
    base_tmp = tempfile.gettempdir()
    output_dir = os.path.abspath(os.path.join(base_tmp, "temp_processing", "outputs"))
    pdfs = sorted(
        os.path.join(output_dir, f)
        for f in os.listdir(output_dir)
        if f.endswith(".pdf")
    )

    if not pdfs:
        return JSONResponse(status_code=400, content={"error": "No PDFs found to print."})

    try:
        if sys.platform == "win32" and cap["method"] == "win32print":
            # ── Windows path ──────────────────────────────────────────
            import win32print
            import win32api
            printer_name = win32print.GetDefaultPrinter()
            for pdf_path in pdfs:
                win32api.ShellExecute(0, "print", pdf_path, f'/d:"{printer_name}"', ".", 0)
            return JSONResponse(content={
                "success": True,
                "message": f"{len(pdfs)} PDF(s) sent to Windows printer: {printer_name}",
            })
        else:
            # ── Linux / Mac CUPS path ─────────────────────────────────
            printer_name = cap.get("printer") or None
            sent = 0
            errors = []
            for pdf_path in pdfs:
                cmd = ["lp"]
                if printer_name:
                    cmd += ["-d", printer_name]
                cmd.append(pdf_path)
                result = await asyncio.to_thread(
                    subprocess.run, cmd, capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    sent += 1
                else:
                    errors.append(f"{os.path.basename(pdf_path)}: {result.stderr.strip()}")

            if errors:
                return JSONResponse(
                    status_code=207,
                    content={
                        "success": sent > 0,
                        "message": f"{sent}/{len(pdfs)} PDF(s) sent to CUPS printer: {printer_name or 'default'}.",
                        "errors": errors,
                    },
                )
            return JSONResponse(content={
                "success": True,
                "message": f"{sent} PDF(s) sent to CUPS printer: {printer_name or 'default'}.",
            })
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


