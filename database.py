"""数据库模型与操作"""
import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

_DB_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_SQLITE = f"sqlite:///{os.path.join(_DB_DIR, 'lottery.db')}"

# 部署平台（Render / Neon / Supabase 等）注入 DATABASE_URL 时自动切到 PostgreSQL，
# 本地开发不设该变量则继续用 SQLite，两边代码完全一致。
DATABASE_URL = os.getenv("DATABASE_URL") or _DEFAULT_SQLITE
# 部分平台仍给 postgres:// 旧前缀，SQLAlchemy 已不支持
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_IS_SQLITE = DATABASE_URL.startswith("sqlite")
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if _IS_SQLITE else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ========== 彩种配置 ==========
LOTTERY_CONFIG = {
    "ssq": {
        "name": "双色球",
        "draw_days": "2,4,7",
        "main_label": "红球",
        "main_count": 6, "main_min": 1, "main_max": 33,
        "extra_label": "蓝球",
        "extra_count": 1, "extra_min": 1, "extra_max": 16,
    },
    "dlt": {
        "name": "大乐透",
        "draw_days": "1,3,6",
        "main_label": "前区",
        "main_count": 5, "main_min": 1, "main_max": 35,
        "extra_label": "后区",
        "extra_count": 2, "extra_min": 1, "extra_max": 12,
    },
    "hk6": {
        "name": "香港六合彩",
        "draw_days": "2,4,6,7",
        "main_label": "搅珠号码",
        "main_count": 6, "main_min": 1, "main_max": 49,
        "extra_label": "特别号码",
        "extra_count": 1, "extra_min": 1, "extra_max": 49,
    }
}

# ========== ORM 模型 ==========

class DrawRecord(Base):
    """开奖记录表"""
    __tablename__ = "draw_records"
    id = Column(Integer, primary_key=True, index=True)
    lottery_code = Column(String(10), index=True, nullable=False)
    draw_number = Column(String(20), index=True, nullable=False)
    draw_date = Column(String(10), nullable=False)
    numbers = Column(String(200), nullable=False)       # JSON: [1,5,12,23,28,33]
    extra_numbers = Column(String(100), nullable=False)  # JSON: [7]
    prize_pool = Column(String(50))
    sales = Column(String(50))
    details = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    """用户表"""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    password_hash = Column(String(200), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Favorite(Base):
    """收藏表"""
    __tablename__ = "favorites"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    lottery_code = Column(String(10), nullable=False)
    numbers = Column(String(200))        # 收藏的主号码 JSON
    extra_numbers = Column(String(100))  # 收藏的特别号码 JSON
    note = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


class SearchHistory(Base):
    """搜索历史表"""
    __tablename__ = "search_history"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=True)
    lottery_code = Column(String(10))
    search_type = Column(String(20))  # draw_number / number_search
    query = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


# ========== 数据库初始化 ==========

def init_db():
    """建表，写彩种配置"""
    Base.metadata.create_all(bind=engine)

def get_db():
    """FastAPI 依赖：获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
