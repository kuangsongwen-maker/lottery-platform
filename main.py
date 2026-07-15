"""彩票数据平台 - 后端入口 (FastAPI + SQLite)"""
import json, threading, os, random
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Query, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from jose import JWTError, jwt
from passlib.context import CryptContext

from database import (
    init_db, get_db, SessionLocal,
    DrawRecord, User, Favorite, SearchHistory,
    LOTTERY_CONFIG,
)
from crawler import LotteryCrawler

# ========== 配置 ==========
SECRET_KEY = os.getenv("JWT_SECRET", "lottery-platform-dev-secret-key-2024")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE = 30  # 天
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
auth_scheme = HTTPBearer(auto_error=False)
crawler = LotteryCrawler()


# ========== 应用生命周期 ==========

REFRESH_INTERVAL = 6 * 3600  # 6小时

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _auto_seed()
    # 定时刷新
    def _periodic_refresh():
        while True:
            threading.Event().wait(REFRESH_INTERVAL)
            try:
                _auto_seed()
            except Exception as e:
                print(f"[定时] 刷新异常: {e}")
    t = threading.Thread(target=_periodic_refresh, daemon=True)
    t.start()
    yield

app = FastAPI(title="彩票数据平台", version="0.1.0", lifespan=lifespan)


# ========== 辅助函数 ==========

def _auto_seed():
    """每次启动时自动更新最近缺失的开奖数据"""
    db = SessionLocal()
    try:
        print("[启动] 检查并更新最新开奖数据...")
        def job():
            db2 = SessionLocal()
            try:
                for code in ("ssq", "dlt", "hk6"):
                    existing = {r.draw_number for r in
                                db2.query(DrawRecord.draw_number)
                                .filter(DrawRecord.lottery_code == code).all()}
                    new_count = 0

                    if code == "hk6":
                        # 六合彩使用 pilio 数据源（无需代理），按日期匹配
                        records = crawler.fetch_hk6_pilio(pages=5)
                        existing_dates = {
                            r.draw_date for r in
                            db2.query(DrawRecord.draw_date)
                            .filter(DrawRecord.lottery_code == "hk6").all()
                        }
                        latest = (db2.query(DrawRecord)
                                  .filter(DrawRecord.lottery_code == "hk6")
                                  .order_by(DrawRecord.draw_number.desc()).first())
                        next_num = int(latest.draw_number[2:]) + 1 if latest else 1
                        year_prefix = datetime.now().strftime("%y")

                        for r in records:
                            if r["draw_date"] in existing_dates:
                                rec = db2.query(DrawRecord).filter_by(
                                    lottery_code="hk6", draw_date=r["draw_date"]).first()
                                if rec:
                                    rec.numbers = r["numbers"]
                                    rec.extra_numbers = r["extra_numbers"]
                                continue
                            r["draw_number"] = f"{year_prefix}{next_num:03d}"
                            next_num += 1
                            db2.add(DrawRecord(**r))
                            new_count += 1
                    else:
                        fetcher = (
                            crawler.fetch_all_ssq if code == "ssq" else
                            crawler.fetch_all_dlt
                        )
                        records = fetcher(max_pages=4)
                        for r in records:
                            if r["draw_number"] not in existing:
                                db2.add(DrawRecord(**r))
                                existing.add(r["draw_number"])
                                new_count += 1
                            else:
                                rec = db2.query(DrawRecord).filter_by(
                                    lottery_code=code, draw_number=r["draw_number"]).first()
                                if rec:
                                    rec.numbers = r["numbers"]
                                    rec.extra_numbers = r["extra_numbers"]
                                    rec.draw_date = r["draw_date"]
                                    if "prize_pool" in r: rec.prize_pool = r["prize_pool"]
                                    if "sales" in r: rec.sales = r["sales"]

                    db2.commit()
                    print(f"[启动] {LOTTERY_CONFIG[code]['name']}: 新增 {new_count} 期")
            except Exception as e:
                print(f"[启动] 更新数据异常: {e}")
                db2.rollback()
            finally:
                db2.close()
        threading.Thread(target=job, daemon=True).start()
    finally:
        db.close()


def create_token(user_id: int) -> str:
    expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE)
    return jwt.encode({"sub": str(user_id), "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(cred: HTTPAuthorizationCredentials = Depends(auth_scheme),
                     db: Session = Depends(get_db)) -> User:
    if cred is None:
        raise HTTPException(401, "请先登录")
    try:
        payload = jwt.decode(cred.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        uid = int(payload.get("sub"))
    except (JWTError, ValueError, TypeError):
        raise HTTPException(401, "登录已过期，请重新登录")
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(401, "用户不存在")
    return user


def _match_count(hit: list[int], target: list[int]) -> int:
    """统计命中号码个数"""
    hs, ts = set(hit), set(target)
    return len(hs & ts)


def _calc_prize_ssq(red_hit: int, blue_hit: int):
    """双色球中奖判断"""
    rules = [
        (6, 1, "一等奖", "浮动"),
        (6, 0, "二等奖", "浮动"),
        (5, 1, "三等奖", "3000元"),
        (5, 0, "四等奖", "200元"),
        (4, 1, "四等奖", "200元"),
        (4, 0, "五等奖", "10元"),
        (3, 1, "五等奖", "10元"),
        (2, 1, "六等奖", "5元"),
        (1, 1, "六等奖", "5元"),
        (0, 1, "六等奖", "5元"),
    ]
    for r, b, name, amount in rules:
        if red_hit >= r and blue_hit >= b:
            return name, amount
    return "未中奖", "0元"


def _calc_prize_dlt(front_hit: int, back_hit: int):
    """大乐透中奖判断"""
    rules = [
        (5, 2, "一等奖", "浮动"),
        (5, 1, "二等奖", "浮动"),
        (5, 0, "三等奖", "10000元"),
        (4, 2, "四等奖", "3000元"),
        (4, 1, "五等奖", "300元"),
        (3, 2, "六等奖", "200元"),
        (4, 0, "七等奖", "100元"),
        (3, 1, "八等奖", "15元"),
        (2, 2, "八等奖", "15元"),
        (3, 0, "九等奖", "5元"),
        (1, 2, "九等奖", "5元"),
        (2, 1, "九等奖", "5元"),
        (0, 2, "九等奖", "5元"),
    ]
    for f, b, name, amount in rules:
        if front_hit >= f and back_hit >= b:
            return name, amount
    return "未中奖", "0元"


def _calc_prize_hk6(main_hit: int, special_hit: int):
    """香港六合彩中奖判断"""
    rules = [
        (6, 0, "头奖", "浮动"),
        (5, 1, "二奖", "浮动"),
        (5, 0, "三奖", "浮动"),
        (4, 1, "四奖", "9600元"),
        (4, 0, "五奖", "640元"),
        (3, 1, "六奖", "320元"),
        (3, 0, "七奖", "40元"),
    ]
    for m, e, name, amount in rules:
        if main_hit >= m and special_hit >= e:
            return name, amount
    return "未中奖", "0元"


def _fetch_or_crawl(db: Session, lottery: str, draw_number: str) -> DrawRecord | None:
    """查缓存，没有则实时爬取"""
    rec = db.query(DrawRecord).filter_by(lottery_code=lottery, draw_number=draw_number).first()
    if rec:
        return rec
    if lottery == "ssq":
        records = crawler.fetch_ssq(num_periods=200)
    elif lottery == "dlt":
        records = crawler.fetch_dlt(num_periods=200)
    else:
        try:
            records = crawler.fetch_all_hk6()
        except Exception:
            records = []
    target = None
    for r in records:
        if r["draw_number"] == draw_number:
            target = DrawRecord(**r)
        else:
            # 也存下其他记录
            exist = db.query(DrawRecord).filter_by(lottery_code=lottery,
                                                    draw_number=r["draw_number"]).first()
            if not exist:
                db.add(DrawRecord(**r))
    db.commit()
    if target:
        db.add(target)
        db.commit()
        db.refresh(target)
    return target


# ========== 彩种 API ==========

@app.get("/api/lotteries")
def list_lotteries(db: Session = Depends(get_db)):
    result = []
    for code, cfg in LOTTERY_CONFIG.items():
        latest = db.query(DrawRecord).filter_by(lottery_code=code) \
            .order_by(desc(DrawRecord.draw_number)).first()
        result.append({
            "code": code,
            "name": cfg["name"],
            "draw_days": cfg["draw_days"],
            "main_label": cfg["main_label"],
            "extra_label": cfg["extra_label"],
            "main_count": cfg["main_count"],
            "main_min": cfg["main_min"], "main_max": cfg["main_max"],
            "extra_count": cfg["extra_count"],
            "extra_min": cfg["extra_min"], "extra_max": cfg["extra_max"],
            "latest": {
                "draw_number": latest.draw_number,
                "draw_date": latest.draw_date,
                "numbers": json.loads(latest.numbers),
                "extra_numbers": json.loads(latest.extra_numbers),
            } if latest else None,
        })
    return result


# ========== 开奖数据 API ==========

@app.get("/api/draws/{lottery}")
def get_draw(lottery: str,
             draw_number: str = Query(None),
             date: str = Query(None),
             db: Session = Depends(get_db)):
    if lottery not in LOTTERY_CONFIG:
        raise HTTPException(404, "彩种不存在")
    q = db.query(DrawRecord).filter(DrawRecord.lottery_code == lottery)
    if draw_number:
        rec = _fetch_or_crawl(db, lottery, draw_number)
        if not rec:
            raise HTTPException(404, "未找到该期号")
        return _format_draw(rec)
    if date:
        recs = q.filter(DrawRecord.draw_date == date) \
            .order_by(desc(DrawRecord.draw_number)).limit(50).all()
        return [_format_draw(r) for r in recs]
    raise HTTPException(400, "请提供 draw_number 或 date")


@app.get("/api/draws/{lottery}/latest")
def latest_draws(lottery: str, count: int = Query(20, ge=1, le=200),
                 offset: int = Query(0, ge=0),
                 db: Session = Depends(get_db)):
    if lottery not in LOTTERY_CONFIG:
        raise HTTPException(404, "彩种不存在")

    total = db.query(DrawRecord).filter_by(lottery_code=lottery).count()
    recs = db.query(DrawRecord).filter_by(lottery_code=lottery) \
        .order_by(desc(DrawRecord.draw_number)).offset(offset).limit(count).all()
    if not recs:
        # 自动抓取
        fetcher = (
                        crawler.fetch_all_ssq if lottery == "ssq" else
                        crawler.fetch_all_dlt if lottery == "dlt" else
                        crawler.fetch_all_hk6
                    )
        records = fetcher(max_pages=2)
        saved = []
        for r in records:
            exist = db.query(DrawRecord).filter_by(lottery_code=lottery,
                                                    draw_number=r["draw_number"]).first()
            if not exist:
                db.add(DrawRecord(**r))
                saved.append(r)
        if saved:
            db.commit()
            recs = db.query(DrawRecord).filter_by(lottery_code=lottery) \
                .order_by(desc(DrawRecord.draw_number)).offset(offset).limit(count).all()
            total = len(recs)
    return {"total": total, "draws": [_format_draw(r) for r in recs]}


@app.get("/api/draws/{lottery}/search")
def search_numbers(lottery: str,
                   numbers: str = Query(..., description="逗号分隔号码"),
                   match_type: str = Query("exact", regex="^(exact|any)$"),
                   page: int = Query(1, ge=1),
                   page_size: int = Query(20, ge=1, le=100),
                   db: Session = Depends(get_db)):
    """号码搜索：给定一组号码，查出历史上包含这些号码的期数"""
    if lottery not in LOTTERY_CONFIG:
        raise HTTPException(404, "彩种不存在")
    search_nums = [int(n.strip()) for n in numbers.split(",") if n.strip().isdigit()]
    if not search_nums:
        raise HTTPException(400, "请输入有效号码")

    cfg = LOTTERY_CONFIG[lottery]
    all_main = set(range(cfg["main_min"], cfg["main_max"] + 1))
    if not all(n in all_main for n in search_nums):
        raise HTTPException(400, f"号码超出范围 {cfg['main_min']}-{cfg['main_max']}")

    # 第一次搜索如果没有本地数据则自动抓取
    count = db.query(DrawRecord).filter_by(lottery_code=lottery).count()
    if count == 0:
        fetcher = (
                        crawler.fetch_all_ssq if lottery == "ssq" else
                        crawler.fetch_all_dlt if lottery == "dlt" else
                        crawler.fetch_all_hk6
                    )
        records = fetcher(max_pages=4)
        for r in records:
            if not db.query(DrawRecord).filter_by(lottery_code=lottery,
                                                   draw_number=r["draw_number"]).first():
                db.add(DrawRecord(**r))
        db.commit()

    all_records = db.query(DrawRecord).filter_by(lottery_code=lottery) \
        .order_by(desc(DrawRecord.draw_number)).all()

    result = []
    for rec in all_records:
        draw_nums = set(json.loads(rec.numbers))
        if match_type == "exact":
            ok = all(n in draw_nums for n in search_nums)
        else:
            ok = any(n in draw_nums for n in search_nums)
        if ok:
            matched = [n for n in search_nums if n in draw_nums]
            result.append({
                "draw_number": rec.draw_number,
                "draw_date": rec.draw_date,
                "numbers": json.loads(rec.numbers),
                "extra_numbers": json.loads(rec.extra_numbers),
                "matched_numbers": matched,
                "match_count": len(matched),
            })

    result.sort(key=lambda x: (-x["match_count"], -int(x["draw_number"])))

    total = len(result)
    start = (page - 1) * page_size
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "results": result[start:start + page_size],
    }


def _format_draw(rec: DrawRecord) -> dict:
    return {
        "draw_number": rec.draw_number,
        "draw_date": rec.draw_date,
        "numbers": json.loads(rec.numbers),
        "extra_numbers": json.loads(rec.extra_numbers),
        "prize_pool": rec.prize_pool,
        "sales": rec.sales,
    }


# ========== 分析 API ==========

@app.get("/api/analysis/{lottery}/hot-cold")
def hot_cold(lottery: str, range_periods: int = Query(50, alias="range", ge=10, le=500),
             db: Session = Depends(get_db)):
    if lottery not in LOTTERY_CONFIG:
        raise HTTPException(404, "彩种不存在")
    cfg = LOTTERY_CONFIG[lottery]

    # 确保有数据
    if db.query(DrawRecord).filter_by(lottery_code=lottery).count() == 0:
        fetcher = (
                        crawler.fetch_all_ssq if lottery == "ssq" else
                        crawler.fetch_all_dlt if lottery == "dlt" else
                        crawler.fetch_all_hk6
                    )
        records = fetcher(max_pages=4)
        for r in records:
            if not db.query(DrawRecord).filter_by(lottery_code=lottery,
                                                   draw_number=r["draw_number"]).first():
                db.add(DrawRecord(**r))
        db.commit()

    recs = db.query(DrawRecord).filter_by(lottery_code=lottery) \
        .order_by(desc(DrawRecord.draw_number)).limit(range_periods).all()
    if not recs:
        raise HTTPException(404, "暂无数据")

    freq = {}
    for rec in recs:
        for n in json.loads(rec.numbers):
            freq[n] = freq.get(n, 0) + 1

    total = len(recs)
    main_range = range(cfg["main_min"], cfg["main_max"] + 1)
    stats = []
    for n in main_range:
        c = freq.get(n, 0)
        stats.append({"number": n, "count": c, "rate": round(c / total * 100, 1),
                       "missing": total - c})

    stats.sort(key=lambda x: x["count"], reverse=True)
    return {
        "total_periods": total,
        "hot": stats[:20],
        "cold": sorted(stats, key=lambda x: x["count"])[:20],
    }


@app.post("/api/analysis/prize-calc")
def prize_calc(body: dict, db: Session = Depends(get_db)):
    lottery = body.get("lottery")
    draw_number = body.get("draw_number", "")
    user_nums = body.get("numbers", [])
    user_extra = body.get("extra_numbers", [])
    if lottery not in LOTTERY_CONFIG:
        raise HTTPException(404, "彩种不存在")

    rec = _fetch_or_crawl(db, lottery, draw_number)
    if not rec:
        raise HTTPException(404, "未找到该期开奖结果")

    draw_nums = json.loads(rec.numbers)
    draw_extra = json.loads(rec.extra_numbers)

    if lottery == "ssq":
        main_hit = _match_count(user_nums, draw_nums)
        extra_hit = _match_count(user_extra, draw_extra)
        name, amount = _calc_prize_ssq(main_hit, extra_hit)
        ml, el = "红球", "蓝球"
    elif lottery == "dlt":
        main_hit = _match_count(user_nums, draw_nums)
        extra_hit = _match_count(user_extra, draw_extra)
        name, amount = _calc_prize_dlt(main_hit, extra_hit)
        ml, el = "前区", "后区"
    else:  # hk6: 特别号码与搅珠号码同池，用户只需输入6个号码
        main_hit = _match_count(user_nums, draw_nums)
        extra_hit = 1 if draw_extra and draw_extra[0] in user_nums else 0
        name, amount = _calc_prize_hk6(main_hit, extra_hit)
        ml, el = "搅珠号码", "特别号码"

    return {
        "draw_number": draw_number,
        "draw_numbers": draw_nums,
        "draw_extra": draw_extra,
        "user_numbers": user_nums,
        "user_extra": user_extra,
        "match_detail": {
            "main_hit": main_hit,
            "extra_hit": extra_hit,
        },
        "main_label": ml,
        "extra_label": el,
        "prize_level": name,
        "prize_amount": amount,
    }


@app.post("/api/analysis/compare")
def compare_bets(body: dict, db: Session = Depends(get_db)):
    """多注号码对比"""
    lottery = body.get("lottery")
    draw_number = body.get("draw_number", "")
    bets = body.get("bets", [])  # [[numbers, extra_numbers], ...]
    if lottery not in LOTTERY_CONFIG:
        raise HTTPException(404, "彩种不存在")
    if not bets:
        raise HTTPException(400, "请至少输入一注")

    rec = _fetch_or_crawl(db, lottery, draw_number)
    if not rec:
        raise HTTPException(404, "未找到该期开奖结果")

    draw_nums = json.loads(rec.numbers)
    draw_extra = json.loads(rec.extra_numbers)
    if lottery == "ssq":
        calc_fn = _calc_prize_ssq
    elif lottery == "dlt":
        calc_fn = _calc_prize_dlt
    else:
        calc_fn = _calc_prize_hk6

    results = []
    for idx, bet in enumerate(bets):
        nums = bet[0] if isinstance(bet, list) else bet.get("numbers", [])
        extra = bet[1] if isinstance(bet, list) else bet.get("extra_numbers", [])
        mh = _match_count(nums, draw_nums)
        if lottery == "hk6":
            me = 1 if draw_extra and draw_extra[0] in nums else 0
        else:
            me = _match_count(extra, draw_extra)
        name, amount = calc_fn(mh, me)
        results.append({
            "index": idx + 1,
            "numbers": nums,
            "extra_numbers": extra,
            "main_hit": mh,
            "extra_hit": me,
            "prize_level": name,
            "prize_amount": amount,
        })

    return {"draw_number": draw_number, "results": results}


@app.get("/api/analysis/{lottery}/predict")
def predict_numbers(
    lottery: str,
    range_periods: int = Query(200, alias="range", ge=5, le=1000),
    methods: str = Query("mix,random", alias="methods"),
    count: int = Query(1, ge=1, le=20),
    dan: str = Query("", alias="dan"),
    tuo: str = Query("", alias="tuo"),
    db: Session = Depends(get_db),
):
    """号码预测：多方法可选，支持组合，支持多注"""
    if lottery not in LOTTERY_CONFIG:
        raise HTTPException(404, "彩种不存在")
    cfg = LOTTERY_CONFIG[lottery]
    is_hk6 = lottery == "hk6"

    # 解析胆拖
    dan_nums = [int(x.strip()) for x in dan.split(",") if x.strip().isdigit()]
    tuo_nums = [int(x.strip()) for x in tuo.split(",") if x.strip().isdigit()]
    # 过滤胆拖号码在有效范围内
    dan_nums = [n for n in dan_nums if cfg["main_min"] <= n <= cfg["main_max"]]
    tuo_nums = [n for n in tuo_nums if cfg["main_min"] <= n <= cfg["main_max"]]
    # 胆 + 拖 不能超过主号码数量
    if len(dan_nums) > cfg["main_count"]:
        raise HTTPException(400, f"胆码数量不能超过 {cfg['main_count']}")
    if len(dan_nums) + len(tuo_nums) > cfg["main_count"]:
        tuo_nums = tuo_nums[:cfg["main_count"] - len(dan_nums)]

    if db.query(DrawRecord).filter_by(lottery_code=lottery).count() == 0:
        fetcher = (
                        crawler.fetch_all_ssq if lottery == "ssq" else
                        crawler.fetch_all_dlt if lottery == "dlt" else
                        crawler.fetch_all_hk6
                    )
        records = fetcher(max_pages=4)
        for r in records:
            if not db.query(DrawRecord).filter_by(lottery_code=lottery,
                                                   draw_number=r["draw_number"]).first():
                db.add(DrawRecord(**r))
        db.commit()

    recs = db.query(DrawRecord).filter_by(lottery_code=lottery) \
        .order_by(desc(DrawRecord.draw_number)).limit(range_periods).all()
    if not recs:
        raise HTTPException(404, "暂无数据")

    total = len(recs)
    mr = range(cfg["main_min"], cfg["main_max"] + 1)
    er = range(cfg["extra_min"], cfg["extra_max"] + 1)
    mc, ec = cfg["main_count"], cfg["extra_count"]

    # 频率统计 & 遗漏统计
    main_freq, extra_freq = {}, {}
    main_last, extra_last = {}, {}
    for idx, rec in enumerate(recs):
        for n in json.loads(rec.numbers):
            main_freq[n] = main_freq.get(n, 0) + 1
            main_last[n] = idx
        for n in json.loads(rec.extra_numbers):
            extra_freq[n] = extra_freq.get(n, 0) + 1
            extra_last[n] = idx

    # 马尔可夫转移矩阵
    main_trans = {}
    for n in mr:
        main_trans[n] = {m: 1 for m in mr}  # +1平滑
    for i in range(len(recs) - 1):
        curr = json.loads(recs[i].numbers)
        nextn = json.loads(recs[i + 1].numbers)
        for c in curr:
            for n in nextn:
                if c in main_trans and n in main_trans[c]:
                    main_trans[c][n] += 1

    def build_stat(freq, last, cnt):
        return [{"number": n, "frequency": c, "rate": round(c / cnt * 100, 1),
                  "missing": cnt - 1 - last.get(n, -1)} for n, c in freq.items()]

    ms = build_stat(main_freq, main_last, total)
    es = build_stat(extra_freq, extra_last, total)
    main_sorted = sorted(ms, key=lambda x: x["frequency"], reverse=True)
    extra_sorted = sorted(es, key=lambda x: x["frequency"], reverse=True)
    main_cold = sorted(ms, key=lambda x: x["missing"], reverse=True)
    extra_cold = sorted(es, key=lambda x: x["missing"], reverse=True)

    def pick(pool, n):
        return sorted(random.sample(pool, min(n, len(pool))))

    def _apply_dan_tuo(main_list):
        """胆码和拖码作为参考建议，通过加权随机影响预测但不强制出现"""
        # 构建加权池：胆码权重最高，拖码次之，普通号码基础权重
        pool = []
        weights = []
        seen = set()
        for n in range(cfg["main_min"], cfg["main_max"] + 1):
            pool.append(n)
            if n in dan_nums:
                weights.append(100)
            elif n in tuo_nums:
                weights.append(30)
            elif n in main_list:
                weights.append(15)
            else:
                weights.append(5)
        # 根据权重随机选号
        selected = set()
        total = sum(weights)
        while len(selected) < mc:
            r = random.randint(1, total)
            acc = 0
            for i, w in enumerate(weights):
                acc += w
                if r <= acc:
                    selected.add(pool[i])
                    break
            # 移除已选的权重
            for i, w in enumerate(weights):
                if pool[i] in selected:
                    total -= weights[i]
                    weights[i] = 0
        return sorted(selected)

    # 各方法生成器
    def gen_hot():
        pm = [x["number"] for x in main_sorted[:mc + 8]]
        pe = [x["number"] for x in extra_sorted[:ec + 5]]
        return pick(pm if len(pm) >= mc else [x["number"] for x in main_sorted], mc), \
               pick(pe if len(pe) >= ec else [x["number"] for x in extra_sorted], ec)

    def gen_cold():
        pm = [x["number"] for x in main_cold[:mc + 8]]
        pe = [x["number"] for x in extra_cold[:ec + 5]]
        return pick(pm if len(pm) >= mc else [x["number"] for x in main_cold], mc), \
               pick(pe if len(pe) >= ec else [x["number"] for x in extra_cold], ec)

    def gen_mix():
        """综合法：热号为主（约70%），冷号为辅（约30%），加随机"""
        hot_pool = [x["number"] for x in main_sorted[:mc + 6]]
        cold_pool = [x["number"] for x in main_cold[:mc + 6]]
        hot_take = max(int(mc * 0.7), mc - 2)
        cold_take = mc - hot_take
        selected = pick(hot_pool, hot_take)
        cold_candidates = [n for n in cold_pool if n not in selected]
        if cold_candidates:
            selected.extend(pick(cold_candidates, min(cold_take, len(cold_candidates))))
        # 不足的从热号池补
        remaining = [n for n in hot_pool if n not in selected]
        random.shuffle(remaining)
        while len(selected) < mc and remaining:
            selected.append(remaining.pop(0))
        # 特别号码：从高频中随机
        extra_pool = [x["number"] for x in extra_sorted[:max(ec + 5, 10)]]
        return sorted(selected[:mc]), pick(extra_pool, ec)

    def gen_rand():
        return pick(list(mr), mc), pick(list(er), ec)

    def gen_weighted_hot():
        """加权热号法：近期号码权重更高"""
        w_main, w_extra = {}, {}
        for idx, rec in enumerate(recs):
            weight = total - idx  # 最新一期权重最高
            for n in json.loads(rec.numbers):
                w_main[n] = w_main.get(n, 0) + weight
            for n in json.loads(rec.extra_numbers):
                w_extra[n] = w_extra.get(n, 0) + weight
        sorted_m = sorted(w_main.items(), key=lambda x: x[1], reverse=True)
        sorted_e = sorted(w_extra.items(), key=lambda x: x[1], reverse=True)
        pm = [n for n, _ in sorted_m[:mc + 8]]
        pe = [n for n, _ in sorted_e[:ec + 5]]
        return pick(pm if len(pm) >= mc else [n for n, _ in sorted_m], mc), \
               pick(pe if len(pe) >= ec else [n for n, _ in sorted_e], ec)

    def gen_odd_even():
        """奇偶比法：按历史常见奇偶比例生成"""
        oe_freq = {}
        for rec in recs:
            nums = json.loads(rec.numbers)
            odd = sum(1 for n in nums if n % 2 == 1)
            key = f"{odd}:{mc - odd}"
            oe_freq[key] = oe_freq.get(key, 0) + 1
        best = max(oe_freq, key=oe_freq.get)
        best_odd, best_even = map(int, best.split(":"))
        odds = [n for n in mr if n % 2 == 1]
        evens = [n for n in mr if n % 2 == 0]
        selected = pick(odds, min(best_odd, len(odds))) + pick(evens, min(best_even, len(evens)))
        return sorted(selected), pick(list(er), ec)

    def gen_big_small():
        """大小号分布法：按历史常见大小号比例生成"""
        mid = (cfg["main_min"] + cfg["main_max"]) / 2
        bs_freq = {}
        for rec in recs:
            nums = json.loads(rec.numbers)
            big = sum(1 for n in nums if n > mid)
            key = f"{big}:{mc - big}"
            bs_freq[key] = bs_freq.get(key, 0) + 1
        best = max(bs_freq, key=bs_freq.get)
        best_big, best_small = map(int, best.split(":"))
        bigs = [n for n in mr if n > mid]
        smalls = [n for n in mr if n <= mid]
        selected = pick(bigs, min(best_big, len(bigs))) + pick(smalls, min(best_small, len(smalls)))
        return sorted(selected), pick(list(er), ec)

    def gen_sum_range():
        """和值范围法：控制主号码总和在历史常见区间"""
        sums = [sum(json.loads(rec.numbers)) for rec in recs]
        mean = sum(sums) / len(sums)
        std = (sum((s - mean) ** 2 for s in sums) / len(sums)) ** 0.5
        lo, hi = mean - std, mean + std
        for _ in range(100):
            mn = pick(list(mr), mc)
            if lo <= sum(mn) <= hi:
                return sorted(mn), pick(list(er), ec)
        return sorted(mn), pick(list(er), ec)

    def gen_smart():
        """智能综合法（多约束优化）：同时兼顾热号、奇偶比、大小号、和值范围、区间分布"""
        mid = (cfg["main_min"] + cfg["main_max"]) / 2
        step = (cfg["main_max"] - cfg["main_min"] + 1) // 3

        # 计算历史约束参数
        oe_freq = {}
        bs_freq = {}
        all_sums = []
        zone_hit_rate = []
        for rec in recs:
            nums = sorted(json.loads(rec.numbers))
            odd = sum(1 for n in nums if n % 2 == 1)
            oe_freq[odd] = oe_freq.get(odd, 0) + 1
            big = sum(1 for n in nums if n > mid)
            bs_freq[big] = bs_freq.get(big, 0) + 1
            all_sums.append(sum(nums))
            zones = sum(1 for z in range(3)
                        for lo in [cfg["main_min"] + z * step]
                        for hi in [min(cfg["main_min"] + (z+1)*step - 1, cfg["main_max"])]
                        if any(lo <= n <= hi for n in nums))
            zone_hit_rate.append(zones)

        target_odd = max(oe_freq, key=oe_freq.get)
        target_big = max(bs_freq, key=bs_freq.get)
        mean = sum(all_sums) / len(all_sums)
        std = (sum((s - mean) ** 2 for s in all_sums) / len(all_sums)) ** 0.5
        max_freq = max(main_freq.values()) if main_freq else 1

        zone_ranges = [(cfg["main_min"] + z*step,
                        min(cfg["main_min"] + (z+1)*step - 1, cfg["main_max"]))
                       for z in range(3)]

        best_score, best_combo = -999, None
        for _ in range(500):
            combo = sorted(random.sample(list(mr), mc))
            score = 0
            score += sum(main_freq.get(n, 0) for n in combo) / max_freq * 20
            score -= abs(sum(1 for n in combo if n % 2 == 1) - target_odd) * 6
            score -= abs(sum(1 for n in combo if n > mid) - target_big) * 6
            score -= abs(sum(combo) - mean) / max(std, 1) * 4
            zh = sum(1 for lo, hi in zone_ranges if any(lo <= n <= hi for n in combo))
            score += zh * 5
            if any(combo[i+1] - combo[i] == 1 for i in range(mc-1)):
                score += 3
            if score > best_score:
                best_score, best_combo = score, list(combo)

        return sorted(best_combo), pick(list(er), ec)

    def gen_markov():
        """马尔可夫链：基于号码转移概率"""
        if not recs:
            return pick(list(mr), mc), pick(list(er), ec)
        latest = json.loads(recs[0].numbers)
        # 从最新号码出发，累加转移概率
        weights = {}
        for ln in latest:
            if ln in main_trans:
                for target, w in main_trans[ln].items():
                    if target not in weights:
                        weights[target] = 0
                    weights[target] += w
        if not weights:
            return pick(list(mr), mc), pick(list(er), ec)
        # 加权不放回采样
        items = list(weights.items())
        selected = []
        for _ in range(min(mc, len(items))):
            total_w = sum(w for _, w in items)
            if total_w <= 0:
                break
            r = random.random() * total_w
            cum = 0
            for i, (n, w) in enumerate(items):
                cum += w
                if r <= cum:
                    selected.append(n)
                    items.pop(i)
                    break
        # 补足不足
        while len(selected) < mc:
            n = random.choice(list(mr))
            if n not in selected:
                selected.append(n)
        return sorted(selected), pick(list(er), ec)

    gen_map = {
        "hot":  ("热号法",       "从近期出现频率最高的号码中选取",                 gen_hot),
        "cold": ("冷号法",       "从遗漏最久的号码中选取（冷号回补策略）",          gen_cold),
        "mix":  ("综合法",       "热号为主，搭配冷号",                             gen_mix),
        "random": ("纯随机法",   "完全随机生成（每个号码独立等概率）",                 gen_rand),
        "whot": ("加权热号法",   "近期号码权重更高，更关注最新走势",                gen_weighted_hot),
        "oe":   ("奇偶比法",     "控制奇偶比例接近历史最常见分布",                   gen_odd_even),
        "bs":   ("大小号分布法", "按历史常见大小号比例均衡选取",                     gen_big_small),
        "sum":  ("和值范围法",   "号码总和控制在历史均值±1标准差范围内",              gen_sum_range),
        "markov": ("马尔可夫链", "基于号码出现后的转移概率，从最新一期推导",          gen_markov),
        "smart": ("智能综合法", "多约束优化：同时兼顾热号、奇偶比、大小号、和值、区间分布", gen_smart),
    }

    methods_list = [m.strip() for m in methods.split(",") if m.strip() in gen_map]
    results = {}
    for m in methods_list:
        mname, mdesc, gen_fn = gen_map[m]
        bets, seen = [], set()
        for _ in range(count):
            for attempt in range(10):
                mn, en = gen_fn()
                # 应用胆拖约束
                mn = _apply_dan_tuo(mn)
                # HK6 不用输出特别号码
                if is_hk6:
                    en = []
                key = f"{mn}|{en}"
                if key not in seen:
                    seen.add(key)
                    if is_hk6:
                        bets.append({"main_numbers": mn})
                    else:
                        bets.append({"main_numbers": mn, "extra_numbers": en})
                    break
        results[m] = {"name": mname, "description": mdesc, "bets": bets}

    resp = {
        "total_periods": total,
        "methods": methods_list,
        "count": count,
        "results": results,
        "hot_main": main_sorted[:20],
        "cold_main": main_cold[:20],
        "disclaimer": "彩票有风险，购彩需谨慎。以上数据基于历史频率统计，仅供参考，不构成任何购彩建议。",
    }
    if not is_hk6:
        resp["hot_extra"] = extra_sorted[:10]
        resp["cold_extra"] = extra_cold[:10]
    if dan_nums or tuo_nums:
        resp["dan_nums"] = dan_nums
        resp["tuo_nums"] = tuo_nums
    return resp


# ========== 用户 API ==========

@app.post("/api/users/register")
def register(body: dict, db: Session = Depends(get_db)):
    try:
        username = (body.get("username") or "").strip()
        password = (body.get("password") or "").strip()
        if len(username) < 2 or len(password) < 4:
            raise HTTPException(400, "用户名至少2位，密码至少4位")
        if db.query(User).filter(User.username == username).first():
            raise HTTPException(409, "用户名已存在")
        user = User(username=username, password_hash=pwd_ctx.hash(password))
        db.add(user)
        db.commit()
        db.refresh(user)
        return {"token": create_token(user.id), "user": {"id": user.id, "username": user.username}}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[注册] 错误: {e}")
        raise HTTPException(500, f"注册失败: {e}")


@app.post("/api/users/login")
def login(body: dict, db: Session = Depends(get_db)):
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "").strip()
    user = db.query(User).filter(User.username == username).first()
    if not user or not pwd_ctx.verify(password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    return {"token": create_token(user.id), "user": {"id": user.id, "username": user.username}}


@app.get("/api/users/me")
def get_me(user: User = Depends(get_current_user)):
    return {"id": user.id, "username": user.username, "created_at": str(user.created_at)[:10]}


# ========== 收藏 API ==========

@app.get("/api/favorites")
def list_favorites(user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    favs = db.query(Favorite).filter(Favorite.user_id == user.id).all()
    return [{
        "id": f.id,
        "lottery_code": f.lottery_code,
        "lottery_name": LOTTERY_CONFIG.get(f.lottery_code, {}).get("name", ""),
        "numbers": json.loads(f.numbers) if f.numbers else [],
        "extra_numbers": json.loads(f.extra_numbers) if f.extra_numbers else [],
        "note": f.note,
    } for f in favs]


@app.post("/api/favorites")
def add_favorite(body: dict,
                 user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    fav = Favorite(
        user_id=user.id,
        lottery_code=body.get("lottery_code"),
        numbers=json.dumps(body.get("numbers", [])),
        extra_numbers=json.dumps(body.get("extra_numbers", [])),
        note=body.get("note", ""),
    )
    db.add(fav)
    db.commit()
    db.refresh(fav)
    return {"id": fav.id, "message": "收藏成功"}


@app.delete("/api/favorites/{fav_id}")
def delete_favorite(fav_id: int,
                    user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    fav = db.query(Favorite).filter(Favorite.id == fav_id,
                                    Favorite.user_id == user.id).first()
    if not fav:
        raise HTTPException(404, "收藏不存在")
    db.delete(fav)
    db.commit()
    return {"message": "已取消收藏"}


# ========== 手动刷新 ==========

@app.post("/api/refresh")
def refresh_data(db: Session = Depends(get_db)):
    """手动触发数据刷新"""
    from threading import Thread
    def job():
        db2 = SessionLocal()
        try:
            for code in ("ssq", "dlt", "hk6"):
                existing = {r.draw_number for r in
                            db2.query(DrawRecord.draw_number)
                            .filter(DrawRecord.lottery_code == code).all()}
                fetcher = (
                    crawler.fetch_all_ssq if code == "ssq" else
                    crawler.fetch_all_dlt if code == "dlt" else
                    crawler.fetch_all_hk6
                )
                records = fetcher(max_pages=4)
                for r in records:
                    if r["draw_number"] not in existing:
                        db2.add(DrawRecord(**r))
                        existing.add(r["draw_number"])
                    else:
                        rec = db2.query(DrawRecord).filter_by(
                            lottery_code=code, draw_number=r["draw_number"]).first()
                        if rec:
                            rec.numbers = r["numbers"]
                            rec.extra_numbers = r["extra_numbers"]
                            rec.draw_date = r["draw_date"]
                db2.commit()
        except Exception as e:
            print(f"[手动刷新] 异常: {e}")
            db2.rollback()
        finally:
            db2.close()
    Thread(target=job, daemon=True).start()
    return {"message": "数据刷新已启动，请在稍后查看"}


# ========== 手动录入 ==========

@app.post("/api/draws/manual")
def manual_add_draw(body: dict = None,
                    user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """手动录入开奖数据（用于六合彩等无法自动抓取的彩种）"""
    lottery = body.get("lottery", "hk6")
    if lottery not in LOTTERY_CONFIG:
        raise HTTPException(400, "彩种不存在")
    cfg = LOTTERY_CONFIG[lottery]
    draw_number = str(body.get("draw_number", "")).strip()
    draw_date = str(body.get("draw_date", "")).strip()
    numbers = body.get("numbers", [])
    extra_numbers = body.get("extra_numbers", [])
    if not draw_number or not draw_date:
        raise HTTPException(400, "期号和日期不能为空")
    if len(numbers) != cfg["main_count"]:
        raise HTTPException(400, f"主号码需要 {cfg['main_count']} 个")
    if len(extra_numbers) != cfg["extra_count"]:
        raise HTTPException(400, f"特别号码需要 {cfg['extra_count']} 个")
    for n in numbers + extra_numbers:
        if not (cfg["main_min"] <= n <= cfg["main_max"]):
            raise HTTPException(400, f"号码 {n} 超出范围 [{cfg['main_min']}-{cfg['main_max']}]")
    existing = db.query(DrawRecord).filter_by(
        lottery_code=lottery, draw_number=draw_number).first()
    if existing:
        existing.numbers = json.dumps(numbers)
        existing.extra_numbers = json.dumps(extra_numbers)
        existing.draw_date = draw_date
        msg = "已更新"
    else:
        db.add(DrawRecord(
            lottery_code=lottery, draw_number=draw_number,
            draw_date=draw_date,
            numbers=json.dumps(numbers),
            extra_numbers=json.dumps(extra_numbers),
        ))
        msg = "已添加"
    db.commit()
    return {"message": msg}


STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


# ========== 静态文件 ==========

@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
