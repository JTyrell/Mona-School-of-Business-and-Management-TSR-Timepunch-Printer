import asyncio
import os
import time
from datetime import datetime, timedelta

import pandas as pd
from playwright.sync_api import sync_playwright
from playwright.async_api import async_playwright

# ----------------------------------------------------------------------
# Helper functions (shared by both sync and async paths)
# ----------------------------------------------------------------------

def format_time_12h(t):
    return t.strftime("%I:%M").lstrip("0")

def get_week_monday(date):
    return date - timedelta(days=date.weekday())

def parse_excel(file_path, hourly_rate=516, ignore_mismatch=False):
    sheets_entries = {}
    tsr_name = "TSR"
    xl = pd.ExcelFile(file_path)

    for sheet_name in xl.sheet_names:
        entries = []
        df = pd.read_excel(xl, sheet_name=sheet_name, header=None)

        header_row = -1
        # Extract TSR Name from A1 if it looks like a name (not just blank)
        potential_name = str(df.iloc[0, 0]).strip() if not df.empty else ""
        if potential_name and len(potential_name) > 2 and "DATE" not in potential_name.upper():
            tsr_name = potential_name

        for i, row in df.iterrows():
            if row.astype(str).str.contains("DATE", case=False, na=False).any():
                header_row = int(i)
                break
        if header_row == -1:
            continue

        header = df.iloc[header_row]
        date_col = None
        total_col = None
        hours_col = None
        start_col = None
        end_col = None
        for idx, val in enumerate(header):
            val_str = str(val).upper()
            if 'DATE' in val_str:
                date_col = idx
            if 'TOTAL' in val_str and 'LINE' in val_str:
                total_col = idx
            if 'HOUR' in val_str:
                hours_col = idx
            if 'START' in val_str:
                start_col = idx
            if 'END' in val_str:
                end_col = idx

        if date_col is None or total_col is None:
            continue

        # Collect valid rows first to smartly determine date format (US vs UK)
        raw_rows = []
        for i in range(header_row + 1, len(df)):
            row = df.iloc[i]
            date_val = row[date_col]
            if 'TOTAL' in str(date_val).upper():
                continue
            total_val = row[total_col]
            
            hours = 0
            try:
                if hours_col is not None:
                    # pandas might read formulas as NaN or strings
                    val = row[hours_col]
                    if pd.isna(val) or (isinstance(val, str) and val.startswith('=')):
                        raise ValueError("Unevaluated formula or NaN")
                    hours = float(val)
                else:
                    val = row[total_col]
                    if pd.isna(val) or (isinstance(val, str) and val.startswith('=')):
                        raise ValueError("Unevaluated formula or NaN")
                    hours = float(val) / hourly_rate
            except Exception:
                # Fallback: if hours couldn't be extracted (e.g. pushed from Autofill where formulas aren't evaluated)
                if start_col is not None and end_col is not None:
                    from dateutil.parser import parse as dt_parse
                    import datetime as dt
                    start_val = row[start_col]
                    end_val = row[end_col]
                    if pd.notna(start_val) and pd.notna(end_val):
                        try:
                            # if they are already time objects
                            if isinstance(start_val, dt.time):
                                s_time = start_val
                            else:
                                s_time = dt_parse(str(start_val)).time()
                                
                            if isinstance(end_val, dt.time):
                                e_time = end_val
                            else:
                                e_time = dt_parse(str(end_val)).time()
                                
                            s_dt = dt.datetime.combine(dt.date.today(), s_time)
                            e_dt = dt.datetime.combine(dt.date.today(), e_time)
                            if e_dt < s_dt:
                                e_dt += dt.timedelta(days=1)
                            hours = (e_dt - s_dt).total_seconds() / 3600
                        except Exception:
                            pass

            hours = round(hours, 2)
                
            if hours > 0:
                raw_rows.append({'row': row, 'date_val': date_val, 'hours': hours})

        if not raw_rows:
            continue

        # Smart format detection: test both True and False for dayfirst
        parsed_true = []
        parsed_false = []
        for r in raw_rows:
            try:
                parsed_true.append(pd.to_datetime(r['date_val'], dayfirst=True).date())
            except Exception:
                pass
            try:
                parsed_false.append(pd.to_datetime(r['date_val'], dayfirst=False).date())
            except Exception:
                pass
                
        span_true = (max(parsed_true) - min(parsed_true)).days if parsed_true else 9999
        span_false = (max(parsed_false) - min(parsed_false)).days if parsed_false else 9999
        
        # Timesheets are typically tight, contiguous blocks (e.g. 1-4 weeks).
        # A wrong format interpretation usually scatters days across multiple months (huge span).
        # We pick the format that produces the tightest date span. Ties go to the UK default (True).
        use_dayfirst = span_true <= span_false

        for r in raw_rows:
            try:
                date = pd.to_datetime(r['date_val'], dayfirst=use_dayfirst)
                entries.append({
                    'date': date.date(),
                    'hours': r['hours'],
                    'weekday': date.weekday()
                })
            except Exception:
                continue

        if entries:
            entries.sort(key=lambda x: x['date'])
            sheets_entries[sheet_name] = entries

    return sheets_entries, tsr_name


def group_into_biweeks(entries):
    if not entries:
        return []

    min_date = min(e['date'] for e in entries)
    max_date = max(e['date'] for e in entries)
    
    # Use the exact first date from the excel file, do not force shift to Monday
    current_start = min_date

    periods = []
    while current_start <= max_date:
        period_end = current_start + timedelta(days=13)
        periods.append((current_start, period_end))
        current_start += timedelta(days=14)

    biweeks = []
    for start, end in periods:
        week1_start = start
        week1_end = start + timedelta(days=6)
        week2_start = start + timedelta(days=7)
        week2_end = end
        week1_entries = [e for e in entries if week1_start <= e['date'] <= week1_end]
        week2_entries = [e for e in entries if week2_start <= e['date'] <= week2_end]
        if week1_entries or week2_entries:
            biweeks.append((week1_start, week2_start, week1_entries, week2_entries))
    return biweeks


def calculate_times_from_hours(hours):
    start_time = datetime.strptime("8:00", "%H:%M")
    if hours <= 4:
        end_time = start_time + timedelta(hours=hours)
        return (start_time.time(), end_time.time(), None, None)
    else:
        lunch_start = start_time + timedelta(hours=4)
        lunch_end = lunch_start + timedelta(hours=1)
        work_end = lunch_end + timedelta(hours=hours - 4)
        return (start_time.time(), lunch_start.time(), lunch_end.time(), work_end.time())


# ----------------------------------------------------------------------
# Sync helpers
# ----------------------------------------------------------------------

def fill_time(col_locator, time_obj):
    hour_str = time_obj.strftime("%I")
    min_str = time_obj.strftime("%M")
    is_pm = time_obj.hour >= 12
    col_locator.locator("input.hour").fill(hour_str)
    col_locator.locator("input.minute").fill(min_str)
    if is_pm:
        col_locator.locator("label:has-text('PM')").click()
    else:
        col_locator.locator("label:has-text('AM')").click()


def fill_day(page, day_index, entry):
    if not entry or entry['hours'] <= 0:
        return
    in1, out1, in2, out2 = calculate_times_from_hours(entry['hours'])
    day_row = page.locator(".weekly-lunch-col.inputLine").nth(day_index)
    fill_time(day_row.locator(".wco-weekly-col-2"), in1)
    fill_time(day_row.locator(".wco-weekly-col-3"), out1)
    if in2 and out2:
        fill_time(day_row.locator(".wco-weekly-col-4"), in2)
        fill_time(day_row.locator(".wco-weekly-col-5"), out2)


def set_date_field(page, month_id, day_id, year_id, target_date):
    month = target_date.strftime("%m")
    day = target_date.strftime("%d")
    year = target_date.strftime("%Y")
    page.locator(month_id).evaluate(f"el => el.value = '{month}'")
    page.locator(day_id).evaluate(f"el => el.value = '{day}'")
    page.locator(year_id).evaluate(f"el => el.value = '{year}'")


# ----------------------------------------------------------------------
# Async helpers
# ----------------------------------------------------------------------

async def async_fill_time(col_locator, time_obj):
    hour_str = time_obj.strftime("%I")
    min_str = time_obj.strftime("%M")
    is_pm = time_obj.hour >= 12
    await col_locator.locator("input.hour").fill(hour_str)
    await col_locator.locator("input.minute").fill(min_str)
    if is_pm:
        await col_locator.locator("label:has-text('PM')").click()
    else:
        await col_locator.locator("label:has-text('AM')").click()


async def async_fill_day(page, day_index, entry):
    if not entry or entry['hours'] <= 0:
        return
    in1, out1, in2, out2 = calculate_times_from_hours(entry['hours'])
    day_row = page.locator(".weekly-lunch-col.inputLine").nth(day_index)
    await async_fill_time(day_row.locator(".wco-weekly-col-2"), in1)
    await async_fill_time(day_row.locator(".wco-weekly-col-3"), out1)
    if in2 and out2:
        await async_fill_time(day_row.locator(".wco-weekly-col-4"), in2)
        await async_fill_time(day_row.locator(".wco-weekly-col-5"), out2)


async def async_set_date_field(page, month_id, day_id, year_id, target_date):
    month = target_date.strftime("%m")
    day = target_date.strftime("%d")
    year = target_date.strftime("%Y")
    await page.locator(month_id).evaluate(f"el => el.value = '{month}'")
    await page.locator(day_id).evaluate(f"el => el.value = '{day}'")
    await page.locator(year_id).evaluate(f"el => el.value = '{year}'")


# ----------------------------------------------------------------------
# Shared build helper
# ----------------------------------------------------------------------

def _build_week_maps(biweek):
    week1_start, week2_start, week1_entries, week2_entries = biweek

    def entries_by_day(entries, week_start):
        day_map = {
            (week_start + timedelta(days=i)).strftime("%a"): {'hours': 0}
            for i in range(7)
        }
        for entry in entries:
            day_name = entry['date'].strftime("%a")
            day_map[day_name]['hours'] = round(day_map[day_name]['hours'] + entry['hours'], 2)
        return day_map

    return entries_by_day(week1_entries, week1_start), entries_by_day(week2_entries, week2_start)


# ----------------------------------------------------------------------
# Default no-op callback
# ----------------------------------------------------------------------

def _noop_progress(msg, step=None, total=None):
    """Fallback: just print to console."""
    print(f"[Bot] {msg}")


# ======================================================================
# SYNC implementation
# ======================================================================

def _create_timesheet_pdf_sync(biweek, initials, hourly_rate, output_file, run_headless=True, progress=None, is_mobile=False):
    """Generate a single PDF using Playwright Sync API."""
    if progress is None:
        progress = _noop_progress

    week1_start, week2_start, _, _ = biweek
    week1_map, week2_map = _build_week_maps(biweek)

    progress(f"  Opening Playwright browser (headless={run_headless})...")

    with sync_playwright() as p:
        browser = None
        browser_name = None

        if not run_headless:
            progress("  NOTICE: System forced 'Headed' mode, but Playwright only supports PDF generation in Headless mode.")
            progress("  Temporarily switching back to Headless to ensure the timesheet saves.")
            run_headless = True

        import os
        local_app_data = os.environ.get('LOCALAPPDATA', '')
        prog_files = os.environ.get('PROGRAMFILES', 'C:\\Program Files')
        
        fallback_scans = [
            {"name": "chrome", "channel": "chrome"},
            {"name": "chromium", "channel": None},
            {"name": "opera", "executable_path": os.path.join(local_app_data, "Programs", "Opera", "launcher.exe")},
            {"name": "brave", "executable_path": os.path.join(prog_files, "BraveSoftware", "Brave-Browser", "Application", "brave.exe")},
            {"name": "msedge", "channel": "msedge"}
        ]
        
        for scan in fallback_scans:
            try:
                kwargs = {"headless": run_headless}
                if "channel" in scan and scan["channel"]:
                    kwargs["channel"] = scan["channel"]
                elif "executable_path" in scan:
                    if not os.path.exists(scan["executable_path"]):
                        continue
                    kwargs["executable_path"] = scan["executable_path"]
                    
                browser = p.chromium.launch(**kwargs)
                browser_name = scan["name"]
                progress(f"  Local browser launched: {browser_name}")
                break
            except Exception as e:
                progress(f"  Could not launch {scan['name']}: {e}")

        if not browser:
            progress("  ERROR: No Chromium-compatible browser could be launched locally!")
            return False

        context_kwargs = {
            "viewport": {"width": 1400, "height": 1200},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

        if is_mobile:
            device = p.devices['Pixel 5']
            context_kwargs.update(device)
            progress("  Emulating mobile device (Pixel 5).")

        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        page.set_default_timeout(60000)
        page.set_default_navigation_timeout(60000)

        progress("  Navigating to CalculateHours.com...")
        nav_success = False
        for attempt in range(3):
            try:
                page.goto(
                    "https://www.calculatehours.com/m/Time-Card-Calculator-Biweekly+Lunch.html",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                page.wait_for_load_state("networkidle", timeout=60000)
                page.wait_for_timeout(3000)
                nav_success = True
                progress("  Page loaded successfully.")
                break
            except Exception as e:
                progress(f"  Navigation attempt {attempt + 1}/3 failed: {e}")
                time.sleep(3)

        if not nav_success:
            progress("  ERROR: Could not load the calculator page after 3 attempts.")
            browser.close()
            return False

        # Dismiss consent overlays
        try:
            consent_btn = page.locator("button.fc-cta-consent, button:has-text('Consent')").first
            if consent_btn.is_visible(timeout=2000):
                consent_btn.click(force=True)
                page.wait_for_timeout(1000)
                progress("  Dismissed cookie consent overlay.")
            page.evaluate("""
                const overlays = document.querySelectorAll('.fc-consent-root, .fc-dialog-overlay, .fc-dialog-container');
                overlays.forEach(el => el.remove());
                document.body.style.overflow = 'auto';
            """)
        except Exception:
            pass

        # Set dates
        try:
            set_date_field(page, "#month", "#day", "#year", week1_start)
            set_date_field(page, "#month2", "#day2", "#year2", week2_start)
            progress(f"  Set dates: Week 1 = {week1_start}, Week 2 = {week2_start}")
        except Exception as e:
            progress(f"  WARNING: Could not set date fields: {e}")

        # Set initials
        try:
            initials_input = page.locator("#name").first
            if initials_input.count():
                initials_input.fill(initials)
                progress(f"  Set employee initials: {initials}")
        except Exception as e:
            progress(f"  WARNING: Could not set initials: {e}")

        # Set rate
        try:
            rate_input = page.locator("input[placeholder*='Rate'], input[name='rate']").first
            if rate_input.count():
                rate_input.fill(str(hourly_rate))
                progress(f"  Set hourly rate: {hourly_rate}")
        except Exception as e:
            progress(f"  WARNING: Could not set hourly rate: {e}")

        # Fill time entries
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        w1_filled = 0
        for idx, day in enumerate(day_names):
            entry = week1_map.get(day)
            if entry and entry['hours'] > 0:
                try:
                    fill_day(page, idx, entry)
                    w1_filled += 1
                except Exception as e:
                    progress(f"  WARNING: Week 1 {day} fill failed: {e}")

        w2_filled = 0
        for idx, day in enumerate(day_names):
            entry = week2_map.get(day)
            if entry and entry['hours'] > 0:
                try:
                    fill_day(page, idx + 7, entry)
                    w2_filled += 1
                except Exception as e:
                    progress(f"  WARNING: Week 2 {day} fill failed: {e}")

        progress(f"  Filled {w1_filled} day(s) in Week 1, {w2_filled} day(s) in Week 2.")

        # Click Calculate
        try:
            calc_button = page.locator("input[value='CALCULATE'], button:has-text('CALCULATE')")
            if calc_button.count():
                calc_button.click()
                page.wait_for_timeout(1000)
                progress("  Clicked CALCULATE button.")
        except Exception as e:
            progress(f"  WARNING: Could not click CALCULATE: {e}")

        # Generate PDF
        progress("  Generating PDF output...")
        pdf_generated = False
        try:
            print_button = page.locator("#printpage")
            if print_button.count():
                with context.expect_page() as popup_info:
                    print_button.click()
                popup = popup_info.value
                popup.wait_for_load_state(timeout=60000)
                page.wait_for_timeout(3000)
                popup.pdf(path=output_file, format="A4", print_background=True)
                popup.close()
                pdf_generated = True
            else:
                page.pdf(path=output_file, format="A4", print_background=True)
                pdf_generated = True
        except Exception as e:
            progress(f"  ERROR generating PDF: {e}")

        browser.close()

        if pdf_generated and os.path.isfile(output_file):
            size_kb = os.path.getsize(output_file) // 1024
            progress(f"  PDF saved: {os.path.basename(output_file)} ({size_kb} KB)")
            return True
        else:
            progress(f"  ERROR: PDF file was not created at {output_file}")
            return False


# ======================================================================
# ASYNC implementation
# ======================================================================

async def _create_timesheet_pdf_async(biweek, initials, hourly_rate, output_file, run_headless=True, progress=None, is_mobile=False):
    """Generate a single PDF using Playwright Async API."""
    if progress is None:
        progress = _noop_progress

    week1_start, week2_start, _, _ = biweek
    week1_map, week2_map = _build_week_maps(biweek)

    progress(f"  Opening Playwright browser (headless={run_headless})...")

    async with async_playwright() as p:
        browser = None
        
        if not run_headless:
            progress("  NOTICE: System forced 'Headed' mode, but Playwright only supports PDF generation in Headless mode.")
            progress("  Temporarily switching back to Headless to ensure the timesheet saves.")
            run_headless = True

        import os
        local_app_data = os.environ.get('LOCALAPPDATA', '')
        prog_files = os.environ.get('PROGRAMFILES', 'C:\\Program Files')
        
        fallback_scans = [
            {"name": "chrome", "channel": "chrome"},
            {"name": "chromium", "channel": None},
            {"name": "opera", "executable_path": os.path.join(local_app_data, "Programs", "Opera", "launcher.exe")},
            {"name": "brave", "executable_path": os.path.join(prog_files, "BraveSoftware", "Brave-Browser", "Application", "brave.exe")},
            {"name": "msedge", "channel": "msedge"}
        ]
        
        for scan in fallback_scans:
            try:
                kwargs = {"headless": run_headless}
                if "channel" in scan and scan["channel"]:
                    kwargs["channel"] = scan["channel"]
                elif "executable_path" in scan:
                    if not os.path.exists(scan["executable_path"]):
                        continue
                    kwargs["executable_path"] = scan["executable_path"]
                    
                browser = await p.chromium.launch(**kwargs)
                browser_name = scan["name"]
                progress(f"  Local browser launched: {browser_name}")
                break
            except Exception as e:
                progress(f"  Could not launch {scan['name']}: {e}")

        if not browser:
            progress("  ERROR: No Chromium-compatible browser could be launched locally!")
            return False

        context_kwargs = {
            "viewport": {"width": 1400, "height": 1200},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

        if is_mobile:
            device = p.devices['Pixel 5']
            context_kwargs.update(device)

        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()
        page.set_default_timeout(60000)
        page.set_default_navigation_timeout(60000)

        progress("  Navigating to CalculateHours.com...")
        nav_success = False
        for attempt in range(3):
            try:
                await page.goto(
                    "https://www.calculatehours.com/m/Time-Card-Calculator-Biweekly+Lunch.html",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                await page.wait_for_load_state("networkidle", timeout=60000)
                await page.wait_for_timeout(3000)
                nav_success = True
                progress("  Page loaded successfully.")
                break
            except Exception as e:
                progress(f"  Navigation attempt {attempt + 1}/3 failed: {e}")
                await asyncio.sleep(3)

        if not nav_success:
            progress("  ERROR: Could not load the calculator page after 3 attempts.")
            await browser.close()
            return False

        try:
            consent_btn = page.locator("button.fc-cta-consent, button:has-text('Consent')").first
            if await consent_btn.is_visible(timeout=2000):
                await consent_btn.click(force=True)
                await page.wait_for_timeout(1000)
                progress("  Dismissed cookie consent overlay.")
            await page.evaluate("""
                const overlays = document.querySelectorAll('.fc-consent-root, .fc-dialog-overlay, .fc-dialog-container');
                overlays.forEach(el => el.remove());
                document.body.style.overflow = 'auto';
            """)
        except Exception:
            pass

        try:
            await async_set_date_field(page, "#month", "#day", "#year", week1_start)
            await async_set_date_field(page, "#month2", "#day2", "#year2", week2_start)
            progress(f"  Set dates: Week 1 = {week1_start}, Week 2 = {week2_start}")
        except Exception as e:
            progress(f"  WARNING: Could not set date fields: {e}")

        try:
            initials_input = page.locator("#name").first
            if await initials_input.count():
                await initials_input.fill(initials)
        except Exception:
            pass

        try:
            rate_input = page.locator("input[placeholder*='Rate'], input[name='rate']").first
            if await rate_input.count():
                await rate_input.fill(str(hourly_rate))
        except Exception:
            pass

        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        for idx, day in enumerate(day_names):
            entry = week1_map.get(day)
            if entry and entry['hours'] > 0:
                await async_fill_day(page, idx, entry)

        for idx, day in enumerate(day_names):
            entry = week2_map.get(day)
            if entry and entry['hours'] > 0:
                await async_fill_day(page, idx + 7, entry)

        try:
            calc_button = page.locator("input[value='CALCULATE'], button:has-text('CALCULATE')")
            if await calc_button.count():
                await calc_button.click()
                await page.wait_for_timeout(1000)
        except Exception:
            pass

        progress("  Generating PDF output...")
        pdf_generated = False
        try:
            print_button = page.locator("#printpage")
            if await print_button.count():
                async with context.expect_page() as popup_info:
                    await print_button.click()
                popup = await popup_info.value
                await popup.wait_for_load_state(timeout=60000)
                await page.wait_for_timeout(3000)
                await popup.pdf(path=output_file, format="A4", print_background=True)
                await popup.close()
                pdf_generated = True
            else:
                await page.pdf(path=output_file, format="A4", print_background=True)
                pdf_generated = True
        except Exception as e:
            progress(f"  ERROR generating PDF: {e}")

        await browser.close()

        if pdf_generated and os.path.isfile(output_file):
            size_kb = os.path.getsize(output_file) // 1024
            progress(f"  PDF saved: {os.path.basename(output_file)} ({size_kb} KB)")
            return True
        else:
            progress(f"  ERROR: PDF file was not created at {output_file}")
            return False


# ======================================================================
# Public entry points
# ======================================================================

def process_timesheets(
    file_path,
    initials,
    hourly_rate,
    output_dir,
    run_headless=True,
    progress_callback=None,
    ignore_mismatch=False,
    is_mobile=False
):
    """
    Sync entry point (used via asyncio.to_thread from FastAPI, or directly from CLI).
    Accepts an optional progress_callback(msg, step, total) for live reporting.
    """
    cb = progress_callback or _noop_progress
    pdf_files = []
    tsr_name = "TSR"

    cb("Parsing Excel workbook...")
    sheets_entries_map, tsr_name = parse_excel(file_path, hourly_rate, ignore_mismatch=ignore_mismatch)
    if not sheets_entries_map:
        cb("No valid timesheet data found in the workbook.", status="error")
        return pdf_files, tsr_name

    # Count total PDFs
    all_biweeks = []
    for sheet_name, entries in sheets_entries_map.items():
        biweeks = group_into_biweeks(entries)
        for bw in biweeks:
            all_biweeks.append((sheet_name, bw))

    total = len(all_biweeks)
    cb(f"Found {len(sheets_entries_map)} sheet(s), {total} bi-weekly period(s) to process.", step=0, total=total)

    current_step = 0
    for sheet_name, biweek in all_biweeks:
        current_step += 1
        week1_start, week2_start, w1e, w2e = biweek
        w1_hrs = sum(e['hours'] for e in w1e)
        w2_hrs = sum(e['hours'] for e in w2e)

        safe_sheet = "".join(c for c in sheet_name if c.isalnum() or c in (' ', '_')).strip()

        # Build filename: CalculateHours_[Month]_[Initials]_sheet#
        month_name = week1_start.strftime("%B")
        output_filename = f"CalculateHours_{month_name}_{initials}_sheet{current_step}.pdf"
        output_path = os.path.join(output_dir, output_filename)

        cb(
            f"[{current_step}/{total}] Sheet '{sheet_name}' - "
            f"{week1_start.strftime('%b %d')} to {week2_start.strftime('%b %d')} "
            f"(W1: {w1_hrs:.1f}h, W2: {w2_hrs:.1f}h)",
            step=current_step,
            total=total,
        )

        success = _create_timesheet_pdf_sync(
            biweek, initials, hourly_rate, output_path, run_headless, progress=cb, is_mobile=is_mobile
        )

        if success:
            pdf_files.append(output_path)
            cb(f"  [OK] PDF {current_step}/{total} completed.", step=current_step, total=total)
        else:
            cb(f"  [FAILED] PDF {current_step}/{total} could not be generated.", step=current_step, total=total)

    cb(f"Finished: {len(pdf_files)}/{total} PDF(s) generated successfully.", step=total, total=total)
    return pdf_files, tsr_name


async def process_timesheets_async(
    excel_file,
    initials,
    hourly_rate,
    output_dir,
    run_headless=True,
    progress_callback=None,
    ignore_mismatch=False,
    is_mobile=False
):
    """
    Main entry point for generating all timesheets from the uploaded Excel file.rs.
    """
    cb = progress_callback or _noop_progress
    pdf_files = []
    tsr_name = "TSR"

    cb("Parsing Excel workbook...")
    sheets_entries_map, tsr_name = parse_excel(excel_file, hourly_rate, ignore_mismatch=ignore_mismatch)
    if not sheets_entries_map:
        return pdf_files, tsr_name

    all_biweeks = []
    for sheet_name, entries in sheets_entries_map.items():
        biweeks = group_into_biweeks(entries)
        for bw in biweeks:
            all_biweeks.append((sheet_name, bw))

    total = len(all_biweeks)
    cb(f"Found {len(sheets_entries_map)} sheet(s), {total} bi-weekly period(s) to process.", step=0, total=total)

    current_step = 0
    for sheet_name, biweek in all_biweeks:
        current_step += 1
        week1_start, week2_start, _, _ = biweek

        # Use the same naming logic for async if needed
        month_name = week1_start.strftime("%B")
        output_filename = f"CalculateHours_{month_name}_{initials}_sheet{current_step}.pdf"
        output_path = os.path.join(output_dir, output_filename)

        cb(f"[{current_step}/{total}] Processing sheet '{sheet_name}'...", step=current_step, total=total)

        success = await _create_timesheet_pdf_async(
            biweek, initials, hourly_rate, output_path, run_headless, progress=cb, is_mobile=is_mobile
        )
        if success:
            pdf_files.append(output_path)

    cb(f"Finished: {len(pdf_files)}/{total} PDF(s) generated.", step=total, total=total)
    return pdf_files, tsr_name
