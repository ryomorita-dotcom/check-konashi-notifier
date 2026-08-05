import os
import json
import requests
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

load_dotenv()

TARGET_URL = "https://d-reserve.jp/calendar?hotelCode=0000001660&sortKeyOrder=0&lt001=0_7_8_9&lnum001=2_0_0_0"

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
#  通知
# ============================

def notify_pushover(message: str):
    if not PUSHOVER_TOKEN or not PUSHOVER_USER:
        print("Pushover の環境変数が設定されていません (.env or GitHub Secrets を確認)")
        return

    requests.post(
        "https://api.pushover.net/1/messages.json",
        data={
            "token": PUSHOVER_TOKEN,
            "user": PUSHOVER_USER,
            "message": message,
        }
    )


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
    """
    config.json の target_dates に含まれない古い日付のデータを state から削除する
    """
    cleaned = {}
    for room, dates_dict in state.items():
        cleaned_room_dict = {}
        for date_str, status in dates_dict.items():
            if date_str in target_dates:
                cleaned_room_dict[date_str] = status
        if cleaned_room_dict:
            cleaned[room] = cleaned_room_dict
    return cleaned


# ============================
#  通知すべきか判定
# ============================

def should_notify(room, date, status, state):
    prev = state.get(room, {}).get(date)
    is_available = status in ["〇", "△"]

    if prev in ["〇", "△"] and is_available:
        return False

    if prev in ["×", "―", None] and is_available:
        return True

    return False


def update_state(room, date, status, state):
    if room not in state:
        state[room] = {}
    state[room][date] = status
    save_state(state)


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
#  main
# ============================

def main():
    config = load_config()
    TARGET_DATES = config["target_dates"]
    save_debug_html = config.get("save_debug_html", False)

    print(f"予約対象日: {TARGET_DATES}")
    print(f"環境判定: {ENV}")

    # state の読み込みと不要な日付のクレンジング
    state = load_state()
    state = clean_state(state, TARGET_DATES)
    save_state(state)

    execution_time_str = datetime.now().strftime("%Y%m%dT%H%M%S")

    # ルームごとのデータを収集する辞書
    # room_data[room_name][target_date] = status
    room_data = {}
    room_order = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(TARGET_URL, wait_until="domcontentloaded")

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

            # デバッグ用HTML保存
            debug_dir = "debug_html"
            os.makedirs(debug_dir, exist_ok=True)
            debug_filename = os.path.join(debug_dir, f"{execution_time_str}_week{week}.html")
            with open(debug_filename, "w", encoding="utf-8") as f:
                f.write(page.content())
            if not save_debug_html and os.path.exists(debug_filename):
                os.remove(debug_filename)

            rows = page.locator("div.calendarBody_roomList")

            for i in range(rows.count()):
                row = rows.nth(i)
                name = row.locator("p.calendarBody_room_name a").inner_text().strip()
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
                        # すでに他の週の走査で値が入っていなければ格納
                        if target_date not in room_data[name]:
                            room_data[name][target_date] = statuses[idx]

            if week < max_week:
                next_btn = page.locator("div.calendarBody_vacancy_next a").first
                next_btn.click()
                page.wait_for_timeout(1000)

        browser.close()

    # ============================
    #  集計・判定・通知・出力
    # ============================

    # 1. [一覧] の表示
    print("\n[一覧]")
    dates_str = ", ".join([f"'{d}'" for d in TARGET_DATES])
    print(f"日付: {dates_str}")
    for room in room_order:
        statuses_list = [room_data.get(room, {}).get(d, "-") for d in TARGET_DATES]
        print(f"{room}: {','.join(statuses_list)}")

    # 2. [日付ごと] の集計
    print("\n[日付ごと]")
    date_availability = {} # date: [available_rooms...]
    for d in TARGET_DATES:
        available_rooms = []
        for room in room_order:
            status = room_data.get(room, {}).get(d, "-")
            if status in ["〇", "△"]:
                available_rooms.append(room)
        date_availability[d] = available_rooms

        if available_rooms:
            print(f"{d}: 空室あり ({', '.join(available_rooms)})")
        else:
            print(f"{d}: 空室なし")

    # 3. [通知] の判定と実行
    print("\n[通知]")
    notification_logs = []

    for d in TARGET_DATES:
        available_rooms = date_availability.get(d, [])
        for room in available_rooms:
            status = room_data.get(room, {}).get(d)
            if should_notify(room, d, status, state):
                # 新規通知
                msg = f"{room} の {d} が空いてる ({status})"
                notify_pushover(msg)
                notification_logs.append(f"{d} {room}: 通知あり (新規のため通知)")
            else:
                # 通知済み
                notification_logs.append(f"{d} {room}: 通知なし (通知済み)")

            # ステートを更新
            update_state(room, d, status, state)

    if notification_logs:
        for log in notification_logs:
            print(log)
    else:
        print("新規の通知対象はありませんでした。")


if __name__ == "__main__":
    main()