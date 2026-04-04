import pandas as pd
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright
import sys
import os

# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
def format_time_12h(t):
    """Convert datetime.time to 12h string without leading zero, e.g. '8:00'."""
    return t.strftime("%I:%M").lstrip("0")

def get_week_monday(date):
    """Return the Monday of the week containing the given date (Monday first)."""
    return date - timedelta(days=date.weekday())

def parse_excel(file_path, hourly_rate=516):
    """
    Read the Excel file and extract date entries with calculated hours per sheet.
    The Excel has: DATE column and LINE TOTAL column.
    Hours = LINE_TOTAL / hourly_rate
    """
    sheets_entries = {}
    
    xl = pd.ExcelFile(file_path)
    
    for sheet_name in xl.sheet_names:
        entries = []
        # Read raw data
        df = pd.read_excel(xl, sheet_name=sheet_name, header=None)
        
        # Find header row containing 'DATE'
        header_row = -1
        for i, row in df.iterrows():
            if row.astype(str).str.contains("DATE", case=False, na=False).any():
                header_row = int(i) # Explicitly cast to int to help the type checker
                break
        if header_row == -1:
            continue
            
        # Find column indices
        header = df.iloc[header_row]
        date_col = None
        total_col = None
        for idx, val in enumerate(header):
            val_str = str(val).upper()
            if 'DATE' in val_str:
                date_col = idx
            if 'TOTAL' in val_str and 'LINE' in val_str:
                total_col = idx
        
        if date_col is None or total_col is None:
            print(f"  Warning: Could not find DATE or LINE TOTAL columns in sheet '{sheet_name}'")
            continue
            
        # Parse data rows (skip header and footer rows)
        for i in range(header_row + 1, len(df)):
            row = df.iloc[i]
            date_val = row[date_col]
            total_val = row[total_col]
            
            # Skip non-date rows
            try:
                date = pd.to_datetime(date_val)
            except:
                continue
                
            # Skip TOTAL rows
            if 'TOTAL' in str(date_val).upper():
                continue
                
            # Calculate hours from line total
            try:
                hours = float(total_val) / hourly_rate
                hours = round(hours, 2)
            except:
                continue
                
            if hours > 0:
                entries.append({
                    'date': date.date(),
                    'hours': hours,
                    'weekday': date.weekday()  # 0=Mon, 6=Sun
                })
        
        if entries:
            # Sort by date
            entries.sort(key=lambda x: x['date'])
            sheets_entries[sheet_name] = entries
            
    return sheets_entries

def group_into_biweeks(entries):
    """Group entries by bi-weekly periods (starting on Monday)."""
    if not entries:
        return []

    min_date = min(e['date'] for e in entries)
    max_date = max(e['date'] for e in entries)
    monday_start = get_week_monday(min_date)
    # Create bi-weekly periods (14 days each)
    periods = []
    current_start = monday_start
    while current_start <= max_date:
        period_end = current_start + timedelta(days=13)
        periods.append((current_start, period_end))
        current_start += timedelta(days=14)

    # Assign each entry to a period
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
    """
    Given total hours worked, calculate clock in/out times.
    Assumes 8:00 AM start, with lunch break if hours > 4.
    Returns (in1, out1, in2, out2) tuple.
    """
    start_time = datetime.strptime("8:00", "%H:%M")
    
    if hours <= 4:
        # Short shift, no lunch
        end_time = start_time + timedelta(hours=hours)
        return (start_time.time(), end_time.time(), None, None)
    else:
        # Standard shift with 1 hour lunch
        lunch_start = start_time + timedelta(hours=4)
        lunch_end = lunch_start + timedelta(hours=1)
        work_end = lunch_end + timedelta(hours=hours - 4)
        return (start_time.time(), lunch_start.time(), lunch_end.time(), work_end.time())

def fill_time(col_locator, time_obj):
    """Helper to fill hour, minute, and AM/PM into a specific column locator."""
    hour_str = time_obj.strftime("%I") # 12-hour padded
    min_str = time_obj.strftime("%M")
    is_pm = time_obj.hour >= 12
    
    col_locator.locator("input.hour").fill(hour_str)
    col_locator.locator("input.minute").fill(min_str)
    if is_pm:
        col_locator.locator("label:has-text('PM')").click()
    else:
        col_locator.locator("label:has-text('AM')").click()

def fill_day(page, day_index, entry):
    """
    Fill the time fields for a single day index (0-13).
    The site has 4 inputs per day: In1, Out1, In2 (lunch return), Out2 (end)
    """
    if not entry or entry['hours'] <= 0:
        return
        
    # Calculate times from hours
    in1, out1, in2, out2 = calculate_times_from_hours(entry['hours'])
    
    day_row = page.locator(".weekly-lunch-col.inputLine").nth(day_index)
    
    # First pair
    fill_time(day_row.locator(".wco-weekly-col-2"), in1)
    fill_time(day_row.locator(".wco-weekly-col-3"), out1)
    
    # Second pair (afternoon in/out) if we have lunch times
    if in2 and out2:
        fill_time(day_row.locator(".wco-weekly-col-4"), in2)
        fill_time(day_row.locator(".wco-weekly-col-5"), out2)

def set_date_field(page, month_id, day_id, year_id, target_date):
    """Set date using separate month/day/year fields."""
    month = target_date.strftime("%m")
    day = target_date.strftime("%d")
    year = target_date.strftime("%Y")
    
    page.locator(month_id).evaluate(f"el => el.value = '{month}'")
    page.locator(day_id).evaluate(f"el => el.value = '{day}'")
    page.locator(year_id).evaluate(f"el => el.value = '{year}'")

def create_timesheet_pdf(biweek, initials, hourly_rate, output_file):
    """Use Playwright to fill the form and save as PDF."""
    week1_start, week2_start, week1_entries, week2_entries = biweek

    # Build a dictionary for quick lookup of entries per day
    def entries_by_day(entries, week_start):
        day_map = { (week_start + timedelta(days=i)).strftime("%a"): {'hours': 0} for i in range(7) }
        for entry in entries:
            day_name = entry['date'].strftime("%a")
            day_map[day_name]['hours'] = round(day_map[day_name]['hours'] + entry['hours'], 2)
        return day_map

    week1_map = entries_by_day(week1_entries, week1_start)
    week2_map = entries_by_day(week2_entries, week2_start)

    with sync_playwright() as p:
        browser = None
        # Try finding any available browser (local Chrome/Edge, or Playwright's downloaded browsers)
        for browser_type, channel in [
            (p.chromium, "chrome"),
            (p.chromium, "msedge"),
            (p.chromium, None),
            (p.firefox, None),
            (p.webkit, None)
        ]:
            try:
                kwargs = {"headless": False, "channel": channel} if channel else {"headless": False}
                browser = browser_type.launch(**kwargs)
                break
            except Exception:
                pass
                
        if not browser:
            print("Failed to launch any browser. Run 'playwright install' or ensure Chrome/Edge is installed.")
            return
            
        assert browser is not None

        context = browser.new_context(
            viewport={"width": 1400, "height": 1200},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        page.set_default_timeout(60000)  # 60s default timeout for slow connections
        page.set_default_navigation_timeout(60000)
        
        for attempt in range(3):
            try:
                page.goto("https://www.calculatehours.com/m/Time-Card-Calculator-Biweekly+Lunch.html", wait_until="domcontentloaded", timeout=60000)
                page.wait_for_load_state("networkidle", timeout=60000)
                page.wait_for_timeout(3000)  # Extra wait for JS to initialize (increased for slow connections)
                break
            except Exception as e:
                import time
                print(f"  Navigation error (attempt {attempt+1}/3). Retrying in 3 seconds...")
                time.sleep(3)
        else:
            print("Failed to load calculator page after 3 attempts.")
            browser.close()
            return

        # ------------------------------------------------------------------
        # Remove Consent Overlays that might intercept clicks
        # ------------------------------------------------------------------
        try:
            # Attempt to click "Consent" button if it exists
            consent_btn = page.locator("button.fc-cta-consent, button:has-text('Consent')").first
            if consent_btn.is_visible(timeout=2000):
                consent_btn.click(force=True)
                page.wait_for_timeout(1000)
            
            # As a fallback, completely remove any remaining consent overlays via JS
            page.evaluate("""
                const overlays = document.querySelectorAll('.fc-consent-root, .fc-dialog-overlay, .fc-dialog-container');
                overlays.forEach(el => el.remove());
                document.body.style.overflow = 'auto';
            """)
        except Exception as e:
            print(f"  Warning: Could not dismiss consent overlay: {e}")

        # ------------------------------------------------------------------
        # Set dates for the two weeks (separate month/day/year fields)
        # ------------------------------------------------------------------
        try:
            set_date_field(page, "#month", "#day", "#year", week1_start)
            set_date_field(page, "#month2", "#day2", "#year2", week2_start)
            print(f"Set dates: {week1_start.strftime('%m/%d/%Y')} to {week2_start.strftime('%m/%d/%Y')}")
        except Exception as e:
            print(f"Warning: Could not set dates: {e}")

        # ------------------------------------------------------------------
        # Set initials and hourly rate
        # ------------------------------------------------------------------
        try:
            initials_input = page.locator("#name").first
            if initials_input.count():
                initials_input.fill(initials)
                print(f"Set initials: {initials}")
        except Exception as e:
            print(f"Warning: Could not set initials: {e}")
            
        try:
            rate_input = page.locator("input[placeholder*='Rate'], input[name='rate']").first
            if rate_input.count():
                rate_input.fill(str(hourly_rate))
                print(f"Set hourly rate: {hourly_rate}")
        except Exception as e:
            print(f"Warning: Could not set rate: {e}")

        # ------------------------------------------------------------------
        # Fill in the times for week 1 and week 2
        # ------------------------------------------------------------------
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        
        print("Filling Week 1 times...")
        for idx, day in enumerate(day_names):
            entry = week1_map.get(day)
            if entry and entry['hours'] > 0:
                print(f"  {day}: {entry['hours']:.1f} hours")
                fill_day(page, idx, entry)
        
        print("Filling Week 2 times...")
        for idx, day in enumerate(day_names):
            entry = week2_map.get(day)
            if entry and entry['hours'] > 0:
                print(f"  {day}: {entry['hours']:.1f} hours")
                fill_day(page, idx + 7, entry)

        # ------------------------------------------------------------------
        # Click CALCULATE to update totals
        # ------------------------------------------------------------------
        calc_button = page.locator("input[value='CALCULATE'], button:has-text('CALCULATE')")
        if calc_button.count():
            calc_button.click()
            page.wait_for_timeout(1000)
            print("Clicked CALCULATE")

        # ------------------------------------------------------------------
        # Click PRINT button and capture the new window
        # ------------------------------------------------------------------
        print_button = page.locator("#printpage")
        if print_button.count():
            print("Clicking PRINT button...")
            
            with context.expect_page() as popup_info:
                print_button.click()
            
            popup = popup_info.value
            popup.wait_for_load_state(timeout=60000)
            page.wait_for_timeout(3000)
            
            popup.pdf(path=output_file, format="A4", print_background=True)
            popup.close()
            print(f"Saved PDF: {output_file}")
        else:
            print("Print button not found, saving current page...")
            page.pdf(path=output_file, format="A4", print_background=True)
            print(f"Saved PDF: {output_file}")
        
        browser.close()

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Get user input
    excel_file = input("Path to Excel file (press Enter to choose from 'Excel Timesheets' folder): ").strip()
    if not excel_file:
        target_dir = "Excel Timesheets"
        if not os.path.exists(target_dir):
            print(f"Folder '{target_dir}' not found in current directory.")
            sys.exit(1)
            
        # Ignore temporary excel files starting with ~
        xlsx_files = [f for f in os.listdir(target_dir) if f.endswith(".xlsx") and not f.startswith("~")]
        
        if not xlsx_files:
            print(f"No Excel files found in '{target_dir}' folder.")
            sys.exit(1)
            
        if len(xlsx_files) == 1:
            excel_file = os.path.abspath(os.path.join(target_dir, xlsx_files[0]))
            print(f"Using: {excel_file}")
        else:
            print(f"\nExcel files found in '{target_dir}':")
            for idx, f in enumerate(xlsx_files, 1):
                print(f"{idx}. {f}")
            
            while True:
                choice = input("\nEnter the number of the file to load: ").strip()
                try:
                    choice_idx = int(choice)
                    if 1 <= choice_idx <= len(xlsx_files):
                        selected_file = xlsx_files[choice_idx - 1]
                        excel_file = os.path.abspath(os.path.join(target_dir, selected_file))
                        print(f"Selected: {excel_file}")
                        break
                    else:
                        print("Invalid number. Please try again.")
                except ValueError:
                    print("Please enter a valid number.")
    elif not os.path.isabs(excel_file):
        excel_file = os.path.abspath(excel_file)

    initials = input("Initials (e.g., JT): ").strip()
    try:
        hourly_rate = float(input("Hourly rate (default 516): ").strip() or "516")
    except ValueError:
        hourly_rate = 516

    # Parse data
    print("\nReading Excel file...")
    sheets_entries_map = parse_excel(excel_file, hourly_rate)
    if not sheets_entries_map:
        print("No valid time entries found.")
        sys.exit(1)

    for sheet_name, entries in sheets_entries_map.items():
        print(f"\n--- Processing Sheet: {sheet_name} ---")
        print(f"Found {len(entries)} date entries.")
        biweeks = group_into_biweeks(entries)
        print(f"Creating {len(biweeks)} bi-weekly timesheet(s) for sheet '{sheet_name}'.\n")

        # Keep track of counts per month for the current sheet
        month_counts = {}

        # Generate one PDF per bi-weekly period
        for i, biweek in enumerate(biweeks):
            week1_start, week2_start, _, _ = biweek
            
            # Get full month name
            month_name = week1_start.strftime("%B")
            month_counts[month_name] = month_counts.get(month_name, 0) + 1
            count = month_counts[month_name]
            
            # Formulate safe filename containing sheet name
            safe_sheet = "".join(c for c in sheet_name if c.isalnum() or c in (' ', '_')).strip()
            output = f"CalculateHours {safe_sheet} {month_name} {count}.pdf"
            
            print(f"=== Generating timesheet {i+1}/{len(biweeks)}: {output} ===")
            create_timesheet_pdf(biweek, initials, hourly_rate, output)

    print("\nDone.")
