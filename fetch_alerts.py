"""행정안전부 긴급재난문자에서 산불 관련 문자를 골라 alerts.json을 만든다.

실행: SAFETY_KEY=<인증키> python fetch_alerts.py
- grid.json   : 지도에 있는 시군구 이름 목록으로 사용
- alerts.json : 웹 페이지가 읽는 결과 (자동 생성, 최근 48시간)
"""
import json, os, re, sys, time
import urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

URL = "https://www.safetydata.go.kr/V2/api/DSSP-IF-00247"
KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
KEEP_HOURS = 48

SIDO = {"서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천",
        "광주광역시": "광주", "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종",
        "경기도": "경기", "강원특별자치도": "강원", "강원도": "강원", "충청북도": "충북", "충청남도": "충남",
        "전북특별자치도": "전북", "전라북도": "전북", "전라남도": "전남", "경상북도": "경북",
        "경상남도": "경남", "제주특별자치도": "제주", "제주도": "제주"}


def region_keys(text, known):
    """'경상남도 창원시 의창구, 경상북도 전체' 같은 수신지역을 지도 지역 이름 목록으로 바꾼다."""
    out = []
    for part in re.split(r"[,\n]", text or ""):
        tok = part.split()
        if not tok or tok[0] not in SIDO:
            continue
        sido = SIDO[tok[0]]
        if sido == "세종":
            cand = ["세종 세종"]
        elif len(tok) < 2 or tok[1] == "전체":
            cand = [k for k in known if k.startswith(sido + " ")]
        else:
            name = re.sub(r"(시|군|구)$", "", tok[1]) if len(tok[1]) > 1 else tok[1]
            cand = [sido + " " + name]
            if sido == "경북" and name == "군위":
                cand = ["대구 군위"]
        for k in cand:
            if k in known and k not in out:
                out.append(k)
    return out


def classify(kind_name, msg):
    """산불 관련이면 'fire'(발생·확산·대피 문자) 또는 'notice'(예방·주의 안내), 아니면 None."""
    if kind_name != "산불" and not (kind_name == "화재" and "산불" in msg):
        return None
    m = re.sub(r"발생\s*(위험|우려|가능|시[\s,]|하지|할\s|예방|하면|되지)", "", msg)
    if re.search(r"산불.{0,15}발생|산불.{0,10}확산|대피|진화\s*(중|완료)|주불", m):
        return "fire"
    return "notice"


def call_api(key, since):
    rows, page = [], 1
    while page <= 5:
        q = urllib.parse.urlencode({"serviceKey": key, "returnType": "json", "pageNo": page,
                                    "numOfRows": 1000, "crtDt": since.strftime("%Y%m%d")})
        last = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(URL + "?" + q, timeout=30) as r:
                    body = json.loads(r.read().decode("utf-8"))
                break
            except Exception as e:
                last = e
                time.sleep(3 * (attempt + 1))
        else:
            raise RuntimeError("재난문자 API 호출 실패: %s" % last)
        if body["header"]["resultCode"] != "00":
            raise RuntimeError("재난문자 API 오류: %s %s" % (body["header"]["resultCode"], body["header"].get("resultMsg")))
        got = body.get("body") or []
        rows += got
        if len(rows) >= int(body.get("totalCount") or 0) or not got:
            break
        page += 1
    return rows


def main(fetch=call_api, now=None):
    key = os.environ.get("SAFETY_KEY", "")
    if not key and fetch is call_api:
        sys.exit("환경변수 SAFETY_KEY 에 인증키를 넣어 주세요.")
    known = list(json.load(open(os.path.join(HERE, "grid.json"), encoding="utf-8")).keys())
    now = now or datetime.now(KST)
    cutoff = now - timedelta(hours=KEEP_HOURS)
    items = []
    for r in fetch(key, cutoff):
        msg = (r.get("MSG_CN") or "").replace("\r", " ").replace("\n", " ").strip()
        kind = classify(r.get("DST_SE_NM") or "", msg)
        if not kind:
            continue
        try:
            t = datetime.strptime(r["CRT_DT"][:19], "%Y/%m/%d %H:%M:%S").replace(tzinfo=KST)
        except Exception:
            continue
        if t < cutoff:
            continue
        region = " ".join((r.get("RCPTN_RGN_NM") or "").split())
        items.append({"sn": r.get("SN"), "t": t.strftime("%Y-%m-%d %H:%M"), "region": region,
                      "keys": region_keys(r.get("RCPTN_RGN_NM"), known), "kind": kind,
                      "step": r.get("EMRG_STEP_NM") or "", "msg": msg})
    items.sort(key=lambda x: x["t"], reverse=True)

    path = os.path.join(HERE, "alerts.json")
    old = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None
    stamp = now.strftime("%Y-%m-%d %H:%M")
    if old and old.get("items") == items:
        age = now - datetime.strptime(old["checked"], "%Y-%m-%d %H:%M").replace(tzinfo=KST)
        if age < timedelta(minutes=55):
            print("변화 없음. alerts.json 은 그대로 둡니다.")
            return
    json.dump({"checked": stamp, "hours": KEEP_HOURS, "items": items},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("alerts.json 저장: 산불 발생 문자", sum(i["kind"] == "fire" for i in items),
          "건, 예방 안내", sum(i["kind"] == "notice" for i in items), "건")


if __name__ == "__main__":
    main()
