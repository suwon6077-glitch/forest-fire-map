"""기상청 초단기실황을 시군구별로 받아 weather.json을 만든다.

실행: KMA_KEY=<인증키> python fetch_weather.py
- grid.json     : 시군구 이름 -> 기상청 격자 [nx, ny]
- history.json  : 시군구별 시간당 강수량 기록 (최근 72시간, 자동 생성)
- weather.json  : 웹 페이지가 읽는 결과 (자동 생성)
"""
import json, os, sys, time
import urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

URL = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst"
KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
KEEP_HOURS = 72      # 강수량을 누적하는 기간
MAX_CATCHUP = 6      # 한 번 실행할 때 거슬러 받아오는 최대 시간 수


def call_api(key, base, nx, ny):
    """한 격자, 한 시각의 실황을 {항목코드: 값} 으로 돌려준다. 자료가 없으면 None."""
    q = urllib.parse.urlencode({
        "serviceKey": key, "pageNo": 1, "numOfRows": 100, "dataType": "JSON",
        "base_date": base.strftime("%Y%m%d"), "base_time": base.strftime("%H00"),
        "nx": nx, "ny": ny})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(URL + "?" + q, timeout=20) as r:
                body = json.loads(r.read().decode("utf-8"))
            head = body["response"]["header"]
            if head["resultCode"] == "03":          # NO_DATA: 아직 발표 전
                return None
            if head["resultCode"] != "00":
                raise RuntimeError(head["resultCode"] + " " + head["resultMsg"])
            return {i["category"]: i["obsrValue"] for i in body["response"]["body"]["items"]["item"]}
        except Exception as e:                      # 일시 오류는 잠깐 쉬고 다시 시도
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("API 호출 실패 nx=%s ny=%s: %s" % (nx, ny, last))


def num(v):
    """문자열 값을 숫자로. 결측(-900 이하)이나 글자는 None."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x <= -900 else x


def hours_to_fetch(now, last_base):
    """받아야 할 발표 시각 목록(오래된 것부터). 실황은 정시 10분쯤 뒤에 나온다."""
    newest = (now - timedelta(minutes=15)).replace(minute=0, second=0, microsecond=0)
    if last_base is None:
        start = newest - timedelta(hours=MAX_CATCHUP - 1)
    else:
        start = max(last_base + timedelta(hours=1), newest - timedelta(hours=MAX_CATCHUP - 1))
    out, t = [], start
    while t <= newest:
        out.append(t)
        t += timedelta(hours=1)
    return out


def main(fetch=call_api, now=None):
    key = os.environ.get("KMA_KEY", "")
    if not key and fetch is call_api:
        sys.exit("환경변수 KMA_KEY 에 인증키를 넣어 주세요.")
    grid = json.load(open(os.path.join(HERE, "grid.json"), encoding="utf-8"))
    hpath = os.path.join(HERE, "history.json")
    hist = json.load(open(hpath, encoding="utf-8")) if os.path.exists(hpath) else {"last": None, "rain": {}}
    now = now or datetime.now(KST)
    last = datetime.strptime(hist["last"], "%Y-%m-%d %H:%M").replace(tzinfo=KST) if hist["last"] else None

    cells = sorted({tuple(v) for v in grid.values()})   # 같은 격자는 한 번만 호출
    latest, done = {}, None
    for base in hours_to_fetch(now, last):
        with ThreadPoolExecutor(8) as ex:
            res = list(ex.map(lambda c: fetch(key, base, c[0], c[1]), cells))
        if sum(r is not None for r in res) < len(cells) * 0.8:
            print(base.strftime("%Y-%m-%d %H:%M"), "자료 없음, 건너뜀")
            continue
        stamp = base.strftime("%Y-%m-%d %H:%M")
        for cell, r in zip(cells, res):
            ck = "%d,%d" % cell
            if r is None:
                continue
            latest[ck] = r
            hist["rain"].setdefault(ck, {})[stamp] = num(r.get("RN1")) or 0.0
        done = base
        print(stamp, "수집 완료", len(cells), "격자")

    if done is None:
        print("새로 받은 자료가 없습니다. weather.json 은 그대로 둡니다.")
        return
    cutoff = (done - timedelta(hours=KEEP_HOURS - 1)).strftime("%Y-%m-%d %H:%M")
    for ck in hist["rain"]:
        hist["rain"][ck] = {k: v for k, v in hist["rain"][ck].items() if k >= cutoff}
    hist["last"] = done.strftime("%Y-%m-%d %H:%M")

    regions, hours = {}, 0
    for name, (nx, ny) in grid.items():
        ck = "%d,%d" % (nx, ny)
        r = latest.get(ck)
        if not r:
            continue
        hum, wind, temp = num(r.get("REH")), num(r.get("WSD")), num(r.get("T1H"))
        if hum is None or wind is None:
            continue
        rain = hist["rain"].get(ck, {})
        hours = max(hours, len(rain))
        regions[name] = {"hum": round(hum), "wind": round(wind, 1),
                         "rain72": round(sum(rain.values()), 1), "temp": temp}
    out = {"base": hist["last"], "rainHours": hours, "regions": regions}
    json.dump(out, open(os.path.join(HERE, "weather.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    json.dump(hist, open(hpath, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("weather.json 저장:", len(regions), "개 지역, 기준", out["base"], ", 강수 누적", hours, "시간")


if __name__ == "__main__":
    main()
