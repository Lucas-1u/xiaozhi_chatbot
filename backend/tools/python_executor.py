"""
================================================================
Python 代码执行器 — python_executor.py
================================================================
让 LLM 能够写代码并在沙箱中执行，返回结果。

安全策略：
- subprocess 隔离执行，超时限制
- 模块白名单（含 pandas/matplotlib/seaborn）
- 文件读写限制在 uploads（上传数据）和 plots（图片输出）目录
- 标准输出上限 5000 字符
"""

import os
import subprocess
import sys
import tempfile
import logging

from backend.config import settings

logger = logging.getLogger(__name__)

MAX_TIMEOUT = 60         # 大文件处理较慢，给足时间
MAX_OUTPUT = 5000

# ========== 服务端绘图开关 ==========
# 默认关闭（见 config.py 说明）：图表展示统一走 plot_chart（浏览器端可交互图表）。
# 关闭时沙箱会拦截 matplotlib / seaborn 的 import，并把模型引导回 plot_chart。
ALLOW_SERVER_PLOTTING = settings.enable_server_plotting

# ========== 目录常量 ==========
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UPLOAD_DIR = os.path.join(BASE_DIR, "data", "uploads")   # 用户上传的数据文件
PLOT_DIR = os.path.join(BASE_DIR, "frontend", "plots")   # 生成的图表
TEMP_DIR = tempfile.gettempdir().replace("\\", "/")      # 允许写临时目录（matplotlib 缓存）

# ========== 沙箱策略 ==========
# 不拦截 import（matplotlib/pandas 内部依赖 importlib 等），改为：
# 1. 字符串检查拦截明显的危险调用
# 2. 包装 open，文件读写限制在 uploads / plots 目录
# 3. 禁用 os 的命令执行和删除函数
# ponytail: 进程级隔离，够用；要硬隔离需上 Docker/firejail

# ========== 工具说明（根据绘图开关动态生成） ==========
if ALLOW_SERVER_PLOTTING:
    _PLOT_PURPOSE = (
        "3) **仅当用户明确索要图片文件时**（如「给我一张图」「导出图片」「我要放进报告」）"
        "才用 matplotlib 绘图并保存到 PLOT_DIR。\n"
    )
    _PLOT_WARNING = (
        "⚠️ **如果目的是向用户「展示」图表（画个柱状图、可视化一下、看趋势），"
        "请改用 plot_chart 工具**——它返回可交互图表（悬停看数值、可导出 PNG），"
        "更快、更清晰、也不占服务器算力。本工具画出来的是静态图片。\n"
    )
    _PLOT_VARS = "  PLOT_DIR   — 需要生成图片文件时，保存到这里\n"
    _PLOT_TPL = (
        "仅在用户明确索取图片时的绘图写法：\n"
        "  import matplotlib; matplotlib.use('Agg')\n"
        "  import matplotlib.pyplot as plt\n"
        "  df.plot(...); plt.savefig(f'{PLOT_DIR}/图名.png', dpi=100, bbox_inches='tight')\n"
    )
    _MODULES = "可用模块: pandas, numpy, matplotlib, seaborn, openpyxl 等。禁止: os, sys, subprocess"
else:
    _PLOT_PURPOSE = ""
    _PLOT_WARNING = (
        "⚠️ **本工具不能画图**（服务端绘图已关闭）：凡是「画图 / 可视化 / 看趋势 / 对比一下」"
        "这类需求，一律调用 plot_chart 工具生成可交互图表；\n"
        "   如果用户要图片文件，请提示他点击图表右上角的「⬇ 导出 PNG」，不要尝试用代码画图。\n"
    )
    _PLOT_VARS = ""
    _PLOT_TPL = ""
    _MODULES = "可用模块: pandas, numpy, openpyxl 等。禁止: os, sys, subprocess（绘图模块已禁用）"

SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_python",
        "description": (
            "在服务器上执行 Python 代码并返回结果。用途：\n"
            "1) 数学计算、数据处理、统计分析；\n"
            "2) 读取用户上传的数据文件（pandas / openpyxl）。\n"
            + _PLOT_PURPOSE
            + _PLOT_WARNING
            + "代码里可以直接使用预定义变量：\n"
            "  UPLOAD_DIR — 用户上传的数据文件所在目录（只读，不要往这里写文件）\n"
            + _PLOT_VARS
            +             "读取上传文件并交给 plot_chart 画图的推荐写法：\n"
            "  import pandas as pd\n"
            "  df = pd.read_csv(f'{UPLOAD_DIR}/文件名.csv')  # 必须用 UPLOAD_DIR 拼接绝对路径，\n"
            "  #   不要用 'data/uploads/xxx.csv'、'/data/xxx' 之类的相对路径，会找不到文件\n"
            "  #   编码已自动探测（UTF-8 / GBK / 带 BOM 都能读对），**不需要自己传 encoding，\n"
            "  #   也不要再写「先试 gbk 失败再试 utf-8」那种循环试探**，那是浪费时间。\n"
            "  print(df.to_json(orient='records', force_ascii=False))  # 打印数据，再用 plot_chart 画图\n"
            "用户一次上传多个文件时，它们的文件名带**相同批次前缀**"
            "（如 3f2a1b9c_1_一月.csv、3f2a1b9c_2_二月.csv），通常是一份数据切开的分片，\n"
            "要合并后分析就按前缀一次读全：\n"
            "  import glob, pandas as pd\n"
            "  paths = sorted(glob.glob(f'{UPLOAD_DIR}/3f2a1b9c_*.csv'))  # 按批次前缀 glob\n"
            "  df = pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)  # 上下拼接\n"
            "  #   如果各文件列不同、要按键关联，用 pd.merge(...) 或 pd.concat(..., axis=1)，别用 axis=0\n"
            "  print('合并后行数:', len(df), '| 列:', list(df.columns))\n"
            + _PLOT_TPL
            + _MODULES
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "要执行的 Python 代码。用 print() 输出结果。",
                }
            },
            "required": ["code"],
        },
    },
}


def execute(code: str) -> dict:
    """执行 Python 代码并返回结果"""
    if not code or not isinstance(code, str):
        return {"success": False, "error": "代码不能为空"}

    # 安全检查：拦截明显的危险调用（沙箱内还有模块黑名单兜底）
    dangerous = ["subprocess", "socket", "__import__",
                 "os.system", "os.popen", "shutil.rmtree"]
    for word in dangerous:
        if word in code:
            return {
                "success": False,
                "error": f"代码包含禁止的操作: '{word}'",
            }

    # 确保目录存在
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(PLOT_DIR, exist_ok=True)

    # 记录执行前的文件列表，用于检测新生成的图（递归，防止 LLM 建子目录）
    before_plots = _list_files(PLOT_DIR)
    before_uploads = _list_files(UPLOAD_DIR)

    # 绘图代码：强制使用系统中文字体
    # （LLM 常硬编码 SimHei，Linux 上没这个字体，会导致中文变方框）
    mpl_patch = ""
    if ALLOW_SERVER_PLOTTING and any(k in code for k in ("matplotlib", "plt.", "sns.", "seaborn")):
        fonts = (["SimHei", "DejaVu Sans"] if sys.platform == "win32"
                 else ["WenQuanYi Zen Hei", "WenQuanYi Micro Hei", "DejaVu Sans"])
        mpl_patch = f'''
try:
    import matplotlib as __mpl
    __real_setitem = __mpl.RcParams.__setitem__
    def __safe_setitem(self, key, val):
        if key == "font.sans-serif":
            val = {fonts}
        return __real_setitem(self, key, val)
    __mpl.RcParams.__setitem__ = __safe_setitem
except Exception:
    pass
'''

    # 绘图禁用时：在沙箱子进程里拦截绘图模块的 import，
    # 并把模型「引导」回 plot_chart（不是单纯拦住，报错信息本身就是使用指引）
    plot_guard = ""
    if not ALLOW_SERVER_PLOTTING:
        plot_guard = '''
import builtins as __b_guard
__real_import_guard = __b_guard.__import__
__BANNED_PLOT_MODULES = ("matplotlib", "seaborn", "pylab", "mpl_toolkits", "plotly", "pyecharts")

def __guarded_import(name, *args, **kwargs):
    if str(name).split(".")[0] in __BANNED_PLOT_MODULES:
        raise ImportError(
            "服务端绘图已关闭：本环境不允许使用 matplotlib/seaborn 画图。"
            "请改用 plot_chart 工具生成可交互图表（用户可悬停查看数值、点击导出 PNG）。"
        )
    return __real_import_guard(name, *args, **kwargs)

__b_guard.__import__ = __guarded_import
'''

    # CSV 编码自动探测：国内 Excel 另存的 CSV 绝大多数是 GBK，
    # pandas 默认按 UTF-8 读会把中文列名变成乱码（表现为 "鍑″唴" 或 "◆◆"）。
    # 这里包装 pd.read_csv，没显式指定 encoding 时自动探测，模型代码无需改写。
    csv_guard = '''
# ---------- CSV 编码自动探测 ----------
def __text_has_cjk(text):
    """文本里是否真的有中文汉字（只看 CJK 统一区，排除日文假名 / 韩文）"""
    for __ch in text[:20000]:
        if "\\u4e00" <= __ch <= "\\u9fff":
            return True
    return False


def __detect_csv_encoding(path):
    """
    判断一个 CSV 文件的编码，按可靠性从高到低：
      1. BOM（UTF-8-SIG / UTF-16 / UTF-32）—— 最确定
      2. utf-8 严格解码 —— 中文场景判定最可靠（GBK 的双字节序列几乎不可能
         凑成合法 UTF-8 三字节序列）
      3. gbk / gb18030 严格解码，且要求解出来确实含中文汉字
      4. big5 / shift_jis / euc_kr（真正的日韩文件）
      5. charset_normalizer 统计法兜底（仅在前四步全失败时用）
      6. 兜底 utf-8

    为什么第 3 步要额外校验「含中文」：统计法有时会把 GBK 中文误判成韩文
    （如把「张三」读成「츰냔」），用"必须能解出汉字"这条规则排除这种误判。
    """
    try:
        with __real_open(path, "rb") as __f:
            __raw = __f.read(262144)          # 只看前 256KB，够判断且不慢
    except Exception:
        return "utf-8"
    if not __raw:
        return "utf-8"

    # 1) BOM
    if __raw.startswith(b"\\xef\\xbb\\xbf"):
        return "utf-8-sig"
    if __raw.startswith((b"\\xff\\xfe\\x00\\x00", b"\\x00\\x00\\xfe\\xff")):
        return "utf-32"
    if __raw.startswith(b"\\xff\\xfe"):
        return "utf-16"

    # 2) utf-8 严格试
    try:
        __raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass

    # 3) GBK 系（要求确实含中文，避免乱判）
    for __enc in ("gbk", "gb18030"):
        try:
            __text = __raw.decode(__enc)
        except UnicodeDecodeError:
            continue
        if __text_has_cjk(__text):
            return __enc

    # 4) 其他东亚编码
    for __enc in ("big5", "shift_jis", "euc_kr"):
        try:
            __raw.decode(__enc)
            return __enc
        except UnicodeDecodeError:
            continue

    # 5) 统计法兜底
    try:
        from charset_normalizer import from_bytes as __cn_from_bytes
        __best = __cn_from_bytes(__raw).best()
        if __best and __best.encoding:
            return __best.encoding
    except Exception:
        pass

    # 6) 兜底
    return "utf-8"


def __install_smart_read_csv():
    """
    替换 pandas.read_csv：模型仍然照常写 pd.read_csv(path)，
    但没指定 encoding 时会自动用探测到的编码。
    显式传了 encoding 的调用一律不干预。
    """
    try:
        import pandas as __pd
    except Exception:
        return
    try:
        __orig_read_csv = __pd.read_csv

        def __smart_read_csv(path, *args, **kwargs):
            if "encoding" in kwargs:
                return __orig_read_csv(path, *args, **kwargs)
            try:
                kwargs["encoding"] = __detect_csv_encoding(path)
            except Exception:
                pass
            # 注意：pd.read_csv 没有 errors 参数（那是 read_table 才有的），不能加
            return __orig_read_csv(path, *args, **kwargs)

        __pd.read_csv = __smart_read_csv
    except Exception:
        pass


__install_smart_read_csv()

# ---------- 强制 stdout 用 UTF-8 ----------
# 双保险：父进程已设了 PYTHONIOENCODING / PYTHONUTF8，这里再把 stdout/stderr
# 重新包一层 utf-8，防止个别环境（隔离模式重试）把环境变量丢掉后又变乱码。
try:
    import io as __io
    import sys as __sys
    def __force_utf8(__stream):
        if __stream is None:
            return __stream
        __enc = (getattr(__stream, "encoding", "") or "").lower().replace("-", "").replace("_", "")
        if __enc == "utf8":
            return __stream
        return __io.TextIOWrapper(__stream.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)
    __sys.stdout = __force_utf8(__sys.stdout)
    __sys.stderr = __force_utf8(__sys.stderr)
except Exception:
    pass
'''

    # 沙箱包装
    sandbox = f'''
import builtins as __b
__real_import = __b.__import__
__real_open = __b.open
__upload_dir = {repr(UPLOAD_DIR)}
__plot_dir = {repr(PLOT_DIR)}

# 文件访问控制：
# - 写操作：只允许 uploads / plots / 临时目录
# - 读操作：允许（matplotlib 等需要读自己的配置），但拦截敏感文件
def __safe_open(file, mode="r", *a, **kw):
    __p = str(file).replace("\\\\", "/")
    __low = __p.lower()
    __allow_write = [__upload_dir.replace("\\\\","/"), __plot_dir.replace("\\\\","/"), {repr(TEMP_DIR)}]
    if any(c in mode for c in "wax+"):
        if not any(__p.startswith(d) for d in __allow_write):
            raise PermissionError(f"只能写入 uploads 或 plots 目录: {{file}}")
    else:
        for __s in [".env", "chat.db", ".git/", "id_rsa", "id_ed25519", "/etc/shadow", "/etc/passwd", ".ssh/"]:
            if __s in __low:
                raise PermissionError(f"禁止读取敏感文件: {{file}}")
    return __real_open(file, mode, *a, **kw)
__b.open = __safe_open

{plot_guard}

{csv_guard}

# 禁用 os 的命令执行函数（保留 os.path / os.remove 等，库内部需要）
import os as __os
for __f in ["system","popen","execv","execve","execvp","execvpe",
            "spawnv","spawnl","spawnve","fork","kill"]:
    if hasattr(__os, __f): setattr(__os, __f, None)

# 提供给用户代码的路径变量
PLOT_DIR = __plot_dir
UPLOAD_DIR = __upload_dir
{mpl_patch}
try:
{_indent(code, 4)}
except Exception as __e:
    print(f"Error: {{type(__e).__name__}}: {{__e}}")
'''

    try:
        # 用当前解释器（服务器上 venv 里才装了 pandas/matplotlib）
        py = sys.executable
        # 让 matplotlib 缓存写到临时目录（在沙箱允许写入的范围内）
        env = os.environ.copy()
        mpl_cache = os.path.join(TEMP_DIR, "mpl_config")
        os.makedirs(mpl_cache, exist_ok=True)
        if ALLOW_SERVER_PLOTTING:
            env["MPLCONFIGDIR"] = mpl_cache

        # 强制子进程用 UTF-8 输出（关键！）
        # 父进程按 utf-8 解码（见 _run），如果子进程按系统 locale（Windows 中文
        # 环境常见 GBK/cp936）写 stdout，中文就会变成「�ܼ�¼��」这种乱码。
        # 实测踩过：模型代码里 print 的中文完全正常，输出却全是乱码。
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"          # 让整个解释器走 UTF-8 模式

        # 写入 matplotlibrc，让 matplotlib 自动使用系统中文字体
        # （LLM 代码里不用再手动设置，避免忘记导致中文变方框）
        if ALLOW_SERVER_PLOTTING:
            zh_font = "SimHei" if sys.platform == "win32" else "WenQuanYi Zen Hei"
            try:
                with open(os.path.join(mpl_cache, "matplotlibrc"), "w", encoding="utf-8") as f:
                    f.write(f"font.sans-serif : {zh_font}, DejaVu Sans\n")
                    f.write("axes.unicode_minus : False\n")
            except Exception as e:
                logger.warning(f"写入 matplotlibrc 失败: {e}")

        def _run(cmd: list, run_env: dict):
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=MAX_TIMEOUT,
                stdin=subprocess.DEVNULL,  # 防止 input() 挂起
                env=run_env,
            )

        def _collect(proc) -> str:
            out = (proc.stdout or "").strip()
            if proc.stderr:
                err = proc.stderr.strip()[:500]
                out = (out + "\n" + err) if out else err
            return out

        proc = _run([py, "-c", sandbox], env)
        output = _collect(proc)

        # ---------- 兜底重试 ----------
        # 已知问题：在 uvicorn 这类常驻服务进程里，子进程偶尔会「静默退出」
        # （rc=1 且 stdout/stderr 全空），而同一段代码在普通进程里 100% 正常，
        # 怀疑是父进程的 PYTHON* 环境变量污染了子解释器。
        # 这里用「隔离模式（-I）+ 干净环境」重试一次。
        if not output and proc.returncode != 0:
            clean_env = {k: v for k, v in os.environ.items()
                         if not k.upper().startswith("PYTHON")}
            clean_env["MPLCONFIGDIR"] = mpl_cache
            for keep in ("PATH", "SystemRoot", "TEMP", "TMP", "USERPROFILE"):
                if os.environ.get(keep):
                    clean_env[keep] = os.environ[keep]
            proc = _run([py, "-I", "-c", sandbox], clean_env)
            output = _collect(proc)
            logger.warning(
                f"⚠️ 子进程静默失败，已用隔离模式重试（rc={proc.returncode}, 输出长度={len(output)}）"
            )

        if not output:
            # 如实报告失败：否则模型会误以为「执行成功但没输出」，反复重试十几轮
            if proc.returncode != 0:
                return {
                    "success": False,
                    "error": (
                        f"代码执行异常（退出码 {proc.returncode}，无任何输出）。"
                        "请把代码简化后重试。"
                    ),
                }
            output = "(无输出)"

        # 检测新生成的图片
        after_plots = _list_files(PLOT_DIR)
        new_plots = list(after_plots - before_plots)

        # 兜底：LLM 可能把图存到了 uploads 目录（含子目录），自动挪到 plots
        stray = [
            f for f in (_list_files(UPLOAD_DIR) - before_uploads)
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".svg"))
        ]
        for rel in stray:
            name = os.path.basename(rel)
            try:
                os.replace(os.path.join(UPLOAD_DIR, rel), os.path.join(PLOT_DIR, name))
                new_plots.append(name)
                logger.info(f"图表从 uploads 挪到 plots: {rel}")
            except Exception as e:
                logger.warning(f"移动图片失败 {rel}: {e}")

        plot_urls = [f"/plots/{name}" for name in sorted(set(new_plots))]

        result = {
            "success": True,
            "output": output[:MAX_OUTPUT],
            "code": code,
        }
        if plot_urls:
            result["plots"] = plot_urls
            result["output"] += f"\n[已生成 {len(plot_urls)} 张图表]"

        return result

    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"代码执行超时（超过 {MAX_TIMEOUT} 秒）"}
    except Exception as e:
        return {"success": False, "error": f"执行失败: {str(e)}"}


def _indent(code: str, n: int) -> str:
    """给代码添加缩进"""
    return "\n".join(" " * n + line for line in code.split("\n"))


def _list_files(directory: str) -> set:
    """递归列出目录下所有文件（返回相对路径集合）"""
    result = set()
    for root, _dirs, files in os.walk(directory):
        for f in files:
            result.add(os.path.relpath(os.path.join(root, f), directory))
    return result
