import os
import json
import requests
import re
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

load_dotenv()

PUSHOVER_TOKEN = os.getenv("PUSHOVER_TOKEN")
PUSHOVER_USER = os.getenv("PUSHOVER_USER")


# ============================
#  環境判定
# ============================

def is_wsl():
    try:
        with open("/proc/sys/kernel/osrelease") as f:
            return "WSL" in f.read()
    except:
        return False

def is_github_actions():
    return os.getenv("GITHUB_ACTIONS") == "true"

def get_environment():
    if is_github_actions():
        return "GITHUB"
    if is_wsl():
        return "WSL"
    return "OTHER"

ENV = get_environment()


# ============================
#  通知 (URLリンク対応)
# ============================

def notify_pushover(message: str, pushover_url: str = ""):
    if not PUSHOVER_TOKEN or not PUSHOVER_USER:
        print("Pushover の環境変数が設定されていません (.env or GitHub Secrets を確認)")
        return

    payload = {
        "token": PUSHOVER_TOKEN,
        "user": PUSHOVER_USER,
        "message": message,
    }
    
    if pushover_url:
        payload["url"] = pushover_url
        payload["url_title"] = "最新の空室状況をブラウザで確認する"

    requests.post("https://api.pushover.net/1/messages.json", data=payload)


# ============================
#  config.json 読み込み
# ============================

def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)


# ============================
#  state.json 読み込み・保存・クレンジング
# ============================

STATE_FILE = "state.json"

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except:
        return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def clean_state(state, target_dates):
    cleaned = {}
    for facility_name, rooms_dict in state.items():
        if not isinstance(rooms_dict, dict):
            continue
        cleaned_rooms = {}
        for room, dates_dict in rooms_dict.items():
            if not isinstance(dates_dict, dict):
                continue
            cleaned_room_dict = {}
            for date_str, status in dates_dict.items():
                if date_str in target_dates:
                    cleaned_room_dict[date_str] = status
            if cleaned_room_dict:
                cleaned_rooms[room] = cleaned_room_dict
        if cleaned_rooms:
            cleaned[facility_name] = cleaned_rooms
    return cleaned


# ============================
#  通知すべきか判定
# ============================

def should_notify(facility_state, room, date, current_status):
    prev_status = facility_state.get(room, {}).get(date)
    is_current_available = current_status in ["〇", "△"]

    if not is_current_available:
        return False

    if prev_status in ["〇", "△"]:
        return False

    if prev_status in ["×", "―", None]:
        return True

    return False


# ============================
#  日付処理・URL生成 (スマートグループ化対応)
# ============================

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


# ============================
#  HTML生成関数 (template.html 読み込み型)
# ============================

def get_short_facility_name(full_name: str) -> str:
    if "（" in full_name:
        return full_name.split("（")[0]
    if "(" in full_name:
        return full_name.split("(")[0]
    return full_name[:4]

def generate_html_report(target_dates, facilities_data, notification_logs, pushover_url):
    JST = timezone(timedelta(hours=9))
    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    dates_meta = ", ".join(target_dates)

    th_dates_html = "".join([f"<th>{d}</th>" for d in target_dates])
    
    all_rows_html = ""
    for fac in facilities_data:
        name = fac["name"]
        room_order = fac["room_order"]
        room_data = fac["room_data"]

        all_rows_html += f'<tr class="facility-header-row"><td colspan="{len(target_dates) + 1}">🏨 {name}</td></tr>\n'

        for room in room_order:
            cells_html = f'<td class="room-name">{room}</td>'
            for d in target_dates:
                status = room_data.get(room, {}).get(d, "-")
                if status == "〇":
                    css_class = "status-maru"
                elif status == "△":
                    css_class = "status-sankaku"
                elif status == "×":
                    css_class = "status-batsu"
                else:
                    css_class = "status-dash"
                cells_html += f'<td><span class="{css_class}">{status}</span></td>'
            all_rows_html += f"<tr>{cells_html}</tr>\n"

    date_rows_html = ""
    for d in target_dates:
        available_rooms_with_fac = []
        for fac in facilities_data:
            short_name = get_short_facility_name(fac["name"])
            for room in fac["room_order"]:
                status = fac["room_data"].get(room, {}).get(d, "-")
                if status in ["〇", "△"]:
                    available_rooms_with_fac.append(f"({short_name}) {room}")

        if available_rooms_with_fac:
            status_text = '<span class="status-sankaku">空室あり</span>'
            room_text = f"<strong>{', '.join(available_rooms_with_fac)}</strong>"
            date_cell_style = f"<strong>{d}</strong>"
        else:
            status_text = '<span class="status-batsu">空室なし</span>'
            room_text = "-"
            date_cell_style = d
        
        date_rows_html += f"""
            <tr>
                <td>{date_cell_style}</td>
                <td>{status_text}</td>
                <td>{room_text}</td>
            </tr>
        """

    buttons_html = ""
    for fac in facilities_data:
        buttons_html += f"""
            <div>
                <a href="{fac['direct_url']}" target="_blank" class="btn-reserve">
                    🏕️ {fac['name']} の公式予約ページを開く
                </a>
            </div>
        """

    logs_li = ""
    if notification_logs:
        for log in notification_logs:
            logs_li += f"<li>{log}</li>\n"
    else:
        logs_li = "<li>新規の通知対象はありませんでした。</li>"

    # 外部の template.html からレイアウトを読み込んで値を埋め込む
    with open("template.html", "r", encoding="utf-8") as f:
        template = f.read()

    html_content = template.format(
        now_str=now_str,
        dates_meta=dates_meta,
        th_dates_html=th_dates_html,
        all_rows_html=all_rows_html,
        date_rows_html=date_rows_html,
        buttons_html=buttons_html,
        logs_li=logs_li
    )

    return html_content


# ============================
#  main
# ============================

def main():
    config = load_config()
    TARGET_DATES = config["target_dates"]
    save_debug_html = config.get("save_debug_html", False)
    pushover_url = config.get("pushover_url", "")
    facilities = config.get("facilities", [])

    state = load_state()
    state = clean_state(state, TARGET_DATES)

    execution_time_str = datetime.now().strftime("%Y%m%dT%H%M%S")

    JST = timezone(timedelta(hours=9))
    now_jst = datetime.now(JST)
    current_datetime_str = now_jst.strftime("%m/%d, %H:%M")

    week_groups = get_week_groups(TARGET_DATES)

    notification_logs = []
    facilities_data = []

    print("[データ取得]")
    print(f"対象日付 (共通): {', '.join(TARGET_DATES)}\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        
        for fac_idx, facility in enumerate(facilities):
            fac_name = facility["name"]
            raw_url = facility["url"]
            exclude_keywords = facility.get("exclude_keywords", [])
            
            room_data = {}
            room_order = []
            
            print(f"対象施設: {fac_name}")
            
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
                    
                    should_skip = False
                    for kw in exclude_keywords:
                        if kw in name:
                            should_skip = True
                            break
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

            date_availability = {}
            for d in TARGET_DATES:
                available_rooms = []
                for room in room_order:
                    status = room_data.get(room, {}).get(d, "-")
                    if status in ["〇", "△"]:
                        available_rooms.append(room)
                date_availability[d] = available_rooms

            if fac_name not in state:
                state[fac_name] = {}

            for room in room_order:
                if room not in state[fac_name]:
                    state[fac_name][room] = {}
                for d in TARGET_DATES:
                    current_status = room_data.get(room, {}).get(d, "-")
                    
                    if should_notify(state[fac_name], room, d, current_status):
                        msg = f"[{fac_name}] {room} の {d} が空いてる ({current_status})"
                        notify_pushover(msg, pushover_url)
                        notification_logs.append(f"[{current_datetime_str}] [{fac_name}] 対象日 {d} に空きあり ({room}: 新規空室検知 ({current_status} に変化))")

                    state[fac_name][room][d] = current_status

            default_button_url = apply_ci_to_url(raw_url, list(week_groups.keys())[0])

            facilities_data.append({
                "name": fac_name,
                "direct_url": default_button_url,
                "room_order": room_order,
                "room_data": room_data,
                "date_availability": date_availability
            })

        browser.close()

    save_state(state)

    print("----------------------------------------")
    print(f"予約対象日: {TARGET_DATES}")
    print(f"環境判定: {ENV}")
    for fac in facilities_data:
        print(f"\n[{fac['name']}]")
        for room in fac["room_order"]:
            statuses_list = [fac["room_data"].get(room, {}).get(d, "-") for d in TARGET_DATES]
            print(f"  {room}: {','.join(statuses_list)}")
    print("----------------------------------------")

    html_report = generate_html_report(TARGET_DATES, facilities_data, notification_logs, pushover_url)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_report)
    print("------> index.html を生成しました。")


if __name__ == "__main__":
    main()