import os
import re
from datetime import datetime, timedelta

def parse_date_text(raw: str) -> str:
    raw = raw.strip()
    if "\n" in raw:
        raw = raw.split("\n")[0]
    out = ""
    for ch in raw:
        if ch.isdigit() or ch == "/":
            out += ch
        else:
            break
    return out

def get_week_groups(target_dates: list) -> dict:
    if not target_dates:
        return {}

    current_year = datetime.now().year
    
    dt_list = []
    for d_str in target_dates:
        m, d = map(int, d_str.split("/"))
        dt = datetime(current_year, m, d)
        if dt < datetime.now() - timedelta(days=30):
            dt = datetime(current_year + 1, m, d)
        dt_list.append((dt, d_str))
    
    dt_list.sort(key=lambda x: x[0])

    groups = {}
    current_group_dates = []
    group_anchor_dt = None

    for dt, d_str in dt_list:
        if not current_group_dates:
            current_group_dates.append(d_str)
            group_anchor_dt = dt
        else:
            if (dt - group_anchor_dt).days <= 6:
                current_group_dates.append(d_str)
            else:
                ci_dt = group_anchor_dt + timedelta(days=3)
                ci_param = ci_dt.strftime("%Y%m%d")
                groups[ci_param] = current_group_dates
                
                current_group_dates = [d_str]
                group_anchor_dt = dt

    if current_group_dates and group_anchor_dt:
        ci_dt = group_anchor_dt + timedelta(days=3)
        ci_param = ci_dt.strftime("%Y%m%d")
        groups[ci_param] = current_group_dates

    return groups

def apply_ci_to_url(base_url: str, ci_param: str) -> str:
    if "ci=" in base_url:
        return re.sub(r'ci=\d+', f'ci={ci_param}', base_url)
    else:
        separator = "&" if "?" in base_url else "?"
        return f"{base_url}{separator}ci={ci_param}"

def scrape(browser, facility, target_dates, save_debug_html, execution_time_str, fac_idx):
    fac_name = facility["name"]
    raw_url = facility["url"]
    exclude_keywords = facility.get("exclude_keywords", [])
    
    room_data = {}
    room_order = []
    week_groups = get_week_groups(target_dates)
    
    print(f"対象施設: {fac_name} (dreserve)")
    
    for group_idx, (ci_param, dates_in_week) in enumerate(week_groups.items(), 1):
        current_url = apply_ci_to_url(raw_url, ci_param)
        group_range_str = f"{dates_in_week[0]}~{dates_in_week[-1]}" if len(dates_in_week) > 1 else dates_in_week[0]

        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(current_url, wait_until="domcontentloaded")

        try:
            page.wait_for_selector(
                "div.calendarBody_date_PC_inner p.calendarBody_date_PC_item",
                timeout=5000
            )
        except:
            pass

        date_nodes = page.locator("div.calendarBody_date_PC_inner p.calendarBody_date_PC_item")
        raw_dates = [node.inner_text() for node in date_nodes.all()]
        dates = [parse_date_text(d) for d in raw_dates]
        date_index = {d: i for i, d in enumerate(dates)}

        debug_dir = "debug_html"
        os.makedirs(debug_dir, exist_ok=True)
        debug_filename = os.path.join(debug_dir, f"{execution_time_str}_fac{fac_idx}_ci{ci_param}.html")
        with open(debug_filename, "w", encoding="utf-8") as f:
            f.write(page.content())
        if not save_debug_html and os.path.exists(debug_filename):
            os.remove(debug_filename)

        rows = page.locator("div.calendarBody_roomList")

        for i in range(rows.count()):
            row = rows.nth(i)
            name = row.locator("p.calendarBody_room_name a").inner_text().strip()
            
            should_skip = any(kw in name for kw in exclude_keywords)
            if should_skip:
                continue

            if name not in room_order:
                room_order.append(name)
            if name not in room_data:
                room_data[name] = {}

            status_cells = row.locator("div.calendarBody_vacancy_date")
            statuses = [
                status_cells.nth(j).locator("span.calendarBody_vacancy_salesStatus").inner_text().strip()
                for j in range(status_cells.count())
            ]

            for target_date in dates_in_week:
                idx = date_index.get(target_date)
                if idx is not None and idx < len(statuses):
                    room_data[name][target_date] = statuses[idx]

        page.close()
        print(f"--> 取得グループ{group_idx} ({group_range_str}) : 完了")

    print("") 

    # ボタン用のデフォルトURL（最初の週のパラメータを使用）
    default_button_url = apply_ci_to_url(raw_url, list(week_groups.keys())[0])

    return {
        "name": fac_name,
        "direct_url": default_button_url,
        "room_order": room_order,
        "room_data": room_data,
    }
