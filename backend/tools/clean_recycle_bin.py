"""
================================================================
Windows 回收站工具 — clean_recycle_bin.py
================================================================
对接真实的 Windows 回收站（通过 winshell 库）。

支持操作：
- list：列出回收站中所有文件（原始文件名、大小、删除时间）
- empty：彻底清空回收站
- delete：彻底删除回收站中匹配名称的文件（支持精确匹配和模糊匹配）

安全策略：
- empty 和 delete 操作不可逆，后端会记录警告日志
- 文件名匹配防止误删（不支持通配符）
"""

import os
import sys
import logging

# 只在 Windows 上导入 winshell（Linux 不支持）
if sys.platform == "win32":
    import winshell

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "clean_recycle_bin",
        "description": (
            "管理 Windows 回收站。"
            "可以列出回收站中的文件，或永久删除/清空回收站。"
            "支持的操作：list（列出文件）、delete（删除指定文件）、empty（清空全部）"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "delete", "empty"],
                    "description": (
                        "'list' 列出回收站中的所有文件；"
                        "'delete' 永久删除指定的文件（输入匹配原始文件名的关键词）；"
                        "'empty' 彻底清空回收站（不可恢复，需谨慎）"
                    ),
                },
                "filenames": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要删除的文件名或关键词（action='delete' 时需要）。支持部分名称匹配。",
                },
            },
            "required": ["action"],
        },
    },
}


def _format_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.1f} MB"
    else:
        return f"{size_bytes / 1024 / 1024 / 1024:.2f} GB"


def _get_recycle_items() -> list:
    """获取回收站项目列表（跨平台）"""
    # Windows：使用 winshell 读取真实回收站
    if sys.platform == "win32":
        try:
            items = []
            for item in winshell.recycle_bin():
                try:
                    size = item.getsize()
                except Exception:
                    size = 0
                items.append({
                    "original_name": item.original_filename(),
                    "size_bytes": size,
                    "size": _format_size(size),
                })
            return items
        except Exception as e:
            logger.error(f"读取 Windows 回收站失败: {e}")
            return []

    # Linux：使用文件目录模拟回收站
    recycle_dir = _get_recycle_dir()
    items = []
    try:
        for entry in os.scandir(recycle_dir):
            if entry.is_file():
                stat = entry.stat()
                items.append({
                    "original_name": entry.path,
                    "size_bytes": stat.st_size,
                    "size": _format_size(stat.st_size),
                })
    except Exception as e:
        logger.error(f"读取回收站目录失败: {e}")
    return items


def execute(action: str, filenames: list[str] | None = None) -> dict:
    """执行回收站操作"""

    # ================================================================
    # list — 列出回收站内容
    # ================================================================
    if action == "list":
        try:
            items = _get_recycle_items()
            if not items:
                return {
                    "success": True,
                    "action": "list",
                    "message": "🎉 回收站是空的，没有文件。",
                    "file_count": 0,
                    "files": [],
                }

            total_size = sum(it["size_bytes"] for it in items)
            return {
                "success": True,
                "action": "list",
                "message": f"回收站中有 {len(items)} 个文件，共 {_format_size(total_size)}",
                "file_count": len(items),
                "total_size": _format_size(total_size),
                "files": items[:50],  # 最多返回前 50 个，防止响应过长
            }
        except Exception as e:
            logger.error(f"list 回收站失败: {e}")
            return {"success": False, "action": "list", "error": str(e)}

    # ================================================================
    # empty — 清空整个回收站
    # ================================================================
    if action == "empty":
        try:
            items = _get_recycle_items()
            count = len(items)

            if count == 0:
                return {"success": True, "action": "empty", "message": "回收站已经是空的", "deleted_count": 0}

            logger.warning(f"⚠️ 正在清空回收站，{count} 个文件将被永久删除！")
            if sys.platform == "win32":
                winshell.recycle_bin().empty(confirm=False)
            else:
                # Linux：清空回收站目录
                recycle_dir = _get_recycle_dir()
                for entry in os.scandir(recycle_dir):
                    if entry.is_file():
                        os.remove(entry.path)
            logger.warning(f"✅ 回收站已清空，共删除 {count} 个文件")

            return {
                "success": True,
                "action": "empty",
                "message": f"回收站已彻底清空，共永久删除 {count} 个文件（不可恢复）",
                "deleted_count": count,
            }
        except Exception as e:
            logger.error(f"empty 回收站失败: {e}")
            return {"success": False, "action": "empty", "error": f"清空失败: {e}"}

    # ================================================================
    # delete — 删除回收站中匹配的文件
    # ================================================================
    if action == "delete":
        if not filenames:
            return {"success": False, "action": "delete", "error": "请指定要删除的文件名"}

        try:
            # 先收集所有回收站项目
            all_items = []
            if sys.platform == "win32":
                for item in winshell.recycle_bin():
                    try:
                        all_items.append({
                            "original": item.original_filename(),
                            "real_path": item.real_filename(),
                            "info_path": item.real_filename().replace("$R", "$I"),
                        })
                    except Exception:
                        continue
            else:
                # Linux：从回收站目录收集文件
                recycle_dir = _get_recycle_dir()
                for entry in os.scandir(recycle_dir):
                    if entry.is_file():
                        all_items.append({
                            "original": entry.path,
                            "real_path": entry.path,
                            "info_path": None,
                        })

            deleted = []
            skipped = []

            for keyword in filenames:
                keyword_lower = keyword.lower().strip()
                found = False

                for entry in all_items:
                    if keyword_lower in os.path.basename(entry["original"]).lower():
                        # 永久删除：删除回收站中的 $R 数据文件和 $I 信息文件
                        try:
                            if os.path.exists(entry["real_path"]):
                                os.remove(entry["real_path"])
                        except Exception as e:
                            logger.warning(f"删除 {entry['real_path']} 失败: {e}")
                        if entry.get("info_path"):
                            try:
                                if os.path.exists(entry["info_path"]):
                                    os.remove(entry["info_path"])
                            except Exception:
                                pass
                        deleted.append(entry["original"])
                        logger.info(f"已从回收站永久删除: {entry['original']}")
                        found = True

                if not found:
                    skipped.append(keyword)

            parts = []
            if deleted:
                parts.append(f"成功永久删除 {len(deleted)} 个文件")
            if skipped:
                parts.append(f"未找到匹配 '{skipped[0]}' 的文件" if len(skipped) == 1
                             else f"{len(skipped)} 个关键词未匹配到文件")

            return {
                "success": True,
                "action": "delete",
                "message": "；".join(parts),
                "deleted": deleted,
                "skipped": skipped,
            }
        except Exception as e:
            logger.error(f"delete 回收站文件失败: {e}")
            return {"success": False, "action": "delete", "error": str(e)}

    return {"success": False, "error": f"未知操作: '{action}'。支持 list / delete / empty"}
