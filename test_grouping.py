from datetime import timedelta
from backend.timesheet_bot import parse_excel, group_into_biweeks, get_week_monday

sheets_map, _ = parse_excel('Javian Saunders TSR Timesheet.xlsx', 516)

for sheet_name, entries in sheets_map.items():
    print(f"\n{'='*60}")
    print(f"Sheet: {sheet_name}")
    print(f"{'='*60}")
    
    print("\nAll entries:")
    for e in entries:
        mon = get_week_monday(e['date'])
        print(f"  {e['date']} ({e['date'].strftime('%A')})  week_monday={mon}  hours={e['hours']}")
    
    bws = group_into_biweeks(entries)
    print(f"\nCurrent grouping: {len(bws)} PDF(s)")
    for i, (w1s, w2s, w1e, w2e) in enumerate(bws):
        end = w1s + timedelta(days=13)
        w1h = sum(e['hours'] for e in w1e)
        w2h = sum(e['hours'] for e in w2e)
        print(f"  PDF {i+1}: {w1s} to {end}  |  W1: {len(w1e)} days ({w1h:.1f}h), W2: {len(w2e)} days ({w2h:.1f}h)")
    
    # Simulate Monday-aligned grouping
    min_date = min(e['date'] for e in entries)
    max_date = max(e['date'] for e in entries)
    monday_start = get_week_monday(min_date)
    
    periods = []
    current = monday_start
    while current <= max_date:
        periods.append((current, current + timedelta(days=13)))
        current += timedelta(days=14)
    
    print(f"\nMonday-aligned grouping: ", end="")
    count = 0
    for start, end in periods:
        w1s = start
        w1e_list = [e for e in entries if w1s <= e['date'] <= w1s + timedelta(days=6)]
        w2s = start + timedelta(days=7)
        w2e_list = [e for e in entries if w2s <= e['date'] <= w2s + timedelta(days=6)]
        if w1e_list or w2e_list:
            count += 1
            w1h = sum(e['hours'] for e in w1e_list)
            w2h = sum(e['hours'] for e in w2e_list)
            print(f"\n  PDF {count}: {start} to {end}  |  W1: {len(w1e_list)} days ({w1h:.1f}h), W2: {len(w2e_list)} days ({w2h:.1f}h)")
    print(f"\n  Total: {count} PDF(s)")
