"""彩票数据爬虫 - 双色球 & 大乐透
数据源：500.com（稳定可靠，表格解析）
"""
import json
import requests
from bs4 import BeautifulSoup


class LotteryCrawler:
    """爬取 500.com 开奖历史数据"""

    BASE = "https://datachart.500.com"
    HEADERS = {
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"),
    }

    def __init__(self):
        self.sess = requests.Session()
        self.sess.headers.update(self.HEADERS)

    # ---------- 通用 ----------

    def _fetch_html(self, url: str, params: dict = None) -> str | None:
        try:
            resp = self.sess.get(url, params=params, timeout=20)
            resp.encoding = "utf-8"
            return resp.text
        except Exception as e:
            print(f"[爬虫] 请求失败 {url}: {e}")
            return None

    @staticmethod
    def _td_texts(html: str, table_id: str = "tablelist") -> list[list[str]]:
        """解析 HTML 表格，返回每行的文本列表"""
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table", id=table_id)
        if not table:
            return []
        rows = table.find_all("tr")
        result = []
        for row in rows:
            cells = row.find_all("td")
            texts = [c.get_text(strip=True) for c in cells]
            if texts:
                result.append(texts)
        return result

    # ---------- 双色球 ----------

    @staticmethod
    def _ssq_draw_range(num_periods: int) -> dict:
        """通过日期反推算近 N 期的期号范围，自动跨年"""
        import datetime
        now = datetime.date.today()
        # 估算当前期号：一年约 153 期
        doy = now.timetuple().tm_yday
        cur_issue = min(int((doy / 365) * 153), 153)
        end_code = (now.year % 100) * 1000 + min(cur_issue + 10, 153)
        # 往前推 N 期所需的天数
        span_days = int(num_periods * (365 / 153)) + 30
        start_date = now - datetime.timedelta(days=span_days)
        start_doy = start_date.timetuple().tm_yday
        start_issue = min(int((start_doy / 365) * 153), 153)
        start_code = (start_date.year % 100) * 1000 + max(1, start_issue - 5)
        return {"start": start_code, "end": end_code}

    def fetch_ssq(self, num_periods: int = 50) -> list[dict]:
        """抓取双色球数据"""
        r = self._ssq_draw_range(max(num_periods, 50))
        url = f"{self.BASE}/ssq/history/newinc/history.php"
        html = self._fetch_html(url, {"start": r["start"], "end": r["end"]})
        if not html:
            return []
        rows = self._td_texts(html)
        # 匹配：期号, 红1-6, 蓝球, _, 奖池, 一等注, 一等金, 二等注, 二等金, 销量, 日期
        results = []
        for row in rows:
            if len(row) < 16:
                continue
            draw_number = row[0]
            if not draw_number.isdigit():
                continue
            reds = [int(row[i]) for i in range(1, 7) if row[i].isdigit()]
            if len(reds) != 6:
                continue
            blue = [int(row[7])] if row[7].isdigit() else []
            if not blue:
                continue
            results.append({
                "lottery_code": "ssq",
                "draw_number": draw_number,
                "draw_date": row[15],
                "numbers": json.dumps(reds),
                "extra_numbers": json.dumps(blue),
                "prize_pool": row[9].replace(",", ""),
                "sales": row[14].replace(",", ""),
            })
        return results

    def fetch_all_ssq(self, max_pages=99) -> list[dict]:
        """抓取近 200 期双色球"""
        return self.fetch_ssq(num_periods=200)

    # ---------- 大乐透 ----------

    def fetch_dlt(self, num_periods: int = 50) -> list[dict]:
        """抓取大乐透数据"""
        # 大乐透每年约 153 期，推算逻辑同双色球
        r = self._ssq_draw_range(max(num_periods, 50))  # 复用估算逻辑
        url = f"{self.BASE}/dlt/history/newinc/history.php"
        html = self._fetch_html(url, {"start": r["start"], "end": r["end"]})
        if not html:
            return []
        rows = self._td_texts(html)
        results = []
        for row in rows:
            if len(row) < 15:
                continue
            draw_number = row[0]
            if not draw_number.isdigit():
                continue
            front = [int(row[i]) for i in range(1, 6) if row[i].isdigit()]
            back = [int(row[i]) for i in range(6, 8) if row[i].isdigit()]
            if len(front) != 5 or len(back) != 2:
                continue
            results.append({
                "lottery_code": "dlt",
                "draw_number": draw_number,
                "draw_date": row[14],
                "numbers": json.dumps(front),
                "extra_numbers": json.dumps(back),
                "prize_pool": row[8].replace(",", ""),
                "sales": row[13].replace(",", ""),
            })
        return results

    def fetch_all_dlt(self, max_pages=99) -> list[dict]:
        """抓取近 200 期大乐透"""
        return self.fetch_dlt(num_periods=200)

    # ---------- 香港六合彩 ----------

    # HKJC GraphQL 有 Cloudflare TLS 指纹保护，必须通过 Playwright 浏览器调用
    HK6_GRAPHQL = "https://info.cld.hkjc.com/graphql/base/"
    HK6_PROXY = {"server": "http://127.0.0.1:7890"}

    # HKJC 页面使用的标准 GraphQL query（必须包含完整字段，精简版会被服务器拒绝）
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

    @staticmethod
    def _hk6_to_record(r: dict) -> dict | None:
        """将 HKJC GraphQL 原始记录转为统一格式"""
        dr = r.get("drawResult", {})
        drawn = dr.get("drawnNo", [])
        xdrawn = dr.get("xDrawnNo")
        if len(drawn) != 6 or xdrawn is None:
            return None
        year = r.get("year", "")
        no = r.get("no", 0)
        pool = r.get("lotteryPool", {})
        return {
            "lottery_code": "hk6",
            "draw_number": f"{year[-2:]}{int(no):03d}",
            "draw_date": r.get("drawDate", "").split("+")[0],
            "numbers": json.dumps(drawn),
            "extra_numbers": json.dumps([xdrawn]),
            "prize_pool": str(pool.get("jackpot", "0")),
            "sales": str(pool.get("totalInvestment", "0")),
        }

    def _hk6_playwright_fetch(self, date_ranges: list[tuple[str, str]]) -> list[dict]:
        """单次 Playwright 会话，批量查询多个日期范围的六合彩数据

        直接通过 page.evaluate 在浏览器内执行 fetch 并返回结果，
        避免 on_response 回调的异常问题。
        """
        from playwright.sync_api import sync_playwright

        seen_ids = set()
        results = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, proxy=self.HK6_PROXY)
            context = browser.new_context(
                user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"),
            )
            page = context.new_page()

            # 访问页面建立会话（TLS 握手、cookie）
            page.goto(
                "https://bet.hkjc.com/ch/marksix/checkdrawresult",
                timeout=30000, wait_until="networkidle",
            )
            page.wait_for_timeout(2000)

            for sd, ed in date_ranges:
                try:
                    raw = page.evaluate(
                        "async (args) => {"
                        "const [url, q, sd, ed] = args;"
                        "const r = await fetch(url, {"
                        "method: 'POST',"
                        "headers: {'Content-Type': 'application/json'},"
                        "body: JSON.stringify({"
                        "operationName: 'marksixResult',"
                        "variables: {startDate: sd, endDate: ed, drawType: 'All'},"
                        "query: q"
                        "})});"
                        "const d = await r.json();"
                        "return JSON.stringify(d);"
                        "}",
                        [self.HK6_GRAPHQL, self.HK6_QUERY, sd, ed],
                    )
                    resp = json.loads(raw)
                    for d in resp.get("data", {}).get("lotteryDraws", []):
                        did = d.get("id", "")
                        if did and did not in seen_ids:
                            seen_ids.add(did)
                            rec = self._hk6_to_record(d)
                            if rec:
                                results.append(rec)
                except Exception as e:
                    print(f"[爬虫] HK6 查询 {sd}-{ed} 异常: {e}")

            browser.close()

        return results

    def fetch_hk6(self, start_date: str = None, end_date: str = None,
                  num_months: int = 6) -> list[dict]:
        """抓取香港六合彩数据"""
        import datetime
        if not end_date:
            end_date = datetime.date.today().strftime("%Y%m%d")
        if not start_date:
            start_date = (datetime.date.today() -
                         datetime.timedelta(days=num_months * 30)).strftime("%Y%m%d")
        return self._hk6_playwright_fetch([(start_date, end_date)])

    def fetch_all_hk6(self, max_pages=10) -> list[dict]:
        """分页抓取全部六合彩数据（60天/页，HKJC API限制查询范围~85天）"""
        import datetime
        today = datetime.date.today()
        ranges = []
        day_span = 60
        for i in range(max_pages + 1):
            ed = today - datetime.timedelta(days=day_span * i)
            sd = ed - datetime.timedelta(days=day_span)
            ranges.append((sd.strftime("%Y%m%d"), ed.strftime("%Y%m%d")))
        return self._hk6_playwright_fetch(ranges)
