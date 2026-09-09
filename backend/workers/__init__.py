# -*- coding: utf-8 -*-
"""backend.workers 包：v1.6.3.5 在线元数据审核的独立执行进程。

- metadata_runner：本机常驻轻量监督服务（认领 ACCEPTED 任务、派生并回收子进程）。
- metadata_audit_worker：单任务子进程入口（提取 DDL → 流式审核 → 制备产物 → 原子发布）。

两者均不 import backend.main（不启动 Web 应用 / BackgroundScheduler / scheduler_lease）。
"""
