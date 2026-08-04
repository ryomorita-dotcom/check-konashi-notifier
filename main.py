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
#  state.json 読み込み・保存
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
#  config.json 読み込み
# ============================

def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)


# ============================
#  日付処理
# ============================

def date_to_week_index(date_str):
    month, day = map(int, date_str.split("/"))
    base = datetime(2026, 8, 1)
    target = datetime(2026, month, day)
    delta_days = (target - base).days
    return delta_days // 7 + 1

def week_start_date(week_index):
    base = datetime(2026, 8, 1)
    start = base + timedelta(days=(week_index - 1) * 7)
    return start.strftime("%Y%m%d")

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


# ============================
#  main
# ============================

def main():
    config = load_config()
    TARGET_DATES = config["target_dates"]

    max_week = max(date_to_week_index(d) for d in TARGET_DATES)

    print(f"予約対象日: {TARGET_DATES}")
    print(f"{max_week} 週目までチェックします")
    print(f"環境判定: {ENV}")

    state = load_state()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(TARGET_URL, wait_until="domcontentloaded")

        found = []

        for week in range(1, max_week + 1):

            print(f"\n=== WEEK {week} ===")

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

            print("DATES (PC):", dates)

            if week == 1:
                if len(dates) >= 1:
                    m, d = map(int, dates[0].split("/"))
                    start = datetime(2026, m, d)
                else:
                    start = datetime(2026, 8, 1)

                dates = [(start + timedelta(days=i)).strftime("%-m/%-d") for i in range(7)]
                print("DATES (WEEK1 COMPLETED):", dates)

            date_index = {d: i for i, d in enumerate(dates)}

            start_date = week_start_date(week)
            with open(f"week_{start_date}.html", "w", encoding="utf-8") as f:
                f.write(page.content())

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

                    # ★ 対象日がその週に存在しない → ログ不要
                    if idx is None:
                        continue

                    status = statuses[idx]

                    # ★ 空室ありの場合だけログを出す
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

                    # ★ 満室の場合はログ不要（あなたの意図）
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
