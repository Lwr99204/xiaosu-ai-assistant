"""SQLite 连接与建表（TDD §4.7）。

刻意不用 ORM：4 张表、字段固定、无复杂关联，sqlite3 + Repository 手写 SQL
更轻、启动更快、零额外依赖。并发策略照 TDD：WAL 模式 + check_same_thread=False
+ 每操作短连接，规避 FastAPI 线程池下的连接竞争。

已知取舍：写操作在事件循环里同步执行。SQLite 单次 WAL 写入是毫秒级，单机
内部系统可接受；未来若出现慢查询再迁到线程池，当前不为此增加复杂度。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

#: 建表 DDL。与代码同目录，打包/拷贝时不会丢。
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: Path) -> sqlite3.Connection:
    """打开一个短连接。调用方负责用 contextlib.closing 关闭。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path) -> None:
    """启动建表。schema 全部 IF NOT EXISTS，幂等可重复执行。"""
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with closing(connect(db_path)) as conn, conn:
        conn.executescript(schema)


def now_iso() -> str:
    """统一时间字符串：UTC ISO-8601 秒级。字典序即时间序，可直接 ORDER BY。"""
    return datetime.now(UTC).replace(microsecond=0).isoformat()
