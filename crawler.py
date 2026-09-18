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
    # 数据源：pil.io.idv.tw（纯 HTML，无需代理）

    def fetch_all_hk6(self, max_pages=10) -> list[dict]:
        """分页抓取全部六合彩数据（使用 pilio.idv.tw，无需代理）"""
        records = self.fetch_hk6_pilio(pages=max_pages)
        if not records:
            return records
        # 按期号排序倒序分配期号：最旧→最新编排，再翻回最新→最旧
        records.reverse()
        from collections import defaultdict
        year_counts = defaultdict(int)
        for r in records:
            year = r["draw_date"][:4]
            year_counts[year] += 1
            r["draw_number"] = f"{year[2:]}{year_counts[year]:03d}"
        records.reverse()
        return records

    # ---------- 福彩（官网 cwl.gov.cn）----------
    _FC_API = "https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice"
    _FC_NAME = {"kl8": "kl8", "3d": "3d", "qlc": "qlc"}

    def fetch_fc_all(self, code: str, page_size: int = 200) -> list[dict]:
        """从福彩官网拉取全部历史（分页）。code in kl8/3d/qlc"""
        name = self._FC_NAME[code]
        results = []
        page_no = 1
        while True:
            params = {"name": name, "systemType": "PC",
                      "pageSize": page_size, "pageNo": page_no}
            data = None
            for attempt in range(2):
                try:
                    resp = self.sess.get(self._FC_API, params=params,
                                         headers={**self.HEADERS,
                                                  "Referer": "https://www.cwl.gov.cn/"},
                                         timeout=20)
                    data = resp.json()
                    break
                except Exception as e:
                    print(f"[爬虫] 福彩 {code} 第{page_no}页第{attempt+1}次失败: {e}")
                    import time
                    time.sleep(1)
            if data is None:
                break
            if data.get("state") != 0:
                print(f"[爬虫] 福彩 {code} 接口异常: {data.get('message')}")
                break
            items = data.get("result") or []
            if not items:
                break
            for it in items:
                rec = self._parse_fc_item(code, it)
                if rec:
                    results.append(rec)
            total = data.get("total", 0)
            if page_no * page_size >= total:
                break
            page_no += 1
            import time
            time.sleep(0.15)
        results.sort(key=lambda r: r["draw_date"])
        return results

    @staticmethod
    def _parse_fc_item(code: str, it: dict) -> dict | None:
        import re
        draw_number = str(it.get("code", "")).strip()
        date_raw = it.get("date", "") or ""
        m = re.match(r"(\d{4}-\d{2}-\d{2})", date_raw)
        draw_date = m.group(1) if m else ""
        red = (it.get("red") or "").strip()
        blue = (it.get("blue") or "").strip()
        if not draw_date or not red:
            return None
        try:
            numbers = [int(x) for x in red.split(",") if x.strip()]
        except ValueError:
            return None
        extra = []
        if blue:
            try:
                extra = [int(x) for x in blue.split(",") if x.strip()]
            except ValueError:
                extra = []
        return {
            "lottery_code": code,
            "draw_number": draw_number,
            "draw_date": draw_date,
            "numbers": json.dumps(numbers),
            "extra_numbers": json.dumps(extra),
            "prize_pool": str(it.get("poolmoney") or "0").replace(",", ""),
            "sales": str(it.get("sales") or "0").replace(",", ""),
        }

    # ---------- 体彩（官网 sporttery.cn）----------
    _SP_API = "https://webapi.sporttery.cn/gateway/lottery/getDigitalDrawInfoV1.qry"
    _SP_PARAM = {"pls": "35,0", "plw": "350133,0", "qxc": "04,0"}
    _SP_KEY = {"pls": "pls", "plw": "plw", "qxc": "qxc"}

    def fetch_sporttery_latest(self, code: str) -> list[dict]:
        """从体彩官网拉取最新一期。code in pls/plw/qxc。

        体彩官方接口仅返回最新一期，历史数据需由 sync_data 增量合并累积。
        """
        param = self._SP_PARAM[code]
        try:
            resp = self.sess.get(self._SP_API, params={"param": param, "isVerify": 1},
                                 headers={**self.HEADERS,
                                          "Referer": "https://www.sporttery.cn/"},
                                 timeout=20)
            data = resp.json()
        except Exception as e:
            print(f"[爬虫] 体彩 {code} 请求失败: {e}")
            return []
        v = (data.get("value") or {}).get(self._SP_KEY[code], {})
        lp = v.get("lastPoolFund") or v.get("lastPoolDraw") or {}
        if not lp:
            # 兼容字段名：部分版本为 lastPoolDraw
            lp = v.get("lastPoolDraw") or {}
        if not lp:
            print(f"[爬虫] 体彩 {code} 暂无最新开奖")
            return []
        draw_number = str(lp.get("lotteryDrawNum", "")).strip()
        result = (lp.get("lotteryDrawResult") or lp.get("lotteryUnsortDrawresult") or "").strip()
        draw_time = lp.get("lotteryDrawTime") or ""
        draw_date = draw_time[:10] if draw_time else ""
        if not draw_date or not result:
            return []
        try:
            numbers = [int(x) for x in result.split() if x.strip()]
        except ValueError:
            return []
        pool = str(lp.get("poolBalanceAfterdraw") or "0").replace(",", "")
        return [{
            "lottery_code": code,
            "draw_number": draw_number,
            "draw_date": draw_date,
            "numbers": json.dumps(numbers),
            "extra_numbers": json.dumps([]),
            "prize_pool": pool,
            "sales": "0",
        }]

    # ---------- 统一分发 ----------
    def fetch_all(self, code: str, max_pages: int = 4) -> list[dict]:
        """按彩种代码统一抓取：ssq/dlt/hk6 走原逻辑，福彩/体彩走新接口。"""
        if code == "ssq":
            return self.fetch_all_ssq(max_pages=max_pages)
        if code == "dlt":
            return self.fetch_all_dlt(max_pages=max_pages)
        if code == "hk6":
            return self.fetch_all_hk6(max_pages=max_pages)
        if code in ("kl8", "3d", "qlc"):
            return self.fetch_fc_all(code)
        if code in ("pls", "plw", "qxc"):
            return self.fetch_sporttery_latest(code)
        print(f"[爬虫] 未知彩种: {code}")
        return []

    # ---------- 香港六合彩：新数据源（pil.io，无需代理）----------

    def fetch_hk6_pilio(self, pages: int = 5) -> list[dict]:
        """从 pilio.idv.tw 抓取六合彩数据（纯 HTML，无需代理）

        Returns:
            按日期从新到旧排列的记录，draw_number 为空字符串，
            调用方需自行匹配已有记录或生成期号。
        """
        import urllib.request
        from html.parser import HTMLParser
        import re, datetime

        class _HkParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tables = []          # 所有表格，每个表格是 list[list[str]]
                self._cur_table = None
                self._in_table = False
                self._in_tr = False
                self._in_td = False
                self._row = []
                self._cell = ""
            def handle_starttag(self, tag, attrs):
                if tag == "table":
                    self._cur_table = []
                    self._in_table = True
                elif tag == "tr" and self._in_table:
                    self._row = []
                    self._in_tr = True
                elif tag == "td" and self._in_tr:
                    self._in_td = True
                    self._cell = ""
            def handle_endtag(self, tag):
                if tag == "table" and self._in_table:
                    if self._cur_table:
                        self.tables.append(self._cur_table)
                    self._cur_table = None
                    self._in_table = False
                elif tag == "tr" and self._in_tr:
                    if self._row:
                        self._cur_table.append(self._row)
                    self._in_tr = False
                elif tag == "td" and self._in_td:
                    self._row.append(self._cell.strip())
                    self._in_td = False
            def handle_data(self, data):
                if self._in_td:
                    self._cell += data

        results = []
        seen_dates = set()

        for page in range(1, pages + 1):
            url = f"https://www.pilio.idv.tw/ltohk/list.asp?indexpage={page}&orderby=new"
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
            try:
                html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8")
            except Exception as e:
                print(f"[爬虫] pilio 第{page}页请求异常: {e}")
                continue

            parser = _HkParser()
            parser.feed(html)

            # 处理所有表格中符合条件的行（3列，第一列是日期格式）
            for table in parser.tables:
                for row in table:
                    if len(row) != 3:
                        continue
                    m = re.match(r"(\d{1,2})/(\d{2})(\d{2})\(", row[0])
                    if not m:
                        continue
                    month, day, year_suffix = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    dt = datetime.date(2000 + year_suffix, month, day)
                    date_key = dt.isoformat()
                    if date_key in seen_dates:
                        continue
                    seen_dates.add(date_key)

                    nums_raw = row[1].replace("\xa0", "").strip()
                    try:
                        numbers = [int(n.strip()) for n in nums_raw.split(",") if n.strip()]
                    except ValueError:
                        continue
                    if len(numbers) != 6:
                        continue
                    try:
                        special = int(row[2].strip())
                    except ValueError:
                        continue

                    results.append({
                        "lottery_code": "hk6",
                        "draw_number": "",
                        "draw_date": date_key,
                        "numbers": json.dumps(numbers),
                        "extra_numbers": json.dumps([special]),
                        "prize_pool": "0",
                        "sales": "0",
                    })

        return results
