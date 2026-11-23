"""横切能力（叶子层，可被任意上层依赖）：异常体系 / 重试 / id / 计时。

🔴 本包内模块**不得** import `app.config` 之外的任何业务模块；`errors.py`、
`ids.py`、`timing.py` 连 `app.config` 也不 import，否则会与配置层形成循环依赖。
"""
