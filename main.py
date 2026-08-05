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

    print(f"[{ENV}] 通知: {message}")

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
        # 対象日のデータが1件でも残るルームのみ保持する
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
    """
    画面上の1週目初日（base_date: datetime型）を基準として、
    各ターゲット日付が何週目（1始まり）にあたるかを計算し、その最大値を返す
    """
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

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(TARGET_URL, wait_until="domcontentloaded")

        # Week 1 の日付群を最初に取得して、動的な基準日（base_date）を決定する
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

        print(f"動的基準日 (Week 1 初日): {base_date.strftime('%Y/%m/%d')}")

        # ターゲット日付から必要な最大週数を動的に算出
        max_week = get_target_dates_week_indices(TARGET_DATES, base_date)
        print(f"算出された最大週数 (max_week): {max_week} 週目までチェックします")

        found = []

        for week in range(1, max_week + 1):

            print(f"\n=== WEEK {week} ===")

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

            # 正常に7日分取得できた場合
            if len(dates) >= 7:
                print("DATES:", dates)
            else:
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
                print("DATES (COMPLETED):", dates)

            date_index = {d: i for i, d in enumerate(dates)}

            # デバッグ用HTMLを保存する専用フォルダの作成
            debug_dir = "debug_html"
            os.makedirs(debug_dir, exist_ok=True)

            # デバッグ用HTMLの出力 (debug_html/{実行日時}_week{X}.html)
            debug_filename = os.path.join(debug_dir, f"{execution_time_str}_week{week}.html")
            with open(debug_filename, "w", encoding="utf-8") as f:
                f.write(page.content())

            # 設定が false ならファイルはすぐに削除する
            if not save_debug_html:
                if os.path.exists(debug_filename):
                    os.remove(debug_filename)

            rows = page.locator("div.calendarBody_roomList")

            for i in range(rows.count()):
                row = rows.nth(i)

                name = row.locator("p.calendarBody_room_name a").inner_text().strip()
                if "持参テント" in name:
                    continue

                status_cells = row.locator("div.calendarBody_vacancy_date")
                statuses = [
                    status_cells.nth(j).locator("span.calendarBody_vacancy_salesStatus").inner_text().strip()
                    for j in range(status_cells.count())
                ]

                print(f"ROOM: {name}")
                print("STATUSES:", statuses)

                for target_date in TARGET_DATES:
                    idx = date_index.get(target_date)

                    if idx is None:
                        continue

                    status = statuses[idx]

                    if status in ["〇", "△"]:
                        if should_notify(name, target_date, status, state):
                            print(f"--- 空きあり、通知未実施のため通知します")
                            print(f"空き：あり")
                            print(f"通知：あり（初回）")

                            msg = f"{name} の {target_date} が空いてる ({status})"
                            found.append(msg)
                            notify_pushover(msg)
                        else:
                            print(f"--- 空きあり、通知済みのため通知しません")
                            print(f"空き：あり")
                            print(f"通知：なし（通知済み）")

                    update_state(name, target_date, status, state)

            if week < max_week:
                next_btn = page.locator("div.calendarBody_vacancy_next a").first
                next_btn.click()
                page.wait_for_timeout(1000)

        if not found:
            print("空きなし")

        browser.close()


if __name__ == "__main__":
    main()