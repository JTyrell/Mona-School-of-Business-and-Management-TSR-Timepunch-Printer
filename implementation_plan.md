# Migrating MSBM Timepunch Printer to Heroku

To deploy our fullstack application to Heroku, we will transition it into a monolithic application. FastAPI will handle the API requests and statically serve the compiled React frontend.

## Goal Description
Prepare the codebase to be built and executed on a Heroku Dyno, merging the dual-folder setup into a deployable monolith and handling Linux/Playwright compatibilities.

## User Review Required

> [!WARNING]  
> **Heroku's Ephemeral File System:**
> Heroku Dynos do not have persistent local storage. While we *can* still save uploaded timesheets into the `Excel Timesheets` folder on the dyno, **Heroku restarts all dynos automatically at least once a day**, and any deployments wipe the container. This means the `Excel Timesheets` folder will be periodically erased. If you require permanent logging, you must integrate an external cloud bucket (like AWS S3).
> *Do you accept that local files in `Excel Timesheets` will be periodically wiped?*

> [!CAUTION]
> **Playwright & Linux Dependencies:**
> Heroku runs on Linux containers and lacks the graphical drivers required for Chrome out of the box. You will be required to add a specific Heroku Buildpack (e.g. `heroku-buildpack-playwright` or `heroku/google-chrome`) via the Heroku Dashboard to ensure Playwright can download and execute its hidden browsers successfully.

## Proposed Changes

### Heroku Configuration
#### [NEW] Procfile
- Instruct Heroku to launch the FastAPI server using Uvicorn: `web: uvicorn backend.main:app --host=0.0.0.0 --port=${PORT:-5000}`.

### Backend (Python/FastAPI)
#### [MODIFY] backend/requirements.txt
- Remove the `pywin32` dependency. This library hooks into Windows native APIs for local printers and will cause the Heroku Linux container build to crash instantly. The frontend will gracefully fall back to `window.print()`.

#### [MODIFY] backend/main.py
- Refactor the FastApi app to **serve static files** from the `frontend/dist` directory. 
- Automatically proxy all non-API routes `/` to the React `index.html` allowing FastAPI to act as both the API and the web-server.
- Change the `POST` endpoints to support Heroku's dynamic `PORT` configurations.

### Frontend (React/Vite)
#### [MODIFY] frontend/src/App.jsx
- Change the API calls from explicitly targeting `http://localhost:8000/api/...` to use relative paths (`/api/...`). This ensures the app can reach the API regardless of what randomly generated Heroku domain URL is assigned to it.

#### [NEW] package.json (Local Root)
- Add a root `package.json` that Heroku will detect via the `heroku/nodejs` buildpack. It will run a `postinstall` script to `cd frontend && npm install && npm run build` which compiles the React interface so FastAPI can serve it into production.

## Open Questions
1. **File Storage:** Are you okay with the `Excel Timesheets` directory acting as a temporary cache that resets every 24 hours?

## Verification Plan
We will provide the exact Buildpack pipeline orders you need to enter into your Heroku deployment settings for this merged Monolith to build successfully.
