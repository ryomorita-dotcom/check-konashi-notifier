import os
import json
import requests
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
    # 施設ごとの状態管理構造に対応したクレンジング
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

    # 今回が空室ではない場合は絶対に通知しない
    if not is_current_available:
        return False

    # 前回が空室（〇または△）だった場合はすでに通知済みなので通知しない
    if prev_status in ["〇", "△"]:
        return False

    # 前回が満室（×）、未設定（―）、または記録なし（None）で、今回空室になった場合は通知する！
    if prev_status in ["×", "―", None]:
        return True

    return False


# ============================
#  日付処理
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

def get_target_dates_week_indices(target_dates, base_date):
    max_w = 1
    for d_str in target_dates:
        m, d = map(int, d_str.split("/"))
        target_dt = datetime(base_date.year, m, d)
        if target_dt < base_date:
            target_dt = datetime(base_date.year + 1, m, d)
        
        delta_days = (target_dt - base_date).days
        week_idx = delta_days // 7 + 1
        if week_idx > max_w:
            max_w = week_idx
    return max_w


# ============================
#  HTML生成関数 (複数施設対応)
# ============================

def generate_html_report(target_dates, facilities_data, notification_logs, pushover_url):
    JST = timezone(timedelta(hours=9))
    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    dates_meta = ", ".join(target_dates)

    th_dates_html = "".join([f"<th>{d}</th>" for d in target_dates])
    
    facilities_html = ""
    for fac in facilities_data:
        name = fac["name"]
        url = fac["url"]
        room_order = fac["room_order"]
        room_data = fac["room_data"]
        date_availability = fac["date_availability"]

        # 部屋ごとのマトリクス行
        rows_html = ""
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
            rows_html += f"<tr>{cells_html}</tr>\n"

        # 日付ごとの空室状況行
        date_rows_html = ""
        for d in target_dates:
            available_rooms = date_availability.get(d, [])
            if available_rooms:
                status_text = '<span class="status-sankaku">空室あり</span>'
                room_text = f"<strong>{', '.join(available_rooms)}</strong>"
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

        facilities_html += f"""
        <div class="facility-section">
            <h2>🏨 {name}</h2>
            
            <table>
                <thead>
                    <tr>
                        <th>ルーム名 \\ 日付</th>
                        {th_dates_html}
                    </tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
            </table>

            <div class="section-title">📅 日付ごとの空室状況 ({name})</div>
            <table>
                <thead>
                    <tr>
                        <th>日付</th>
                        <th>状態</th>
                        <th>空室ルーム</th>
                    </tr>
                </thead>
                <tbody>
                    {date_rows_html}
                </tbody>
            </table>

            <div class="action-container">
                <a href="{url}" target="_blank" class="btn-reserve">
                    {name} の公式予約ページを開く
                </a>
            </div>
        </div>
        <hr style="border:0; border-top:1px solid #ddd; margin:30px 0;">
        """

    logs_li = ""
    if notification_logs:
        for log in notification_logs:
            logs_li += f"<li>{log}</li>\n"
    else:
        logs_li = "<li>新規の通知対象はありませんでした。</li>"

    html_content = f"""<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <title>予約空室チェッカー 結果</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            margin: 20px;
            color: #333;
            background-color: #f9f9f9;
            display: inline-block;
            zoom: 1.2;
        }}
        h1 {{
            font-size: 1.3rem;
            margin-bottom: 5px;
        }}
        h2 {{
            font-size: 1.1rem;
            color: #0366d6;
            margin-top: 15px;
            margin-bottom: 8px;
        }}
        .meta {{
            font-size: 0.85rem;
            color: #666;
            margin-bottom: 20px;
        }}
        table {{
            width: auto;
            border-collapse: collapse;
            background: #fff;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            margin-bottom: 15px;
        }}
        th, td {{
            border: 1px solid #e1e4e8;
            padding: 8px 14px;
            text-align: center;
            font-size: 0.9rem;
            white-space: nowrap;
        }}
        th {{
            background-color: #f1f8ff;
            font-weight: 600;
        }}
        td.room-name {{
            text-align: left;
            font-weight: 500;
            background-color: #fcfcfc;
        }}
        .status-maru {{
            color: #d73a49;
            font-size: 1.15rem;
            font-weight: bold;
        }}
        .status-sankaku {{
            color: #e36209;
            font-size: 1.15rem;
            font-weight: bold;
        }}
        .status-batsu {{
            color: #6a737d;
            font-size: 1.15rem;
            font-weight: bold;
        }}
        .status-dash {{
            color: #dfe2e5;
            font-size: 1.15rem;
            font-weight: bold;
        }}
        .section-title {{
            font-size: 0.95rem;
            margin-top: 15px;
            margin-bottom: 6px;
            border-bottom: 2px solid #eaecef;
            padding-bottom: 3px;
            font-weight: bold;
        }}
        ul {{
            padding-left: 20px;
            font-size: 0.9rem;
            margin: 5px 0;
        }}
        li {{
            margin-bottom: 3px;
        }}
        .action-container {{
            margin-top: 10px;
            margin-bottom: 15px;
            text-align: left;
        }}
        .btn-reserve {{
            display: inline-block;
            background-color: #0366d6;
            color: #ffffff;
            padding: 8px 16px;
            font-size: 0.85rem;
            font-weight: bold;
            text-decoration: none;
            border-radius: 6px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.15);
            transition: background-color 0.2s;
        }}
        .btn-reserve:hover {{
            background-color: #0056b3;
        }}
    </style>
</head>
<body>

    <h1>キャンプ場・山小屋 予約空室状況チェッカー</h1>
    <div class="meta">
        <strong>最終更新:</strong> {now_str} (JST) / 
        <strong>対象日:</strong> {dates_meta}
    </div>

    {facilities_html}

    <div class="section-title">🔔 通知ログ</div>
    <ul>
        {logs_li}
    </ul>

</body>
</html>
"""
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

    # 旧設定（単一URL）との互換性フォールバック
    if not facilities and "TARGET_URL" in globals():
        facilities = [{"name": "小梨平（上高地キャンプ場 ケビン）", "url": TARGET_URL}]

    state = load_state()
    state = clean_state(state, TARGET_DATES)

    execution_time_str = datetime.now().strftime("%Y%m%dT%H%M%S")

    JST = timezone(timedelta(hours=9))
    now_jst = datetime.now(JST)
    current_datetime_str = now_jst.strftime("%m/%d, %H:%M")

    notification_logs = []
    facilities_data = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        
        for fac_idx, facility in enumerate(facilities):
            fac_name = facility["name"]
            fac_url = facility["url"]
            print(f"\n--- 取得中: {fac_name} ---")

            page = browser.new_page(viewport={"width": 1920, "height": 1080})
            page.goto(fac_url, wait_until="domcontentloaded")

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

            if len(dates) >= 1 and "/" in dates[0]:
                m, d = map(int, dates[0].split("/"))
                current_year = datetime.now().year
                base_date = datetime(current_year, m, d)
            else:
                base_date = datetime(2026, 8, 1)

            max_week = get_target_dates_week_indices(TARGET_DATES, base_date)

            room_data = {}
            room_order = []

            for week in range(1, max_week + 1):
                if week > 1:
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

                if len(dates) < 7:
                    if len(dates) >= 1 and "/" in dates[0]:
                        try:
                            m, d = map(int, dates[0].split("/"))
                            current_year = datetime.now().year
                            start = datetime(current_year, m, d)
                        except:
                            start = base_date
                    else:
                        start = base_date
                    dates = [(start + timedelta(days=i)).strftime("%-m/%-d") for i in range(7)]

                date_index = {d: i for i, d in enumerate(dates)}

                debug_dir = "debug_html"
                os.makedirs(debug_dir, exist_ok=True)
                debug_filename = os.path.join(debug_dir, f"{execution_time_str}_fac{fac_idx}_week{week}.html")
                with open(debug_filename, "w", encoding="utf-8") as f:
                    f.write(page.content())
                if not save_debug_html and os.path.exists(debug_filename):
                    os.remove(debug_filename)

                rows = page.locator("div.calendarBody_roomList")

                for i in range(rows.count()):
                    row = rows.nth(i)
                    name = row.locator("p.calendarBody_room_name a").inner_text().strip()
                    
                    # 小梨平などの持参テント除外（必要に応じて他施設にも適用可）
                    if "持参テント" in name:
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

                    for target_date in TARGET_DATES:
                        idx = date_index.get(target_date)
                        if idx is not None and idx < len(statuses):
                            if target_date not in room_data[name]:
                                room_data[name][target_date] = statuses[idx]

                if week < max_week:
                    next_btn = page.locator("div.calendarBody_vacancy_next a").first
                    next_btn.click()
                    page.wait_for_timeout(1000)

            page.close()

            # 日付ごとの空室集計
            date_availability = {}
            for d in TARGET_DATES:
                available_rooms = []
                for room in room_order:
                    status = room_data.get(room, {}).get(d, "-")
                    if status in ["〇", "△"]:
                        available_rooms.append(room)
                date_availability[d] = available_rooms

            # 状態管理・通知判定
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

            facilities_data.append({
                "name": fac_name,
                "url": fac_url,
                "room_order": room_order,
                "room_data": room_data,
                "date_availability": date_availability
            })

        browser.close()

    save_state(state)

    # ターミナル出力用
    output_lines = [f"予約対象日: {TARGET_DATES}", f"環境判定: {ENV}"]
    for fac in facilities_data:
        output_lines.append(f"\n[{fac['name']}]")
        for room in fac["room_order"]:
            statuses_list = [fac["room_data"].get(room, {}).get(d, "-") for d in TARGET_DATES]
            output_lines.append(f"  {room}: {','.join(statuses_list)}")
    
    print("\n".join(output_lines))

    # HTML生成
    html_report = generate_html_report(TARGET_DATES, facilities_data, notification_logs, pushover_url)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_report)
    print("\n------> index.html を生成しました（複数施設対応版）。")


if __name__ == "__main__":
    main()