import json
import os
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# モジュール群のインポート
from scrapers import dreserve, tenawan

load_dotenv()

PUSHOVER_TOKEN = os.getenv("PUSHOVER_TOKEN")
PUSHOVER_USER = os.getenv("PUSHOVER_USER")

# スクレイパーのマッピング
SCRAPER_MAP = {
    "dreserve": dreserve,
    "tenawan": tenawan,
}


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
#  HTML生成関数 (template.html 読み込み型)
# ============================

def get_day_info(date_str: str):
  current_year = datetime.now().year
  m, d = map(int, date_str.split("/"))
  dt = datetime(current_year, m, d)

  days_jp = ["(月)", "(火)", "(水)", "(木)", "(金)", "(土)", "(日)"]
  weekday_str = days_jp[dt.weekday()]

  holidays_2026 = [
      "2026-09-21",  # 敬老の日
      "2026-09-23",  # 秋分の日
      "2026-09-22",  # 国民の休日など
  ]

  dt_str = dt.strftime("%Y-%m-%d")
  weekday = dt.weekday()

  if dt_str in holidays_2026 or weekday == 6:
    return "sun-hol", weekday_str
  elif weekday == 5:
    return "sat", weekday_str
  else:
    return "weekday", weekday_str


def generate_html_report(
    target_dates, facilities_data, notification_logs, pushover_url
):
  JST = timezone(timedelta(hours=9))
  now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
  dates_meta = ", ".join(target_dates)

  th_dates_html = ""
  for d in target_dates:
    css_cls, weekday_str = get_day_info(d)
    th_dates_html += f'<th class="{css_cls}">{d} {weekday_str}</th>'

  all_rows_html = ""
  for fac in facilities_data:
    name = fac["name"]
    direct_url = fac["direct_url"]
    room_order = fac["room_order"]
    room_data = fac["room_data"]

    all_rows_html += f"""
            <tr class="facility-header-row">
                <td colspan="{len(target_dates) + 1}">
                    🏨 <a href="{direct_url}" target="_blank" class="facility-link">{name} <span class="external-icon">&#x2197;</span></a>
                </td>
            </tr>
        """

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
    css_cls, weekday_str = get_day_info(d)

    available_rooms_with_fac = []
    for fac in facilities_data:
      short_name = (
          fac["name"].split("（")[0] if "（" in fac["name"] else fac["name"][:4]
      )
      for room in fac["room_order"]:
        status = fac["room_data"].get(room, {}).get(d, "-")
        if status in ["〇", "△"]:
          available_rooms_with_fac.append(f"({short_name}) {room}")

    if available_rooms_with_fac:
      status_text = '<span class="status-sankaku">空室あり</span>'
      room_text = f"<strong>{', '.join(available_rooms_with_fac)}</strong>"
    else:
      status_text = '<span class="status-batsu">空室なし</span>'
      room_text = "-"

    date_rows_html += f"""
            <tr>
                <td><span class="{css_cls}"><strong>{d} {weekday_str}</strong></span></td>
                <td>{status_text}</td>
                <td>{room_text}</td>
            </tr>
        """

  logs_li = ""
  if notification_logs:
    for log in notification_logs:
      logs_li += f"<li>{log}</li>\n"
  else:
    logs_li = "<li>新規の通知対象はありませんでした。</li>"

  with open("template.html", "r", encoding="utf-8") as f:
    template = f.read()

  html_content = template.format(
      now_str=now_str,
      dates_meta=dates_meta,
      th_dates_html=th_dates_html,
      all_rows_html=all_rows_html,
      date_rows_html=date_rows_html,
      logs_li=logs_li,
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

  notification_logs = []
  facilities_data = []

  print("[データ取得]")
  print(f"対象日付 (共通): {', '.join(TARGET_DATES)}\n")

  with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    process_facilities(
        facilities,
        browser,
        TARGET_DATES,
        save_debug_html,
        execution_time_str,
        state,
        notification_logs,
        facilities_data,
        current_datetime_str,
        pushover_url,
    )
    browser.close()

  save_state(state)

  print("----------------------------------------")
  print(f"予約対象日: {TARGET_DATES}")
  print(f"環境判定: {ENV}")
  for fac in facilities_data:
    print(f"\n[{fac['name']}]")
    for room in fac["room_order"]:
      statuses_list = [
          fac["room_data"].get(room, {}).get(d, "-") for d in TARGET_DATES
      ]
      print(f"  {room}: {','.join(statuses_list)}")
  print("----------------------------------------")

  html_report = generate_html_report(
      TARGET_DATES, facilities_data, notification_logs, pushover_url
  )
  with open("index.html", "w", encoding="utf-8") as f:
    f.write(html_report)
  print("------> index.html を生成しました。")


def process_facilities(
    facilities,
    browser,
    target_dates,
    save_debug_html,
    execution_time_str,
    state,
    notification_logs,
    facilities_data,
    current_datetime_str,
    pushover_url,
):
  for fac_idx, facility in enumerate(facilities):
    fac_name = facility["name"]
    fac_type = facility.get("type", "dreserve")

    scraper = SCRAPER_MAP.get(fac_type)
    if not scraper:
      print(
          f"エラー: 未知の施設タイプです ({fac_type}) -> 施設名: {fac_name}"
      )
      continue

    # すべてのスクレイパーを共通の引数シグネチャで呼び出し
    scraped_result = scraper.scrape(
        browser=browser,
        facility=facility,
        target_dates=target_dates,
        save_debug_html=save_debug_html,
        execution_time_str=execution_time_str,
        fac_idx=fac_idx,
    )

    room_order = scraped_result["room_order"]
    room_data = scraped_result["room_data"]
    direct_url = scraped_result["direct_url"]

    date_availability = {}
    for d in target_dates:
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
      for d in target_dates:
        current_status = room_data.get(room, {}).get(d, "-")

        if should_notify(state[fac_name], room, d, current_status):
          msg = f"[{fac_name}] {room} の {d} が空いてる ({current_status})"
          notify_pushover(msg, pushover_url)
          notification_logs.append(
              f"[{current_datetime_str}] [{fac_name}] 対象日 {d} に空きあり"
              f" ({room}: 新規空室検知 ({current_status} に変化))"
          )

        state[fac_name][room][d] = current_status

    facilities_data.append({
        "name": fac_name,
        "direct_url": direct_url,
        "room_order": room_order,
        "room_data": room_data,
        "date_availability": date_availability,
    })


if __name__ == "__main__":
  main()