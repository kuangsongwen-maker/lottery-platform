"""六合彩数据补缺脚本 - 在本地运行（需 ClashX 代理）
用法: python3 sync_hk6.py --token YOUR_TOKEN [--periods 30]

--token: Railway上登录后从浏览器 localStorage 获取的 token
--periods: 最近N期（默认30）
--url: Railway 网址（默认 https://lottery-platform-production.up.railway.app）
"""
import argparse, json, os, sys
from datetime import datetime, timedelta

import requests
from playwright.sync_api import sync_playwright

HK6_GRAPHQL = "https://info.cld.hkjc.com/graphql/base/"
HK6_PROXY = {"server": "http://127.0.0.1:7890"}
HK6_QUERY = ("fragment lotteryDrawsFragment on LotteryDraw {\n"
             "  id\n  year\n  no\n  openDate\n  closeDate\n  drawDate\n"
             "  status\n  snowballCode\n  snowballName_en\n  snowballName_ch\n"
             "  lotteryPool {\n"
             "    sell\n    status\n    totalInvestment\n    jackpot\n"
             "    unitBet\n    estimatedPrize\n    derivedFirstPrizeDiv\n"
             "    lotteryPrizes {\n"
             "      type\n      winningUnit\n      dividend\n"
             "    }\n  }\n"
             "  drawResult {\n    drawnNo\n    xDrawnNo\n  }\n"
             "}\n"
             "query marksixResult($lastNDraw: Int, $startDate: String, "
             "$endDate: String, $drawType: LotteryDrawType) {\n"
             "  lotteryDraws(\n"
             "    lastNDraw: $lastNDraw\n"
             "    startDate: $startDate\n"
             "    endDate: $endDate\n"
             "    drawType: $drawType\n"
             "  ) {\n"
             "    ...lotteryDrawsFragment\n  }\n}")


def fetch_hk6_data(periods: int = 30) -> list[dict]:
    """通过 Playwright 抓取最近 N 期六合彩数据"""
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, proxy=HK6_PROXY)
        ctx = browser.new_context(user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"))
        page = ctx.new_page()
        page.goto("https://bet.hkjc.com/ch/marksix/checkdrawresult",
                  timeout=30000, wait_until="networkidle")
        page.wait_for_timeout(2000)

        # 分 60 天窗口查询，覆盖 periods 所需天数
        today = datetime.now()
        seen = set()
        for i in range(max(periods // 7 + 2, 1)):
            ed = today - timedelta(days=60 * i)
            sd = ed - timedelta(days=60)
            sd_str = sd.strftime("%Y%m%d")
            ed_str = ed.strftime("%Y%m%d")
            try:
                raw = page.evaluate(
                    "async (args) => {const [u,q,sd,ed]=args;"
                    "const r=await fetch(u,{method:'POST',"
                    "headers:{'Content-Type':'application/json'},"
                    "body:JSON.stringify({operationName:'marksixResult',"
                    "variables:{startDate:sd,endDate:ed,drawType:'All'},query:q})});"
                    "return JSON.stringify(await r.json());}",
                    [HK6_GRAPHQL, HK6_QUERY, sd_str, ed_str],
                )
                resp = json.loads(raw)
                for d in resp.get("data", {}).get("lotteryDraws", []):
                    did = d.get("id", "")
                    if did and did not in seen:
                        seen.add(did)
                        dr = d.get("drawResult", {})
                        drawn = dr.get("drawnNo", [])
                        xdrawn = dr.get("xDrawnNo")
                        if len(drawn) != 6 or xdrawn is None:
                            continue
                        year = d.get("year", "")
                        no = d.get("no", 0)
                        results.append({
                            "lottery_code": "hk6",
                            "draw_number": f"{year[-2:]}{int(no):03d}",
                            "draw_date": d.get("drawDate", "").split("+")[0],
                            "numbers": drawn,
                            "extra_numbers": [xdrawn],
                        })
            except Exception as e:
                print(f"  查询 {sd_str}-{ed_str} 失败: {e}")
        browser.close()
    # 按期号排序，取最近 periods 期
    results.sort(key=lambda x: x["draw_number"], reverse=True)
    return results[:periods]


def main():
    ap = argparse.ArgumentParser(description="六合彩数据补缺")
    ap.add_argument("--token", required=True, help="登录 token")
    ap.add_argument("--periods", type=int, default=30, help="最近 N 期")
    ap.add_argument("--url", default="https://lottery-platform-production.up.railway.app",
                    help="Railway 网址")
    args = ap.parse_args()

    print(f"正在抓取最近 {args.periods} 期六合彩数据...")
    records = fetch_hk6_data(args.periods)
    print(f"抓取到 {len(records)} 期")

    if not records:
        print("无数据，退出")
        return

    # 查哪些期数已有
    headers = {"Authorization": f"Bearer {args.token}", "Content-Type": "application/json"}
    r = requests.get(f"{args.url}/api/draws/hk6/latest?count={args.periods}", headers=headers)
    existing = set()
    if r.ok:
        for d in r.json():
            existing.add(d["draw_number"])
    print(f"已存在 {len(existing)} 期")

    # 只补缺的
    added = 0
    for rec in records:
        if rec["draw_number"] not in existing:
            r = requests.post(f"{args.url}/api/draws/manual",
                              headers=headers, json={
                                  "lottery": "hk6",
                                  "draw_number": rec["draw_number"],
                                  "draw_date": rec["draw_date"],
                                  "numbers": rec["numbers"],
                                  "extra_numbers": rec["extra_numbers"],
                              })
            if r.ok:
                print(f"  + {rec['draw_number']} ({rec['draw_date']})")
                added += 1
            else:
                print(f"  x {rec['draw_number']}: {r.text}")

    print(f"\n完成！新增 {added} 期")


if __name__ == "__main__":
    main()
