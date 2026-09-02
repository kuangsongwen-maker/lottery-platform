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
    print(f"[sync] 已生成 {OUT_FILE}：共 {total} 条 "
          f"(ssq={len(draws['ssq'])} dlt={len(draws['dlt'])} hk6={len(draws['hk6'])})")


if __name__ == "__main__":
    main()
