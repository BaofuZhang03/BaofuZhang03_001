"""
测试：submit_enc token 的有效期（TTL）

实验设计：
  1. 在 TARGET_TIME（固定北京时钟时间）前 PRE_FETCH_MS 毫秒获取 1 个 shared token
  2. 各 Shot 在 target_dt + 各自 offset_ms 时刻并行 POST，共用同一 token
  3. Shot 1 故意用错误座位号消耗一次 token（触发"非法预约"但不应让 token 失效）
  4. 后续 Shot 用正确参数，通过返回码判断 token 是否还有效

结果判读：
  Shot N 返回 '代码:303' → token 已失效（TTL 已过 或 一次性使用）
  Shot N 返回其他 msg   → token 仍有效

调整 PRE_FETCH_MS 来探测 TTL 边界，例如：
  PRE_FETCH_MS = 60000  → 提前 60 秒取，看 60 秒后是否 303
  PRE_FETCH_MS = 5000   → 提前 5 秒取，看 5 秒后是否 303
  PRE_FETCH_MS = 1500   → 提前 1.5 秒取（正常抢座场景）
"""

import datetime
import time
import threading
import logging
from zoneinfo import ZoneInfo

from utils import reserve
from utils.encrypt import verify_param

# =====================================================================
# 参数配置（全部填在这里，不需要改其他文件）
# =====================================================================

USERNAME      = ""                    # 超星账号
PASSWORD      = "."

ROOM_ID       = "11386"               # 房间 roomId
SEAT_PAGE_ID  = "11386"               # URL 中 seatId= 的值（seatPageId）
FID_ENC       = "4a18e12602b24c8c"    # fidEnc

START_TIME    = "18:00"
END_TIME      = "22:00"

CORRECT_SEAT  = "060"                 # 正确座位号
WRONG_SEAT    = "060"                 # 故意写错（Shot 1 用，消耗一次 token）

RESERVE_NEXT_DAY = True               # True = 预约明天

# ── 目标时间（北京时间 HH:MM:SS）──────────────────────────────────────
# 脚本会等到这个时刻再开始计时，留出足够时间完成登录
TARGET_TIME = "19:05:00"             # 改成你想测试的时刻

# ── token 提前量（ms）────────────────────────────────────────────────
# 在 target_dt - PRE_FETCH_MS 时刻获取 token，通过改这个值探测 TTL 边界
PRE_FETCH_MS = 6000                   # 例：60000 = 提前 60 秒

# ── 各 Shot 相对 target_dt 的触发偏移（ms）──────────────────────────
# 格式：(offset_ms, seat_num, label)
# Shot 1 故意用 WRONG_SEAT，后续用 CORRECT_SEAT
SHOTS = [
    (200,  CORRECT_SEAT, "CORRECT"),   # Shot 1: T+200ms
    (800,  CORRECT_SEAT, "CORRECT"),   # Shot 2: T+800ms
    (1000, CORRECT_SEAT, "CORRECT"),   # Shot 3: T+1000ms
]

# =====================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)


def beijing_now() -> datetime.datetime:
    return datetime.datetime.now(ZoneInfo("Asia/Shanghai"))


def post_submit(session, submit_url, room_id, seat_num, start_time, end_time,
                reserve_next_day, token_value) -> dict:
    """
    直接 POST 并返回完整 JSON，方便查看原始 msg。
    不调用原始 get_submit，以免其特殊处理逻辑干扰实验观察。
    """
    beijing_today = (
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
    ).date()
    delta_day = 1 if reserve_next_day else 0
    day = str(beijing_today + datetime.timedelta(days=delta_day))

    parm = {
        "roomId":    room_id,
        "startTime": start_time,
        "endTime":   end_time,
        "day":       day,
        "seatNum":   seat_num,
        "captcha":   "",
        "wyToken":   "",
    }
    parm["enc"] = verify_param(parm, token_value)
    resp = session.post(url=submit_url, data=parm, verify=False).content.decode("utf-8")
    import json
    return json.loads(resp)


def main():
    if not USERNAME or not PASSWORD:
        print("请先在文件顶部填写 USERNAME / PASSWORD")
        return

    # 解析 TARGET_TIME 为当天北京时间
    h, m, s = map(int, TARGET_TIME.split(":"))
    today = beijing_now().date()
    target_dt = datetime.datetime(
        today.year, today.month, today.day, h, m, s,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )
    if beijing_now() >= target_dt:
        print(f"TARGET_TIME {TARGET_TIME} 已过，请改为未来时刻")
        return

    prefetch_dt = target_dt - datetime.timedelta(milliseconds=PRE_FETCH_MS)
    login_deadline = prefetch_dt - datetime.timedelta(seconds=5)  # 必须在此之前完成登录

    logging.info(f"Target time     : {target_dt.strftime('%H:%M:%S')}")
    logging.info(f"Pre-fetch at    : {prefetch_dt.strftime('%H:%M:%S.%f')[:-3]}  (T - {PRE_FETCH_MS}ms)")
    logging.info(f"Login deadline  : {login_deadline.strftime('%H:%M:%S')}")
    for i, (offset, seat, label) in enumerate(SHOTS):
        fire = (target_dt + datetime.timedelta(milliseconds=offset)).strftime('%H:%M:%S.%f')[:-3]
        logging.info(f"Shot {i+1}          : {fire}  seat={seat}({label})  (T + {offset}ms)")

    if beijing_now() > login_deadline:
        print(f"\u26a0  距预取时间不足 5 秒，可能来不及登录，请将 TARGET_TIME 改为更晚的时间")
        return

    # ── 登录 ──
    s = reserve(sleep_time=0.1, max_attempt=1, reserve_next_day=RESERVE_NEXT_DAY)
    s.get_login_status()
    ok, _ = s.login(USERNAME, PASSWORD)
    if not ok:
        logging.error("Login failed, abort.")
        return
    s.requests.headers.update({"Host": "office.chaoxing.com"})

    # ── 构造 token URL（注意用预约日期，不是今天）──
    beijing_today = (
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
    ).date()
    reservation_day = str(
        beijing_today + datetime.timedelta(days=1 if RESERVE_NEXT_DAY else 0)
    )
    token_url = s.url.format(
        roomId=ROOM_ID,
        day=reservation_day,
        seatPageId=SEAT_PAGE_ID,
        fidEnc=FID_ENC,
    )

    # ── 等到 target_dt - PRE_FETCH_MS 后预取 token ──
    prefetch_dt = target_dt - datetime.timedelta(milliseconds=PRE_FETCH_MS)
    while beijing_now() < prefetch_dt:
        time.sleep(0.05)

    logging.info(f"[prefetch] Fetching shared token at {beijing_now().strftime('%H:%M:%S.%f')[:-3]}")
    token, value = s._get_page_token(token_url, require_value=True)
    if not token:
        logging.error("[prefetch] Failed to get token, abort.")
        return
    logging.info(f"[prefetch] token = {token}")

    # ── 并行发射，共用同一 token ──
    n = len(SHOTS)
    responses: list[dict | None] = [None] * n

    def shoot(index: int):
        offset_ms, seat, label = SHOTS[index]
        fire_dt = target_dt + datetime.timedelta(milliseconds=offset_ms)
        while beijing_now() < fire_dt:
            time.sleep(0.001)

        fired_at = beijing_now().strftime("%H:%M:%S.%f")[:-3]
        logging.info(f"[shot-{index+1}] T+{offset_ms}ms  firing at {fired_at}  seat={seat}({label})")
        data = post_submit(
            session          = s.requests,
            submit_url       = s.submit_url,
            room_id          = ROOM_ID,
            seat_num         = seat,
            start_time       = START_TIME,
            end_time         = END_TIME,
            reserve_next_day = RESERVE_NEXT_DAY,
            token_value      = value,
        )
        responses[index] = data
        msg = data.get("msg", "")
        tag = "✓" if data.get("success") else ("⚠  303" if "303" in str(msg) else "✗")
        logging.info(f"[shot-{index+1}] {tag}  {data}")

    threads = [
        threading.Thread(target=shoot, args=(i,), daemon=True)
        for i in range(n)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    # ── 结果汇总 ──
    print("\n" + "=" * 60)
    print("RESULT SUMMARY")
    print(f"  token 获取时间: T - {PRE_FETCH_MS}ms")
    print("=" * 60)
    for i, (offset_ms, seat, label) in enumerate(SHOTS):
        resp = responses[i]
        msg  = str(resp.get("msg", "")) if resp else "NO RESPONSE"
        is303 = "303" in msg
        ok    = resp.get("success") if resp else False
        tag   = "✓ success" if ok else ("⚠  303 (token失效)" if is303 else f"✗ {msg[:40]}")
        print(f"  Shot {i+1}  T+{offset_ms:>5}ms  seat={seat}({label:<8})  {tag}")
    print("=" * 60)


if __name__ == "__main__":
    main()
