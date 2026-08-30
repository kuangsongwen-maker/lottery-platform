"""
SQLite -> PostgreSQL 数据迁移脚本（幂等，可重复执行）

用法:
    DATABASE_URL=postgresql://user:pw@host/db python migrate_to_pg.py

行为:
    1. 在目标 PG 库建表（若不存在）
    2. 逐表搬运数据，已存在的记录跳过（按业务唯一键判断，不靠自增 id）
    3. 打印每张表的新增/跳过数量
"""
import os
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, DrawRecord, User, Favorite, SearchHistory, _DEFAULT_SQLITE

PG_URL = os.getenv("DATABASE_URL")
if not PG_URL:
    print("错误: 请先设置 DATABASE_URL 环境变量（PostgreSQL 连接串）")
    print("示例: DATABASE_URL=postgresql://user:pw@host/db python migrate_to_pg.py")
    sys.exit(1)
if PG_URL.startswith("postgres://"):
    PG_URL = PG_URL.replace("postgres://", "postgresql://", 1)

src_engine = create_engine(_DEFAULT_SQLITE, connect_args={"check_same_thread": False})
dst_engine = create_engine(PG_URL)
SrcSession = sessionmaker(bind=src_engine)
DstSession = sessionmaker(bind=dst_engine)


def copy_table(model, unique_fields, label):
    """按 unique_fields 组合成的业务键去重搬运"""
    src, dst = SrcSession(), DstSession()
    added = skipped = 0
    try:
        rows = src.query(model).all()
        existing_keys = set()
        for r in dst.query(model).all():
            existing_keys.add(tuple(getattr(r, f) for f in unique_fields))

        for r in rows:
            key = tuple(getattr(r, f) for f in unique_fields)
            if key in existing_keys:
                skipped += 1
                continue
            cols = {c.name: getattr(r, c.name) for c in model.__table__.columns
                    if c.name != "id"}
            dst.add(model(**cols))
            existing_keys.add(key)
            added += 1
        dst.commit()
        print(f"  {label:10s} 新增 {added:4d} 条，跳过 {skipped:4d} 条（已存在）")
        return added
    except Exception as e:
        dst.rollback()
        print(f"  {label:10s} 迁移失败: {e}")
        raise
    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    print("=" * 46)
    print("SQLite -> PostgreSQL 迁移")
    print("=" * 46)
    Base.metadata.create_all(bind=dst_engine)
    print("目标库建表完成\n")

    copy_table(DrawRecord, ["lottery_code", "draw_number"], "开奖记录")
    copy_table(User, ["username"], "用户")
    copy_table(Favorite, ["user_id", "lottery_code", "numbers"], "收藏")
    copy_table(SearchHistory, ["user_id", "query"], "搜索历史")

    print("\n迁移完成。")
