"""每日由 GitHub Actions 运行：抓取最新开奖数据并写回 data/latest.json。

本地也可手动执行做验证：
    python scripts/sync_data.py

设计：数据"生产"放在 CI（出网无限制），PythonAnywhere 端只从
raw.githubusercontent.com 拉取（在免费账户白名单内），绕开 500.com / pilio
被代理 403 的限制。详见 main.py 的 ensure_synced()。
"""
import sys, os, json, datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from crawler import LotteryCrawler

OUT_DIR = os.path.join(_ROOT, "data")
OUT_FILE = os.path.join(OUT_DIR, "latest.json")


def _clean(r: dict) -> dict:
    """统一成与 DrawRecord 兼容的字段（numbers/extra_numbers 已是 JSON 字符串）。"""
    return {
        "draw_number": r.get("draw_number", ""),
        "draw_date": r["draw_date"],
        "numbers": r["numbers"],
        "extra_numbers": r["extra_numbers"],
        "prize_pool": r.get("prize_pool", "0"),
        "sales": r.get("sales", "0"),
    }


NEW_FC = ["kl8", "3d", "qlc"]      # 福彩：官网全历史
NEW_SP = ["pls", "plw", "qxc"]     # 体彩：官网仅最新一期，增量合并累积


def _load_previous() -> dict:
    """读取上一次生成的 latest.json，用于体彩历史增量合并。"""
    if os.path.exists(OUT_FILE):
        try:
            with open(OUT_FILE, encoding="utf-8") as f:
                return json.load(f).get("draws", {})
        except Exception:
            return {}
    return {}


def _merge_by_key(prev_list, new_list):
    """按 (draw_number 或 draw_date) 去重合并，新数据覆盖旧数据。"""
    seen = {}
    for r in (prev_list or []):
        key = r.get("draw_number") or r.get("draw_date")
        seen[key] = r
    for r in (new_list or []):
        key = r.get("draw_number") or r.get("draw_date")
        seen[key] = r
    return list(seen.values())


def build_payload() -> dict:
    c = LotteryCrawler()
    draws = {}

    # 双色球 / 大乐透：数据源自带期号，直接采用
    draws["ssq"] = [_clean(r) for r in c.fetch_all_ssq()]
    draws["dlt"] = [_clean(r) for r in c.fetch_all_dlt()]

    # 六合彩：pilio 无期号，按日期升序排列，期号留空由 PA 端续编
    hk = c.fetch_hk6_pilio(pages=10)
    hk = sorted(hk, key=lambda r: r["draw_date"])
    draws["hk6"] = [
        {
            "draw_number": "",
            "draw_date": r["draw_date"],
            "numbers": r["numbers"],
            "extra_numbers": r["extra_numbers"],
            "prize_pool": "0",
            "sales": "0",
        }
        for r in hk
    ]

    # 福彩3个：官网全历史
    prev = _load_previous()
    for code in NEW_FC:
        raw = c.fetch_fc_all(code)
        draws[code] = [_clean(r) for r in raw]

    # 体彩3个：官网仅最新一期，与旧数据合并以累积历史
    for code in NEW_SP:
        raw = c.fetch_sporttery_latest(code)
        merged = _merge_by_key(prev.get(code, []), [_clean(r) for r in raw])
        merged.sort(key=lambda r: (r.get("draw_number") or r.get("draw_date") or ""))
        draws[code] = merged

    return draws


def main():
    draws = build_payload()
    os.makedirs(OUT_DIR, exist_ok=True)
    payload = {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "draws": draws,
    }
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    total = sum(len(v) for v in draws.values())
    codes = " ".join(f"{c}={len(draws[c])}" for c in sorted(draws))
    print(f"[sync] 已生成 {OUT_FILE}：共 {total} 条 ({codes})")


if __name__ == "__main__":
    main()
