import os
import urllib.parse
from datetime import datetime


def scrape(
    browser, facility, target_dates, save_debug_html, execution_time_str, fac_idx
):
  fac_name = facility["name"]
  base_url = facility["url"]
  exclude_keywords = facility.get("exclude_keywords", [])

  # 確実に pcpl.asp を叩くようにベースURLを調整
  if "?" in base_url:
    base_url = base_url.split("?")[0]
  if not base_url.endswith("pcpl.asp"):
    base_url = base_url.rsplit("/", 1)[0] + "/pcpl.asp"

  room_data = {}
  room_name = "個室・1泊2食"
  room_order = [room_name]
  room_data[room_name] = {}

  # ご提示いただいた正しい個別日取得用のパラメータ
  base_params = {"type": "11", "m": "4", "st": "", "rm": "1", "ml": "1"}

  print(f"対象施設: {fac_name} (tenawan)")

  current_year = datetime.now().year
  first_url = ""

  for idx, date_str in enumerate(target_dates):
    dt = datetime.strptime(f"{current_year}/{date_str}", "%Y/%m/%d")
    ym = dt.strftime("%y%m")
    d = str(dt.day)

    params = base_params.copy()
    params["ym"] = ym
    params["d"] = d

    query_string = urllib.parse.urlencode(params, safe="")
    current_url = f"{base_url}?{query_string}"

    if idx == 0:
      first_url = current_url

    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(current_url, wait_until="domcontentloaded")

    # デバッグHTMLの保存
    debug_dir = "debug_html"
    os.makedirs(debug_dir, exist_ok=True)
    debug_filename = os.path.join(
        debug_dir, f"{execution_time_str}_fac{fac_idx}_date{ym}{d}.html"
    )
    with open(debug_filename, "w", encoding="utf-8") as f:
      f.write(page.content())
    if not save_debug_html and os.path.exists(debug_filename):
      os.remove(debug_filename)

    try:
      body_text = page.locator("body").inner_text()

      # 満室メッセージの有無をチェック
      is_fully_booked = "予約可能なプランをご用意しておりません" in body_text

      found = False
      if not is_fully_booked:
        reserve_link = page.locator('a[href*="f2.asp"]')
        reserve_img = page.locator('img[alt="ご予約はこちら"]')

        if reserve_link.count() > 0 or reserve_img.count() > 0:
          if not any(kw in body_text for kw in exclude_keywords):
            found = True

      room_data[room_name][date_str] = "〇" if found else "×"

    except Exception as e:
      print(f"[tenawan] Error processing date {date_str}: {e}")
      room_data[room_name][date_str] = "×"

    page.close()

  print(f"--> 取得完了: {fac_name}\n")

  return {
      "name": fac_name,
      "direct_url": first_url,
      "room_order": room_order,
      "room_data": room_data,
  }