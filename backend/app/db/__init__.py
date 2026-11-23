"""存储层：SQLite（WAL）+ 手写 Repository（TDD §4.7）。

依赖方向上 db/ 是叶子层：不 import 项目内任何业务模块，实体用 dataclass，
API 契约（Pydantic）在 schemas/ 层另行定义，两者刻意分离。
"""
