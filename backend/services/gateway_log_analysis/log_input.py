# -*- coding: utf-8 -*-
"""v1.6.3.4 / D06：网关日志共享逐行输入层（REQ-04，DETAIL §6.5）

标准库实现的流式逐行输入，供平台侧质量统计消费——消除原 analyze_log 的
`await file.read()` 全量读取 + `decode().splitlines()` 同时保留多份内容的
内存风险（§6.1 问题 3）。

契约（§6.5）：
  · 增量处理 UTF-8（可选 BOM）、LF/CRLF，最后一行没有换行也计一行；
  · 不得 errors='ignore' 静默丢字节——非法编码记录 encoding_error 行、列入
    跳过覆盖率，保留最多若干条经过脱敏的原因样例；
  · 超长单行在缓冲超过上限时立即拒绝（too_long），不能先 read 整行再检查；
  · NaN/Infinity/负耗时的数值有效性由消费方（统计层）判定，本层只负责
    忠实产出物理行与读取状态。

本层**不**解析日志语义（timecost 等），只做字节→行的忠实流式切分与状态标注，
供 interf/sql 两种 Web 日志的质量统计共用，避免两处判据漂移。
"""
from __future__ import annotations

import codecs

# 行读取状态
LINE_OK = "ok"
LINE_ENCODING_ERROR = "encoding_error"
LINE_TOO_LONG = "too_long"

# 每次读取的块大小（64 KiB）
_READ_CHUNK = 65536


def iter_log_lines(file_path: str, max_line_bytes: int = 1048576):
    """流式逐行读取日志文件，yield (line_text, status)。

    Args:
        file_path: 受控日志文件路径
        max_line_bytes: 单物理行硬上限（GATEWAY_MAX_LINE_BYTES，默认 1 MiB）；
                        超长行立即拒绝并丢弃至下一个换行，不先 read 整行再检查。

    Yields:
        (text, status)：
          status=LINE_OK             → text 为解码后的行文本（不含行尾 \\n/\\r）
          status=LINE_ENCODING_ERROR → 非法 UTF-8，text=''（不静默丢字节，显式标注）
          status=LINE_TOO_LONG       → 超长行，text=''

    说明：
      · 首行剥 UTF-8 BOM（若有）；
      · LF 与 CRLF 均正确处理（剥行尾 \\r）；
      · 最后一行无换行也计一行；
      · 空行（text=''）由消费方计入 empty_lines，本层照常产出 status=LINE_OK。
    """
    bom_len = len(codecs.BOM_UTF8)
    with open(file_path, "rb") as f:
        buf = b""
        first = True
        too_long_pending = False   # 当前行已判超长，丢弃其剩余部分直到下一个 \n
        while True:
            chunk = f.read(_READ_CHUNK)
            if not chunk:
                break
            buf += chunk
            # 处理缓冲中所有完整行（以 \n 分隔）
            while b"\n" in buf:
                line_bytes, buf = buf.split(b"\n", 1)
                if too_long_pending:
                    # 超长行的后续片段：丢弃，直到遇到换行结束该行
                    too_long_pending = False
                    continue
                if first:
                    if line_bytes.startswith(codecs.BOM_UTF8):
                        line_bytes = line_bytes[bom_len:]
                    first = False
                if line_bytes.endswith(b"\r"):
                    line_bytes = line_bytes[:-1]
                if len(line_bytes) > max_line_bytes:
                    yield ("", LINE_TOO_LONG)
                    continue
                try:
                    yield (line_bytes.decode("utf-8"), LINE_OK)
                except UnicodeDecodeError:
                    yield ("", LINE_ENCODING_ERROR)
            # 无换行但缓冲已超限：当前行超长，立即拒绝并丢弃至下一个 \n
            if not too_long_pending and len(buf) > max_line_bytes:
                yield ("", LINE_TOO_LONG)
                too_long_pending = True
                buf = b""
                first = False
        # 文件结束：处理最后一行（可能无换行）
        if buf and not too_long_pending:
            if first and buf.startswith(codecs.BOM_UTF8):
                buf = buf[bom_len:]
            if buf.endswith(b"\r"):
                buf = buf[:-1]
            if len(buf) > max_line_bytes:
                yield ("", LINE_TOO_LONG)
            else:
                try:
                    yield (buf.decode("utf-8"), LINE_OK)
                except UnicodeDecodeError:
                    yield ("", LINE_ENCODING_ERROR)


def count_file_bytes_and_sha256(file_path: str, chunk_size: int = 1048576):
    """流式计算文件净字节数与 SHA-256（§6.5：累计净文件字节并计算 SHA-256）。

    不全量读进内存。返回 (byte_count, sha256_hex)。
    """
    import hashlib
    h = hashlib.sha256()
    total = 0
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            total += len(chunk)
            h.update(chunk)
    return total, h.hexdigest()
