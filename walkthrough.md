# MSBM TSR Timepunch Printer Fullstack App

We have completely built the responsive React Web Application and Python REST API framework for your timepunch generator!

## Architecture Summary
As requested, we implemented a **dual-folder setup**:
1. `frontend/` - A responsive, dynamically animated Vite/React frontend adhering to the stunning #ac1928, #2341a4, #697ec1, and #ffffff MSBM color scheme.
2. `backend/` - A FastAP Python application serving as the powerful API that seamlessly ingests requests and pushes the calculations through the original, reliable automation script.

> [!NOTE]
> Per your instructions, I made an exact logical copy of the original `timesheet_bot.py` functions, moving it to `backend/timesheet_bot.py`, and simply replaced the bottom input-gathering section so it could be programmatically driven by the API (using the new `process_timesheets()` function). 

## Verification & Execution
To use your new application, you will need to open two separate terminals. Ensure you run these from the main folder containing both projects.

### 1. Launch the Backend Server
```powershell
uvicorn backend.main:app --reload --host localhost --port 8000
```
*This powers the API and houses the logic to parse files directly into the native "Excel Timesheets" directory you requested.*

### 2. Launch the Frontend UI
Open a second PowerShell terminal, navigate to the frontend directory, and start the development server:
```powershell
cd frontend
npm run dev
```
Wait a second, and then open the provided URL (e.g. `http://localhost:5173`) in your web browser.

## Features Developed
- **Hidden PDF Generation:** The standard toggle runs Playwright headless in the background while gracefully hiding the window processing behind a spinner.
- **Embedded Interleaving Mode:** Unchecking the "Run Hidden" option allows the user to witness the Playwright bot's progress in a pop-up Chrome browser.
- **Save to PDF or Printer Default:** Clicking 'Print All' safely pings the backend. If `win32print` evaluates true, it passes it to the OS. Otherwise, it gracefully allows you to default to typical behaviors or manually download the ZIP containing the PDF sheets.
- **File Preservation:** Any files fed into the front-end file-picker are securely backed up back into the `Excel Timesheets` folder on your root machine layout.
