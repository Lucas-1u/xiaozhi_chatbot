"""
================================================================
文件上传路由 — upload.py
================================================================
接收用户上传的数据文件（CSV / Excel），保存到 data/uploads/ 目录，
供 LLM 用 pandas 读取并绘图。

支持两种端点：
- POST /api/upload        单个文件（保持原行为，向后兼容）
- POST /api/upload/batch  多个文件（同一批用统一前缀，方便模型 glob 一次读全）
"""

import csv
import io
import json
import logging
import os
import uuid

from fastapi import APIRouter, UploadFile, File, HTTPException

from backend.tools.python_executor import UPLOAD_DIR

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["文件"])

# 允许的文件扩展名
ALLOWED_EXT = {".csv", ".xlsx", ".xls", ".txt", ".json"}
MAX_SIZE = 30 * 1024 * 1024       # 单文件最大 30MB
MAX_FILES = 20                    # 单次最多 20 个文件
MAX_TOTAL_SIZE = 100 * 1024 * 1024  # 单次总大小上限 100MB
PREVIEW_ROWS = 200                # 读多少行用来判断列名


def _fix_filename(name: str) -> str:
    """
    修复中文文件名乱码。

    multipart 请求头按 ISO-8859-1(latin-1) 传输，但浏览器实际塞进去的字节
    取决于来源：
      - Chrome / Edge 等现代浏览器：UTF-8 字节      → 例如「ïúÊÛÊý¾Ý.csv」
      - 旧浏览器 / 老系统 ANSI 代码页：GBK 字节     → 例如「²âÊÔÊý¾Ý.csv」
    直接把 filename 暴露出来时两种都会变成乱码，模型看到乱码名就没法正确引用文件。

    这里把 latin-1 还原成 utf-8 / gbk；只有还原结果里确实出现中日韩字符、
    且不含控制字符时，才认为还原成功 —— 避免把 "café.csv" 这类正常名字改坏。
    """
    if not name:
        return name

    # 本来就有中文 = 正常，直接放行
    if any("一" <= c <= "鿿" for c in name):
        return name

    try:
        raw = name.encode("latin-1")
    except UnicodeEncodeError:
        return name  # 含 latin-1 装不下的字符（说明已经是真 UTF-8 中文名）

    for enc in ("utf-8", "gbk"):
        try:
            fixed = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if any("一" <= c <= "鿿" for c in fixed) and not any(ord(c) < 32 for c in fixed):
            return fixed

    return name


def _read_header(content: bytes, ext: str) -> tuple[list, int | None]:
    """兼容旧接口：从体检结果里取列名和行数"""
    p = _profile(content, ext)
    return p.get("columns", []), p.get("rows")


# =================================================================
# 数据体检
# =================================================================
PROFILE_MAX_ROWS = 5000        # 最多采样多少行做统计（防止大文件拖慢上传）
NULL_TOKENS = {"", "na", "n/a", "nan", "null", "none", "-", "--", "无", "空"}


def _guess_type(values: list) -> str:
    """从采样值猜列类型：数值 / 日期 / 文本 / 空"""
    nums = dates = checked = 0
    for v in values:
        s = str(v).strip()
        if s.lower() in NULL_TOKENS:
            continue
        checked += 1
        if checked > 200:
            break
        try:
            float(s.replace(",", "").replace("，", "").replace("%", ""))
            nums += 1
            continue
        except ValueError:
            pass
        # 粗略识别日期：2020-01-01 / 2020/1/1 / 2020年1月
        if len(s) >= 6 and any(ch in s for ch in "-/年月"):
            digits = sum(c.isdigit() for c in s)
            if digits >= 4 and digits >= len(s) * 0.4:
                dates += 1

    if checked == 0:
        return "空"
    if nums / checked >= 0.9:
        return "数值"
    if dates / checked >= 0.8:
        return "日期"
    return "文本"


def _to_number(v):
    try:
        return float(str(v).replace(",", "").replace("，", "").replace("%", "").strip())
    except (ValueError, AttributeError):
        return None


def _profile_rows(header: list, rows: list, total_rows: int | None) -> dict:
    """对采样到的数据行做体检"""
    profile = {
        "rows": total_rows if total_rows is not None else len(rows),
        "sampled_rows": len(rows),
        "columns": header,
        "column_types": {},
        "missing": {},
        "unique": {},
        "numeric_stats": {},
        "warnings": [],
    }
    if not header:
        return profile

    col_count = len(header)
    buckets = [[] for _ in range(col_count)]
    for r in rows:
        for i in range(col_count):
            buckets[i].append(r[i] if i < len(r) else "")

    for i, name in enumerate(header):
        vals = buckets[i]
        key = name if name else f"第{i + 1}列"

        profile["missing"][key] = sum(
            1 for v in vals if str(v).strip().lower() in NULL_TOKENS
        )
        profile["unique"][key] = len({str(v).strip() for v in vals})

        ctype = _guess_type(vals)
        profile["column_types"][key] = ctype

        if ctype == "数值":
            nums = sorted(n for n in (_to_number(v) for v in vals) if n is not None)
            if nums:
                n = len(nums)
                median = nums[n // 2]
                q1, q3 = nums[n // 4], nums[(3 * n) // 4]
                iqr = q3 - q1

                if iqr > 0:
                    # 常规情况：IQR 法则
                    lo, hi = q1 - 3 * iqr, q3 + 3 * iqr
                    outliers = sum(1 for x in nums if x < lo or x > hi)
                else:
                    # 整列几乎同一个值（IQR=0）——恰恰是最该报警的场景：
                    # 一片 350 里混进一个 9999。退回「与中位数偏离过半」判断。
                    span = abs(median) * 0.5 if median != 0 else 1.0
                    outliers = sum(1 for x in nums if abs(x - median) > span)

                profile["numeric_stats"][key] = {
                    "min": round(nums[0], 4),
                    "max": round(nums[-1], 4),
                    "mean": round(sum(nums) / n, 4),
                    "median": round(median, 4),
                    "outliers": outliers,
                }

    # ---- 生成给人看的告警 ----
    missing_cols = [(k, v) for k, v in profile["missing"].items() if v > 0]
    if missing_cols:
        sample = "、".join(f"{k}({v})" for k, v in missing_cols[:4])
        more = f"，共 {len(missing_cols)} 列" if len(missing_cols) > 4 else ""
        profile["warnings"].append(f"有缺失值：{sample}{more}")

    for k, st in profile["numeric_stats"].items():
        if st["outliers"] > 0:
            profile["warnings"].append(
                f"「{k}」有 {st['outliers']} 个离群值（明显偏离该列主要取值）"
            )

    const_cols = [k for k, u in profile["unique"].items() if u <= 1]
    if const_cols:
        profile["warnings"].append("取值几乎不变（可能是无用列）：" + "、".join(const_cols[:3]))

    return profile


def _profile(content: bytes, ext: str) -> dict:
    """
    文件体检：行列数、每列类型、缺失值、唯一值、数值列统计、离群值提示。

    只采样前 PROFILE_MAX_ROWS 行（大文件也不拖慢上传）。
    任何异常都降级为空结果 —— 绝不因为体检失败而让上传失败。
    """
    empty = {
        "rows": None, "sampled_rows": 0, "columns": [],
        "column_types": {}, "missing": {}, "unique": {},
        "numeric_stats": {}, "warnings": [],
    }

    # ---- CSV / TXT ----
    if ext in (".csv", ".txt"):
        text = None
        for enc in ("utf-8-sig", "gbk", "utf-8", "gb18030", "latin-1"):
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            return empty
        try:
            reader = csv.reader(io.StringIO(text))
            header, sample, total = [], [], 0
            for i, row in enumerate(reader):
                if not row:
                    continue
                if i == 0:
                    header = [c.strip() for c in row]
                    continue
                total += 1
                if len(sample) < PROFILE_MAX_ROWS:
                    sample.append(row)
        except Exception as e:
            logger.debug(f"体检解析 CSV 失败: {e}")
            return empty
        return _profile_rows(header, sample, total)

    # ---- JSON ----
    if ext == ".json":
        try:
            data = json.loads(content.decode("utf-8"))
        except Exception:
            return empty
        if isinstance(data, list) and data and isinstance(data[0], dict):
            header = list(data[0].keys())
            rows = [[str(it.get(h, "")) for h in header] for it in data[:PROFILE_MAX_ROWS]]
            return _profile_rows(header, rows, len(data))
        if isinstance(data, dict):
            return _profile_rows(list(data.keys()), [], 1)
        return empty

    # ---- Excel ----
    if ext in (".xlsx", ".xls"):
        try:
            import openpyxl
        except ImportError:
            return empty
        try:
            book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            sheet = book[book.sheetnames[0]]
            header, rows = [], []
            for i, row in enumerate(sheet.iter_rows(values_only=True)):
                if i == 0:
                    header = [str(c).strip() if c is not None else "" for c in row]
                    continue
                if len(rows) >= PROFILE_MAX_ROWS:
                    break
                rows.append(["" if c is None else str(c) for c in row])
            book.close()
            # Excel 不数总行数（read_only 下要遍历整个文件，太慢）
            return _profile_rows(header, rows, None)
        except Exception as e:
            logger.debug(f"体检解析 Excel 失败: {e}")
            return empty

    return empty


async def _save_one(file: UploadFile) -> dict:
    """
    校验并保存单个上传文件，返回该文件的描述信息。
    失败时抛 HTTPException。
    """
    original = _fix_filename(os.path.basename(file.filename or ""))
    ext = os.path.splitext(original)[1].lower()

    if ext not in ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {ext or '(无扩展名)'}。支持: {', '.join(sorted(ALLOWED_EXT))}",
        )

    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"文件过大（最大 {MAX_SIZE // 1024 // 1024}MB）: {original}",
        )
    if not content:
        raise HTTPException(status_code=400, detail=f"文件是空的: {original}")

    return {
        "original_name": original,
        "content": content,
        "size": len(content),
    }


# =================================================================
# 单文件端点（保持原有行为，向后兼容）
# =================================================================
@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    上传单个数据文件

    返回：
        {"filename": "数据.csv", "path": "完整路径", "size": 1234}
    """
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    info = await _save_one(file)

    unique_name = f"{uuid.uuid4().hex[:8]}_{info['original_name']}"
    filepath = os.path.join(UPLOAD_DIR, unique_name)
    with open(filepath, "wb") as f:
        f.write(info["content"])

    logger.info(f"📁 文件已上传: {unique_name} ({info['size']} 字节)")

    ext = os.path.splitext(info["original_name"])[1].lower()
    profile = _profile(info["content"], ext)

    return {
        "filename": unique_name,
        "original_name": info["original_name"],
        "path": filepath,
        "size": info["size"],
        "columns": profile.get("columns", []),
        "rows": profile.get("rows"),
        "profile": profile,
    }


# =================================================================
# 批量端点：一次传多个文件
# =================================================================
@router.post("/upload/batch")
async def upload_files_batch(files: list[UploadFile] = File(...)):
    """
    批量上传数据文件（多选 / 拖拽）

    设计要点：
    1. 同一批次的文件使用**统一前缀** `{batch}_{序号}_{原名}`，
       模型可以在 run_python 里用 glob 一次读全：
           import glob, pandas as pd
           paths = sorted(glob.glob(f'{UPLOAD_DIR}/{BATCH}_*.csv'))
           df = pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)
    2. 返回每个文件的表头和行数，让模型不用盲猜有哪些列。
    3. 单个文件失败不影响其他文件（部分成功），失败原因逐个返回。

    返回：
        {
          "batch_id": "3f2a1b9c",
          "count": 3, "ok": 2, "failed": 1,
          "files": [{"filename", "original_name", "path", "size", "columns", "rows"}],
          "errors": [{"original_name", "reason"}]
        }
    """
    if not files:
        raise HTTPException(status_code=400, detail="没有收到文件")
    if len(files) > MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"一次最多上传 {MAX_FILES} 个文件（当前 {len(files)} 个）",
        )

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    batch_id = uuid.uuid4().hex[:8]
    total_size = 0

    saved, errors = [], []

    for idx, file in enumerate(files, 1):
        try:
            info = await _save_one(file)
        except HTTPException as e:
            errors.append({
                "original_name": _fix_filename(os.path.basename(file.filename or "")) or "(未知文件)",
                "reason": e.detail,
            })
            continue
        except Exception as e:
            logger.exception(f"处理上传文件失败: {file.filename}")
            errors.append({
                "original_name": os.path.basename(file.filename or ""),
                "reason": f"处理失败: {e}",
            })
            continue

        total_size += info["size"]
        if total_size > MAX_TOTAL_SIZE:
            # 超总量：把这一批已经落盘的文件删掉，避免留垃圾
            for s in saved:
                try:
                    os.remove(s["path"])
                except OSError:
                    pass
            raise HTTPException(
                status_code=400,
                detail=f"文件总大小超过 {MAX_TOTAL_SIZE // 1024 // 1024}MB，已取消本次上传",
            )

        # 统一前缀 + 序号，模型可用 glob 按批次读全
        unique_name = f"{batch_id}_{idx}_{info['original_name']}"
        filepath = os.path.join(UPLOAD_DIR, unique_name)
        with open(filepath, "wb") as f:
            f.write(info["content"])

        ext = os.path.splitext(info["original_name"])[1].lower()
        profile = _profile(info["content"], ext)
        columns, rows = profile.get("columns", []), profile.get("rows")

        saved.append({
            "filename": unique_name,
            "original_name": info["original_name"],
            "path": filepath,
            "size": info["size"],
            "columns": columns,
            "rows": rows,
            "profile": profile,          # 数据体检结果（类型/缺失值/离群值/告警）
        })

    if not saved and errors:
        raise HTTPException(
            status_code=400,
            detail="；".join(f"{e['original_name']}: {e['reason']}" for e in errors[:3]),
        )

    logger.info(
        f"📦 批量上传 [{batch_id}]: 成功 {len(saved)} 个"
        + (f"，失败 {len(errors)} 个" if errors else "")
        + f"，共 {total_size // 1024} KB"
    )

    return {
        "batch_id": batch_id,
        "count": len(files),
        "ok": len(saved),
        "failed": len(errors),
        "files": saved,
        "errors": errors,
    }
