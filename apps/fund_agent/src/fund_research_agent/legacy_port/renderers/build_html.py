"""生成基金深度分析报告 HTML 页面
图表JS使用模板替换而非f-string，避免花括号转义地狱
"""
import argparse, json, math, os, re, sys
from datetime import date
from functools import lru_cache
from html import escape
from urllib.parse import quote

from fund_research_agent.legacy_port.renderers.markdown_cleanup import (
    collapse_repeated_summary_terms,
    strip_heading_placeholder_brackets,
)

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.abspath(__file__))
EXPORT_WORD_API = "../../api/export-word"
REPORT_DATA_CUTOFF_DATE = None


def _relative_output_url(out_html, target_path):
    """Return a browser-safe URL from an emitted HTML file to a project asset."""
    start = os.path.abspath(os.path.dirname(out_html))
    target = os.path.abspath(target_path)
    return os.path.relpath(target, start=start).replace(os.sep, "/")


def _format_cn_date(date_value):
    if not date_value:
        return ""
    return f"{date_value.year}年{date_value.month}月{date_value.day}日"


def load_nav_index_cutoff_date(nav_dir_override=None):
    """Return the configured NAV/index cutoff date, capped by available source data."""
    try:
        import pandas as pd
    except ModuleNotFoundError:
        return None
    nav_dir = nav_dir_override or os.path.join(ROOT, "深度分析素材", "净值与指数数据")
    nav_fp = os.path.join(nav_dir, "基金复权净值数据.csv")
    idx_fp = os.path.join(nav_dir, "000300_SH.csv")
    if not os.path.exists(nav_fp) or not os.path.exists(idx_fp):
        return None
    try:
        nav_dates = pd.to_datetime(pd.read_csv(nav_fp, encoding="utf-8-sig", usecols=["日期"])["日期"]).dropna()
        idx_dates = pd.to_datetime(pd.read_csv(idx_fp, encoding="utf-8-sig", usecols=["date"])["date"]).dropna()
    except Exception:
        return None
    if nav_dates.empty or idx_dates.empty:
        return None
    return min(nav_dates.max(), idx_dates.max()).date()


def configured_report_funds(current_fund_name=""):
    """Return generated report pages that can be reached from the fund switcher."""
    names = []
    visible_names = []
    cfg_path = os.path.join(ROOT, "config.yaml")
    if os.path.exists(cfg_path):
        try:
            import yaml

            cfg = yaml.safe_load(open(cfg_path, "r", encoding="utf-8")) or {}
            visible_names = [str(name).strip() for name in cfg.get("web_visible_funds", []) if str(name).strip()]
            configured_funds = [{"name": name} for name in visible_names] if visible_names else cfg.get("funds", [])
            for item in configured_funds:
                name = str(item.get("name", "")).strip()
                if name and name not in names:
                    names.append(name)
        except Exception:
            text = open(cfg_path, "r", encoding="utf-8", errors="ignore").read()
            for match in re.finditer(r'^\s*-\s+name:\s*["\']?(.+?)["\']?\s*$', text, re.M):
                name = match.group(1).strip()
                if name and name not in names:
                    names.append(name)

    output_dir = os.path.join(ROOT, "output")
    if os.path.isdir(output_dir) and not visible_names:
        for name in sorted(os.listdir(output_dir)):
            if name == "基金经理合集":
                continue
            if os.path.exists(os.path.join(output_dir, name, "report.html")) and name not in names:
                names.append(name)

    if current_fund_name and current_fund_name not in names and not visible_names:
        names.insert(0, current_fund_name)

    options = []
    for name in names:
        report_path = os.path.join(output_dir, name, "report.html")
        if not os.path.exists(report_path) and name != current_fund_name:
            continue
        data_dir = os.path.join(ROOT, "深度分析素材", "按Part拆分数据", name)
        _code, fund_short_name, manager, _op_range = get_fund_info(data_dir)
        if not manager or not fund_short_name:
            manager, _, fund_short_name = name.partition("_")
        switch_key = report_switch_key(name)
        fund_id = assistant_fund_id(name)
        options.append(
            {
                "key": switch_key,
                "fund_id": fund_id,
                "fund_name": name,
                "manager": manager or name,
                "fund": fund_short_name or name,
                "label": f"{manager or name} · {fund_short_name or name}",
                "url": f"/output/{quote(name, safe='')}/report.html",
                "active": name == current_fund_name,
            }
        )
    return options


def fund_switcher_html(current_fund_name):
    options = configured_report_funds(current_fund_name)
    if len(options) <= 1:
        return "", "[]"
    buttons = []
    for item in options:
        active = " active" if item["active"] else ""
        buttons.append(
            f'<button class="manager-option{active}" type="button" data-key="{escape(item["key"])}" '
            f'onclick="selectReportFund(\'{escape(item["key"])}\')">'
            f'<span>{escape(item["label"])}</span><small>{escape(item["fund"])}</small></button>'
        )
    current = next((item for item in options if item["active"]), options[0])
    html_block = f"""
  <div class="manager-filter" id="manager-filter">
    <button class="manager-toggle" type="button" onclick="toggleManagerDropdown()" aria-expanded="false">
      <span><strong id="manager-toggle-name">{escape(current["label"])}</strong></span>
      <span class="manager-caret">▾</span>
    </button>
    <div class="manager-select">
      <input class="manager-search" id="manager-search" type="text" value="" placeholder="搜索基金经理或基金简称" autocomplete="off" onfocus="openManagerDropdown()" oninput="filterManagers(this.value)" onkeydown="handleManagerSearchKey(event)">
      <div class="manager-options" id="manager-options">
        {"".join(buttons)}
      </div>
    </div>
  </div>"""
    return html_block, json.dumps(options, ensure_ascii=False)


def assistant_fund_id(fund_name):
    """Return the AI assistant fund id without making report generation depend on the module."""
    try:
        from ai_assistant.knowledge_base.discovery import slugify
        return slugify(fund_name)
    except Exception:
        return re.sub(r"\W+", "_", fund_name, flags=re.UNICODE).strip("_")


def report_switch_key(fund_name):
    """Return a unique front-end key for report switching."""
    return quote(fund_name, safe="")


def pct(val):
    if val is None or str(val).strip() in {"", "—", "暂无数据", "None"}:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        return float(val.replace("%", "").replace("％", "").replace("−", "-"))
    return 0.0


def safe_float(val, default=0.0):
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val.replace("%", "").replace("−", "-").replace("，", "").strip())
        except ValueError:
            return default
    return default


def clean_metric_value(val, default="—"):
    if val is None:
        return default
    if isinstance(val, str):
        s = val.strip()
        return s if s else default
    return val


def format_pct_metric(val, default="—", signed=False, decimals=1):
    val = clean_metric_value(val, default)
    if val == default:
        return default
    if isinstance(val, str) and not re.search(r"-?\d+(?:\.\d+)?", val):
        return val
    num = safe_float(val, None)
    if num is None:
        return str(val)
    display_num = num if signed else abs(num)
    if decimals == 0:
        rounded = int(display_num + 0.5) if display_num >= 0 else -int(abs(display_num) + 0.5)
        return f"{rounded}%"
    if signed:
        return f"{num:.{decimals}f}%"
    return f"{abs(num):.{decimals}f}%"


def format_month_metric(val, default="—"):
    val = clean_metric_value(val, default)
    if val == default:
        return default
    if isinstance(val, str) and "未修复" in val:
        return "未修复"
    num = safe_float(val, None)
    if num is None:
        return str(val)
    return f"{int(round(num))}个月"


def format_drawdown_range(val, default="—"):
    val = clean_metric_value(val, default)
    if val == default:
        return default
    text = str(val).strip()
    match = re.match(
        r"^\s*(\d{4})年\s*(\d{1,2})月\s*[-~至]+\s*(\d{4})年\s*(\d{1,2})月\s*$",
        text,
    )
    if match:
        y1, m1, y2, m2 = match.groups()
        return f"{y1}.{int(m1)}-{y2}.{int(m2)}"
    return text


# 大类板块调色板（chart_part4 / chart_part7 共用）
SECTOR_PALETTE = {
    "制造": "#58a6ff", "消费": "#d29922", "TMT": "#a371f7",
    "周期": "#3fb950", "金融": "#f85149", "医药": "#f0883e",
    "能源": "#79c0ff", "军工": "#ffa657", "地产": "#7ee787",
    "公用": "#d2a8ff", "交运": "#a5d6ff",
}
_FALLBACK_COLORS = ["#8b949e", "#c9d1d9", "#b392f0", "#f778ba", "#56d4dd"]


def sector_color(name, idx=0):
    if name in SECTOR_PALETTE:
        return SECTOR_PALETTE[name]
    return _FALLBACK_COLORS[idx % len(_FALLBACK_COLORS)]


def load_json(data_dir, fn):
    fp = os.path.join(data_dir, fn)
    if not os.path.exists(fp):
        print(f"  [警告] 缺少 {fn}，跳过")
        return {}
    with open(fp, "r", encoding="utf-8") as f:
        return json.load(f)


def load_private_context_compact(fund_name):
    fp = os.path.join(ROOT, "深度分析素材", "按Part拆分数据", fund_name, "private_context_compact.json")
    if not os.path.exists(fp):
        return {}
    try:
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  [private_context] 警告: {fund_name} 私募素材读取失败: {exc}")
        return {}


def private_holdings_matrix_html(fund_name):
    private_context = load_private_context_compact(fund_name)
    matrix = private_context.get("holdings_matrix", {})
    periods = matrix.get("periods", []) if isinstance(matrix, dict) else []
    rows = matrix.get("rows", []) if isinstance(matrix, dict) else []
    if not periods or not rows:
        return ""

    lines = [
        '<div class="private-holdings-block">',
        '<div class="private-performance-title">私募阶段持仓金额矩阵</div>',
        '<div class="private-holdings-scroll">',
        '<table class="metrics-table private-holdings-table">',
        '<thead><tr><th>个股</th>',
    ]
    lines.extend(f"<th>{escape(str(period))}</th>" for period in periods)
    lines.append("</tr></thead><tbody>")
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("股票简称", "")).strip()
        if not name:
            continue
        lines.append(f"<tr><td>{escape(name)}</td>")
        for period in periods:
            value = str(row.get(period, "")).strip()
            lines.append(f"<td>{escape(value) if value else '—'}</td>")
        lines.append("</tr>")
    lines.extend(["</tbody></table>", "</div>", '<div class="private-holdings-note">单位：亿元；同一股票同一报告期存在多条披露时按持股市值合并。</div>', "</div>"])
    return "".join(lines)


def load_industry_members():
    """加载申万一级行业成分股映射，返回 {行业名: set(个股名)}"""
    try:
        import pandas as pd
    except ModuleNotFoundError:
        print("  [行业映射] 警告: 缺少 pandas，跳过申万行业成分股映射")
        return {}
    mem_dir = os.path.join(ROOT, "深度分析素材", "净值与指数数据", "sw_industry_members")
    if not os.path.isdir(mem_dir):
        return {}
    mapping = {}
    for fn in os.listdir(mem_dir):
        if not fn.endswith(".csv"):
            continue
        fp = os.path.join(mem_dir, fn)
        try:
            df = pd.read_csv(fp, encoding="utf-8-sig", usecols=["l1_name", "name"])
            for _, row in df.iterrows():
                ind = row["l1_name"]
                name = row["name"]
                # 去掉退市标记和多余后缀
                name = re.sub(r"\(退市\)$", "", str(name))
                name = re.sub(r"^(?:S)?(?:\*)?ST", "", name)
                if ind not in mapping:
                    mapping[ind] = set()
                mapping[ind].add(name)
                # 也存原名（带ST等），因为盈亏数据中的个股名可能含这些前缀
                orig = str(row["name"])
                if orig != name:
                    mapping[ind].add(orig)
        except Exception:
            continue
    return mapping


def load_nav_and_index(fund_name, start_year=2016, nav_dir_override=None):
    """加载基金复权净值 + 沪深300收盘价，从 start_year 起。
    返回 (dates, fund_raw_nav, idx_raw_close) 三个列表（原始值，归一化由前端 JS 按可见区间动态计算）。
    """
    try:
        import pandas as pd
    except ModuleNotFoundError:
        print("  [chart_part3b] 警告: 缺少 pandas，跳过净值回撤图")
        return None, None, None
    nav_dir = nav_dir_override or os.path.join(ROOT, "深度分析素材", "净值与指数数据")

    # 基金净值
    fund_col = fund_name.split("_", 1)[1] if "_" in fund_name else fund_name
    nav_fp = os.path.join(nav_dir, "基金复权净值数据.csv")
    if not os.path.exists(nav_fp):
        print("  [chart_part3b] 警告: 缺少基金复权净值数据.csv，跳过净值回撤图")
        return None, None, None
    df_fund = pd.read_csv(nav_fp, encoding="utf-8-sig", usecols=["日期", fund_col])
    df_fund = df_fund.dropna(subset=[fund_col]).drop_duplicates("日期", keep="last")
    df_fund["日期"] = pd.to_datetime(df_fund["日期"])
    df_fund = df_fund[df_fund["日期"].dt.year >= start_year].sort_values("日期").reset_index(drop=True)
    if df_fund.empty:
        print(f"  [chart_part3b] 警告: {fund_col} 在 {start_year} 年后无数据")
        return None, None, None

    # 沪深300（新格式：symbol, name, fetch_time, date, close）
    idx_fp = os.path.join(nav_dir, "000300_SH.csv")
    df_idx = pd.read_csv(idx_fp, encoding="utf-8-sig", usecols=["date", "close"])
    df_idx["date"] = pd.to_datetime(df_idx["date"])
    df_idx = df_idx.drop_duplicates("date", keep="last")
    df_idx = df_idx[df_idx["date"].dt.year >= start_year].sort_values("date").reset_index(drop=True)

    # 对齐到交易日交集
    df_fund = df_fund.rename(columns={"日期": "date", fund_col: "nav"})
    df_idx = df_idx.rename(columns={"close": "idx"})
    merged = pd.merge(df_fund[["date", "nav"]], df_idx[["date", "idx"]], on="date", how="inner")

    if len(merged) < 10:
        print("  [chart_part3b] 警告: 对齐后数据点不足，跳过")
        return None, None, None

    cutoff_date = load_nav_index_cutoff_date(nav_dir_override)
    if cutoff_date:
        merged = merged[merged["date"] <= pd.Timestamp(cutoff_date)].reset_index(drop=True)

    dates = merged["date"].dt.strftime("%Y-%m-%d").tolist()
    fund_nav = merged["nav"].round(6).tolist()
    idx_close = merged["idx"].round(4).tolist()
    return dates, fund_nav, idx_close


def _filter_yearly_returns(raw_returns, start_year=2016, end_year=None):
    returns = {}
    for year, value in (raw_returns or {}).items():
        year_text = str(year).replace("年收益", "").replace("年", "").strip()
        if not year_text.isdigit():
            continue
        year_int = int(year_text)
        if year_int < start_year or (end_year is not None and year_int > end_year):
            continue
        num = safe_float(value, None)
        if num is None or not math.isfinite(num):
            continue
        returns[str(year_int)] = round(float(num), 2)
    return returns


def load_hs300_yearly_returns(start_year=2016, end_year=None, nav_dir_override=None):
    nav_dir = nav_dir_override or os.path.join(ROOT, "深度分析素材", "净值与指数数据")
    derived_fp = os.path.join(nav_dir, "指数年度收益.json")
    if os.path.exists(derived_fp):
        try:
            with open(derived_fp, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            annual = (data.get("沪深300") or {}).get("annual_returns", {})
            returns = _filter_yearly_returns(annual, start_year=start_year, end_year=end_year)
            if returns:
                return returns
        except Exception as exc:
            print(f"  [chart_part3] 警告: 指数年度收益.json 读取失败，回退CSV: {exc}")

    try:
        import pandas as pd
    except ModuleNotFoundError:
        print("  [chart_part3] 警告: 缺少 pandas，跳过沪深300年度收益")
        return {}
    idx_fp = os.path.join(nav_dir, "000300_SH.csv")
    if not os.path.exists(idx_fp):
        print("  [chart_part3] 警告: 缺少 000300_SH.csv，跳过沪深300年度收益")
        return {}
    df = pd.read_csv(idx_fp, encoding="utf-8-sig", usecols=["date", "close"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    max_year = int(df["date"].dt.year.max()) if end_year is None else int(end_year)
    returns = {}
    for year in range(int(start_year), max_year + 1):
        base = df[df["date"] < pd.Timestamp(year=year, month=1, day=1)]
        current = df[df["date"].dt.year == year]
        if base.empty or current.empty:
            continue
        first = float(base.iloc[-1]["close"])
        last = float(current.iloc[-1]["close"])
        if first and math.isfinite(first) and math.isfinite(last):
            returns[str(year)] = round((last / first - 1) * 100, 2)
    return returns


def find_report(output_dir, fund_name):
    candidates = [
        os.path.join(output_dir, fund_name, "report.md"),
        os.path.join(output_dir, f"完整报告_v2_{fund_name}.md"),
        os.path.join(output_dir, f"完整报告_{fund_name}.md"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    raise FileNotFoundError(f"未找到报告文件: {fund_name}")


def get_fund_info(data_dir):
    p3 = load_json(data_dir, "part3_业绩分析.json")
    if not p3:
        return "", "", "", ""
    code = p3.get("基金代码", "")
    name = p3.get("基金简称", "")
    manager = p3.get("基金经理", "")
    rd = p3.get("收益回撤", {})
    op_range = ""
    for k, v in rd.items():
        if "任职区间" in k or "运作区间" in k:
            op_range = str(v)
            break
    return code, name, manager, op_range


def load_report(report_file):
    with open(report_file, "r", encoding="utf-8") as f:
        return f.read()


PART9_TITLE = "Part9：案例分析（分级案例、横向专题与投资框架压力测试）"
PART10_TITLE = "Part10：投资能力（中观轮动能力+个股选择能力）"


def normalize_part_title(title):
    if "总结" in title or "投资复盘" in title:
        return "总结：投资追溯与复盘分析"
    if "Part9" in title or "个股案例" in title or "案例分析" in title:
        return PART9_TITLE
    if "Part10" in title or "投资能力" in title or "中观与选股" in title or "中观轮动" in title:
        return PART10_TITLE
    return title


def split_report_parts(md):
    if "==========" not in md:
        return split_report_parts_by_heading(md)
    parts, key, lines = {}, None, []
    for line in md.split("\n"):
        if line.startswith("=========="):
            if key:
                parts[key] = "\n".join(lines).strip()
            key = normalize_part_title(line.strip().replace("=", "").strip())
            lines = []
        else:
            lines.append(line)
    if key:
        parts[key] = "\n".join(lines).strip()
    return parts


def split_report_parts_by_heading(md):
    part_labels = [
        "Part1：基本概况（从业经历+投资框架+投资实践）",
        "Part2：规模变动（基金规模+管理规模）",
        "Part3：业绩分析（业绩超额+回撤控制+持有体验）",
        "Part4：盈亏分析（逐年分析+结合超额+投资逻辑）",
        "Part5：组合管理（交易换手+仓位择时+持股集中度）",
        "Part6：组合特征（市值分布+估值分布+盈利与分红）",
        "Part7：行业配置（配置偏好+盈亏贡献+配置调整）",
        "Part8：个股配置（配置偏好+盈利贡献+配置调整）",
        PART9_TITLE,
        PART10_TITLE,
        "总结：投资追溯与复盘分析",
    ]

    headings = [(i, line) for i, line in enumerate(md.split("\n")) if line.startswith("# ")]
    if len(headings) >= len(part_labels):
        lines = md.split("\n")
        parts = {}
        for idx, label in enumerate(part_labels):
            start = headings[idx][0]
            end = headings[idx + 1][0] if idx + 1 < len(part_labels) else len(lines)
            parts[label] = "\n".join(lines[start:end]).strip()
        return parts

    def match_part_key(title, seen_first):
        if "总结" in title or "投资复盘" in title:
            return "总结：投资追溯与复盘分析"
        if "规模变动" in title:
            return "Part2：规模变动（基金规模+管理规模）"
        if "业绩分析" in title:
            return "Part3：业绩分析（业绩超额+回撤控制+持有体验）"
        if "盈亏分析" in title:
            return "Part4：盈亏分析（逐年分析+结合超额+投资逻辑）"
        if "组合管理" in title:
            return "Part5：组合管理（交易换手+仓位择时+持股集中度）"
        if "组合特征" in title:
            return "Part6：组合特征（市值分布+估值分布+盈利与分红）"
        if "行业配置" in title:
            return "Part7：行业配置（配置偏好+盈亏贡献+配置调整）"
        if "个股案例" in title or "案例分析" in title:
            return PART9_TITLE
        if "投资能力" in title or "中观与选股" in title or "中观轮动" in title:
            return PART10_TITLE
        if "个股配置" in title:
            return "Part8：个股配置（配置偏好+盈利贡献+配置调整）"
        if seen_first:
            return None
        return "Part1：基本概况（从业经历+投资框架+投资实践）"

    def parse_part_heading(line):
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
        m = re.match(r"^=+\s*(.+?)\s*=+\s*$", stripped)
        return m.group(1).strip() if m else None

    parts, key, lines, seen_first = {}, None, [], False
    for line in md.split("\n"):
        title = parse_part_heading(line)
        if title:
            matched = match_part_key(title, seen_first)
            if matched:
                if key:
                    parts[key] = "\n".join(lines).strip()
                key = matched
                lines = [line]
                seen_first = True
                continue
        if key:
            lines.append(line)
    if key:
        parts[key] = "\n".join(lines).strip()
    return parts


def md_to_html(md):
    def apply_inline(text):
        return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)

    def is_table_row(line):
        stripped = line.strip()
        return stripped.startswith('|') and stripped.endswith('|') and stripped.count('|') >= 2

    def is_table_separator(line):
        if not is_table_row(line):
            return False
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        return bool(cells) and all(re.fullmatch(r':?-{3,}:?', cell or '') for cell in cells)

    def split_table_row(line):
        return [cell.strip() for cell in line.strip().strip('|').split('|')]

    def render_table(lines):
        headers = split_table_row(lines[0])
        body = [line for line in lines[2:] if is_table_row(line)]
        html = ['<div class="markdown-table-wrap"><table class="markdown-table">', '<thead><tr>']
        for cell in headers:
            html.append(f'<th>{apply_inline(cell)}</th>')
        html.extend(['</tr></thead>', '<tbody>'])
        for row in body:
            html.append('<tr>')
            cells = split_table_row(row)
            for idx in range(len(headers)):
                value = cells[idx] if idx < len(cells) else ''
                html.append(f'<td>{apply_inline(value)}</td>')
            html.append('</tr>')
        html.extend(['</tbody></table></div>'])
        return ''.join(html)

    md = strip_heading_placeholder_brackets(md)

    # 先把 # 一级标题降为 ## 二级（LLM 有时违规用一级标题，网页里统一用 ## 和 ### 两级）
    md = re.sub(r'^# (?=[^#])', '## ', md, flags=re.M)

    # 移除水平分割线（LLM 有时输出 --- 作为年份分隔）
    md = re.sub(r'^---+\s*$', '', md, flags=re.M)

    # 移除 LLM 泄漏的 prompt 指导标签
    md = re.sub(r'\*{0,2}【思考路径】\*{0,2}\s*', '', md)

    # 有些模型会在段落后紧接标题行；先补空行，避免 ### 被当成普通文本。
    md = re.sub(r'([^\n])\n(#{2,4}\s+)', r'\1\n\n\2', md)

    # 确保标题行、有序列表行之后有双换行，使它们成为独立块
    md = re.sub(r'^(#{2,4}\s+.+)$', r'\1\n', md, flags=re.M)
    md = re.sub(r'^(\d+\.\s+.+)$', r'\1\n', md, flags=re.M)

    # 按双换行拆分为块，逐块处理，避免列表/标题/段落互相干扰
    blocks = re.split(r'\n{2,}', md.strip())
    html_parts = []
    in_list = False

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        table_lines = block.split('\n')
        if len(table_lines) >= 2 and is_table_row(table_lines[0]) and is_table_separator(table_lines[1]):
            if in_list:
                html_parts.append('</ul>')
                in_list = False
            html_parts.append(render_table(table_lines))
            continue

        # 标题块
        if block.startswith('## '):
            if in_list:
                html_parts.append('</ul>')
                in_list = False
            content = apply_inline(block[3:])
            html_parts.append(f'<h3>{content}</h3>')
            continue
        if block.startswith('### '):
            if in_list:
                html_parts.append('</ul>')
                in_list = False
            content = apply_inline(block[4:])
            html_parts.append(f'<h4>{content}</h4>')
            continue
        if block.startswith('#### '):
            if in_list:
                html_parts.append('</ul>')
                in_list = False
            content = apply_inline(block[5:])
            html_parts.append(f'<h5>{content}</h5>')
            continue

        # 混合块：按原始行顺序处理段落、列表、表格。LLM 有时会输出
        # “说明行 + Markdown 表格”或多行加粗标签，不能简单合并成一个段落。
        lines = block.split('\n')
        has_list_line = any(re.match(r'\s*[-*]\s', l) or re.match(r'\s*\d+\.\s', l) for l in lines)
        has_table = any(
            idx + 1 < len(lines) and is_table_row(line) and is_table_separator(lines[idx + 1])
            for idx, line in enumerate(lines)
        )
        has_bold_label_lines = sum(
            1 for line in lines
            if re.match(r'\s*\*\*.+?\*\*[：:：]?', line.strip())
        ) > 1

        if has_list_line or has_table or has_bold_label_lines:
            para_lines = []

            def flush_para():
                if not para_lines:
                    return
                para = ' '.join(l.strip() for l in para_lines if l.strip())
                if para:
                    html_parts.append(f'<p>{apply_inline(para)}</p>')
                para_lines.clear()

            idx = 0
            while idx < len(lines):
                line = lines[idx]
                if not line.strip():
                    flush_para()
                    idx += 1
                    continue
                if idx + 1 < len(lines) and is_table_row(line) and is_table_separator(lines[idx + 1]):
                    flush_para()
                    if in_list:
                        html_parts.append('</ul>')
                        in_list = False
                    table_lines = [line, lines[idx + 1]]
                    idx += 2
                    while idx < len(lines) and is_table_row(lines[idx]):
                        table_lines.append(lines[idx])
                        idx += 1
                    html_parts.append(render_table(table_lines))
                    continue
                list_match = re.match(r'^\s*(?:[-*]|\d+\.)\s+(.+)$', line)
                if list_match:
                    flush_para()
                    if not in_list:
                        html_parts.append('<ul>')
                        in_list = True
                    html_parts.append(f'<li>{apply_inline(list_match.group(1))}</li>')
                else:
                    if in_list:
                        html_parts.append('</ul>')
                        in_list = False
                    bold_label_line = re.match(r'\s*\*\*.+?\*\*[：:：]?', line.strip())
                    if bold_label_line:
                        flush_para()
                    para_lines.append(line)
                    if bold_label_line:
                        flush_para()
                idx += 1
            flush_para()
        else:
            if in_list:
                html_parts.append('</ul>')
                in_list = False
            # 普通段落：合并多行为单个 <p>
            para = ' '.join(l.strip() for l in lines if l.strip())
            para = apply_inline(para)
            html_parts.append(f'<p>{para}</p>')

    if in_list:
        html_parts.append('</ul>')

    return '\n'.join(html_parts)


def normalize_subheadings_html(html):
    """Turn old Chinese subsection markers into compact numeric labels."""
    major = 0
    minor = 0

    def clean_minor(text):
        text = re.sub(r"^\s*[（(][一二三四五六七八九十]+[）)]\s*", "", text)
        return text.strip()

    def repl(match):
        nonlocal major, minor
        level = match.group(1)
        content = match.group(2).strip()
        if level == "3":
            major += 1
            minor = 0
            return match.group(0)
        content = clean_minor(content)
        if re.match(r"^\d+\.\d+\s+", re.sub(r"<[^>]+>", "", content)):
            return f"<h4>{content}</h4>"
        minor += 1
        number = f"{major or 1}.{minor}"
        return f"<h4>{number} {content}</h4>"

    return re.sub(r"<h([34])>(.*?)</h\1>", repl, html, flags=re.S)


def extract_chart_js_targets(js):
    """Extract DOM ids initialized by generated ECharts snippets."""
    targets = set(re.findall(r"getElementById\('([^']+)'\)", js))
    targets.update(re.findall(r'getElementById\("([^"]+)"\)', js))
    return targets


def filter_chart_js_for_existing_targets(chart_js_map, html):
    """Drop generated chart snippets whose containers were not inserted."""
    html_ids = set(re.findall(r'id="([^"]+)"', html))
    filtered = {}
    for part_key, snippets in chart_js_map.items():
        kept = []
        for js in snippets:
            targets = extract_chart_js_targets(js)
            if targets and not targets.issubset(html_ids):
                missing = ", ".join(sorted(targets - html_ids))
                print(f"  [图表JS] 跳过缺少容器的图表: {missing}")
                continue
            kept.append(js)
        filtered[part_key] = kept
    return filtered


def J(val):
    """JSON序列化，中文不转义"""
    return json.dumps(val, ensure_ascii=False)


# ═══════════════════════════════════════════════
# ECharts 共享样式
# ═══════════════════════════════════════════════
EC_SHARED = r"""
var AX_L={color:'#666',fontSize:10};
var SPLIT={lineStyle:{color:'rgba(0,0,0,.08)',type:'dashed'}};
var AX_LINE={lineStyle:{color:'#ccc'}};
var TIP={backgroundColor:'rgba(255,255,255,.96)',borderColor:'#d4c4a8',textStyle:{color:'#1a1a1a',fontSize:12},extraCssText:'box-shadow:0 4px 16px rgba(44,36,22,.12);border-radius:6px;'};
var DOT={symbol:'circle',symbolSize:4};
function tTip(){return Object.assign({trigger:'axis',axisPointer:{type:'cross',crossStyle:{color:'rgba(0,0,0,.15)',width:1},label:{backgroundColor:'#3d5a98',color:'#fff',fontSize:10}}},TIP);}
function mkPt(c,labelColor,decimals){return{symbol:'pin',symbolSize:36,label:{fontSize:8,color:labelColor||'#fff',formatter:function(p){return typeof decimals==='number'&&typeof p.value==='number'?p.value.toFixed(decimals):p.value;},distance:3},data:[{type:'max',name:'最高'},{type:'min',name:'最低'}],itemStyle:{color:c}};}
function sparseLbl(iv){return{show:true,position:'top',distance:6,fontSize:9,color:'#888',formatter:function(p){var i=p.dataIndex,n=p.data.length-1;if(i===0||i===n||i%iv===0){var v=p.value;return typeof v==='number'?v.toFixed(1):v;}return'';}};}
function sparseLblSkipExtremes(iv,data){return{show:true,position:'top',distance:6,fontSize:9,color:'#888',formatter:function(p){var i=p.dataIndex,n=(data||[]).length-1,vals=(data||[]).filter(function(v){return typeof v==='number'&&!isNaN(v);});var max=Math.max.apply(null,vals),min=Math.min.apply(null,vals),v=p.value;if(v===max||v===min)return'';if(i===0||i===n||i%iv===0){return typeof v==='number'?v.toFixed(1):v;}return'';}};}
function suffixLblSkipExtremes(iv,data,suffix,decimals,color){return{show:true,position:'top',distance:5,fontSize:9,color:color||'#555',formatter:function(p){var i=p.dataIndex,n=(data||[]).length-1,vals=(data||[]).filter(function(v){return typeof v==='number'&&!isNaN(v);});var max=Math.max.apply(null,vals),min=Math.min.apply(null,vals),v=p.value;if(v===max||v===min)return'';if(i===0||i===n||i%iv===0){return typeof v==='number'?v.toFixed(decimals==null?1:decimals)+(suffix||''):v;}return'';}};}
function dzS(){return[{type:'slider',bottom:6,height:16,borderColor:'#d4c4a8',fillerColor:'rgba(61,90,152,.08)',handleStyle:{color:'#3d5a98'},textStyle:{color:'#888',fontSize:9},start:0,end:100},{type:'inside'}];}
function cTitle(t){return{show:true,text:t,left:'center',top:4,textStyle:{color:'#1a1a1a',fontSize:13,fontWeight:600}};}
"""


# ═══════════════════════════════════════════════
# 图表生成 — 用模板字符串，变量用 <!--V:xxx--> 占位
# ═══════════════════════════════════════════════

def chart_part2(data):
    if "规模变动(亿元)" not in data:
        print("  [chart_part2] 警告: 缺少规模变动数据，跳过图表")
        return []
    raw = data["规模变动(亿元)"]
    quarters = sorted(raw.keys())
    fund_sz = [round(raw[q]["基金规模"], 2) if raw[q].get("基金规模") is not None else None for q in quarters]
    mgmt_sz = [round(raw[q]["管理规模"], 2) if raw[q].get("管理规模") is not None else None for q in quarters]

    tpl = """
var _c=echarts.init(document.getElementById('chart-p2'));
_c.setOption({
animationDuration:600,animationEasing:'cubicOut',tooltip:tTip(),
title:cTitle('基金与管理规模变动趋势（亿元）'),
legend:{data:['基金规模','管理规模'],left:'center',top:28,textStyle:{color:'#666',fontSize:10},itemWidth:16,itemHeight:8},
grid:{left:56,right:24,top:64,bottom:56},dataZoom:dzS(),
xAxis:{type:'category',data:__QUARTERS__,name:'报告期',nameLocation:'middle',nameGap:44,nameTextStyle:{color:'#888',fontSize:10},axisLabel:Object.assign({},AX_L,{rotate:45,interval:3}),axisLine:AX_LINE,axisTick:{show:false}},
yAxis:{type:'value',axisLabel:Object.assign({},AX_L,{formatter:function(v){return v>=100?v:v.toFixed(1)}}),splitLine:SPLIT,name:'亿元',nameTextStyle:{color:'#888',fontSize:10}},
series:[
{name:'基金规模',type:'line',data:__FUND_SZ__,smooth:true,...DOT,itemStyle:{color:'#3d5a98'},lineStyle:{width:1.8,color:'#3d5a98'},areaStyle:{color:new echarts.graphic.LinearGradient(0,0,0,1,[{offset:0,color:'rgba(61,90,152,.14)'},{offset:1,color:'rgba(61,90,152,0)'}])},markPoint:mkPt('#3d5a98',null,1),z:2,label:sparseLblSkipExtremes(6,__FUND_SZ__)},
{name:'管理规模',type:'line',data:__MGMT_SZ__,smooth:true,...DOT,itemStyle:{color:'#d4a017'},lineStyle:{width:1.8,color:'#d4a017'},areaStyle:{color:new echarts.graphic.LinearGradient(0,0,0,1,[{offset:0,color:'rgba(212,160,23,.10)'},{offset:1,color:'rgba(212,160,23,0)'}])},markPoint:mkPt('#d4a017','#555',1),z:1,label:sparseLblSkipExtremes(6,__MGMT_SZ__)}
]});
"""
    js = tpl.replace("__QUARTERS__", J(quarters)).replace("__FUND_SZ__", J(fund_sz)).replace("__MGMT_SZ__", J(mgmt_sz))
    return [{"id": "chart-p2", "js": js}]


def chart_part3(data, fund_name="", nav_dir_override=None):
    yearly = data.get("年度收益(%)", {})
    rd = data.get("收益回撤", {})
    if not yearly and not rd:
        print("  [chart_part3] 警告: 缺少业绩数据，跳过图表")
        return []
    filtered = {
        k: v
        for k, v in yearly.items()
        if isinstance(v, (int, float)) and math.isfinite(float(v))
    }
    years = sorted(filtered.keys())
    labels = [y.replace("年收益", "") for y in years]
    annual_full_labels = [f"{label}年至数据截止日" if label == str((load_nav_index_cutoff_date(nav_dir_override) or date.today()).year) else f"{label}年" for label in labels]
    vals = [filtered[y] for y in years]
    hs300_returns = load_hs300_yearly_returns(nav_dir_override=nav_dir_override)
    hs300_vals = [hs300_returns.get(label) for label in labels]
    has_hs300 = any(isinstance(v, (int, float)) and math.isfinite(float(v)) for v in hs300_vals)
    # 8 指标表格（放在年度收益率图下方）
    def rd_get(*keys, default="—"):
        for target in keys:
            for k, v in rd.items():
                if target in str(k):
                    return v
        return default

    def rd_get_single_year_contribution(default="—"):
        for target in ("单一年度收益占比", "单一年度收益贡献", "收益贡献度", "收益贡献"):
            for k, v in rd.items():
                if target in str(k):
                    return v
        for k, v in rd.items():
            key = str(k).strip().replace("（", "(").replace("）", ")")
            if key in {"收益占比", "收益占比%"}:
                return v
        return default

    metrics_table = '<table class="metrics-table p3-metrics"><tbody>\n'
    left_defs = [
        ("年化收益", format_pct_metric(rd_get("年化收益", "任职年化", default="—"), signed=True, decimals=2), "pos"),
        ("年化波动", format_pct_metric(rd_get("年化波动", "—"), decimals=2), "accent"),
        ("最大回撤", "−" + format_pct_metric(rd_get("最大回撤", default="—"), decimals=2), "neg-color"),
        ("年度胜率", format_pct_metric(rd_get("年度胜率", "—"), decimals=0), "accent"),
    ]
    right_defs = [
        ("回撤时长", format_month_metric(rd_get("最大回撤时长", "回撤时长", default="—")), "bright"),
        ("修复耗时", format_month_metric(rd_get("回撤修复耗时", "修复耗时", default="—")), "warn"),
        ("回撤区间", format_drawdown_range(rd_get("最大回撤区间", "回撤区间", default="—")), ""),
        ("收益贡献", format_pct_metric(rd_get_single_year_contribution(default="—"), decimals=0), "pos"),
    ]
    mtable_defs = []
    for i in range(4):
        mtable_defs.extend([left_defs[i], right_defs[i]])
    for i in range(0, len(mtable_defs), 2):
        metrics_table += '<tr>'
        for j in range(2):
            if i + j < len(mtable_defs):
                lbl, val, cls = mtable_defs[i + j]
                val_cls = f"mt-val {cls}" if cls else "mt-val"
                metrics_table += f'<td class="mt-lbl">{lbl}</td><td class="{val_cls}">{val}</td>'
        metrics_table += '</tr>\n'
    metrics_table += '</tbody></table>'

    def private_performance_assets():
        if not fund_name:
            return "", None
        private_context = load_private_context_compact(fund_name)
        performance_rows = private_context.get("performance", [])
        annual_rows = private_context.get("annual_returns", [])
        note = str(private_context.get("performance_note", "")).strip()
        if not isinstance(performance_rows, list) or not performance_rows:
            return "", None

        def pct_text(value, signed=False):
            value = clean_metric_value(value)
            if value == "—":
                return value
            text = str(value).strip()
            num = safe_float(text, None)
            if num is None:
                return escape(text)
            if signed:
                return escape(f"{num:+.2f}%")
            return escape(f"{num:.2f}%")

        def month_text(value):
            value = clean_metric_value(value)
            if value == "—":
                return value
            num = safe_float(value, None)
            if num is None:
                return escape(str(value))
            return escape(f"{num:.1f}个月")

        lines = [
            '<div class="private-performance-block private-performance-wide">',
            '<div class="private-performance-title">私募阶段业绩指标</div>',
            '<table class="metrics-table private-performance-table">',
            "<thead><tr>",
        ]
        headers = ["私募产品", "运作区间", "年化收益", "年化波动", "最大回撤", "回撤时长", "修复耗时", "数据截止"]
        lines.extend(f"<th>{escape(h)}</th>" for h in headers)
        lines.append("</tr></thead><tbody>")
        for row in performance_rows:
            if not isinstance(row, dict):
                continue
            fund = str(row.get("基金简称", "")).strip()
            lines.append("<tr>")
            cells = [
                escape(fund or "—"),
                escape(str(row.get("运作区间", "—"))),
                pct_text(row.get("年化收益（%）")),
                pct_text(row.get("年化波动（%）")),
                pct_text(row.get("最大回撤（%）"), signed=True),
                month_text(row.get("最大回撤时长（月）")),
                month_text(row.get("回撤修复耗时(月)")),
                escape(note or "—"),
            ]
            lines.extend(f"<td>{cell}</td>" for cell in cells)
            lines.append("</tr>")
        lines.extend(["</tbody></table>", "</div>"])

        annual_row = annual_rows[0] if isinstance(annual_rows, list) and annual_rows and isinstance(annual_rows[0], dict) else {}
        annual_labels = []
        annual_vals = []
        annual_axis_labels = []
        for key in sorted(k for k in annual_row.keys() if str(k).isdigit()):
            annual_labels.append(f"{key}年至数据截止日" if key == str((load_nav_index_cutoff_date(nav_dir_override) or date.today()).year) else str(key))
            annual_axis_labels.append(str(key))
            annual_vals.append(safe_float(annual_row.get(key), 0.0))
        if not annual_vals:
            return "".join(lines), None

        private_annual_js = """
var _c=echarts.init(document.getElementById('chart-p3-private-annual'));
_c.setOption({
animationDuration:600,animationEasing:'cubicOut',
title:cTitle('私募年度收益率（%）'),
tooltip:Object.assign({},TIP,{trigger:'axis',axisPointer:{type:'shadow'},formatter:function(ps){
  var labels=__FULL_LABELS__;
  var idx=ps[0].dataIndex;
  var s=(labels[idx]||ps[0].axisValue)+'<br/>';
  ps.forEach(function(p){if(p.value!=null&&!isNaN(p.value))s+=p.marker+p.seriesName+': <b>'+Number(p.value).toFixed(2)+'%</b><br/>';});
  return s;
}}),
legend:{show:false},
grid:{left:48,right:18,top:46,bottom:36},
xAxis:{type:'category',data:__LABELS__,name:'年度',nameLocation:'middle',nameGap:24,nameTextStyle:{color:'#888',fontSize:10},axisLabel:AX_L,axisLine:AX_LINE,axisTick:{show:false}},
yAxis:{type:'value',axisLabel:Object.assign({},AX_L,{formatter:'{value}%'}),splitLine:SPLIT,name:'收益率 (%)',nameTextStyle:{color:'#888',fontSize:10}},
series:[{name:'私募收益',type:'bar',data:(function(){
var v=__VALS__;return v.map(function(x){return{value:x,itemStyle:{color:x>=0?'#c23b22':'#3a7d45',borderRadius:[3,3,0,0]}};});})(),barWidth:'__BAR_W__%',
label:{show:true,position:'top',fontSize:8,color:'#666',formatter:function(p){return p.value==null||isNaN(p.value)?'':Number(p.value).toFixed(1)+'%';}}}]
});
""".replace("__LABELS__", J(annual_axis_labels)).replace("__FULL_LABELS__", J(annual_labels)).replace("__VALS__", J(annual_vals)).replace("__BAR_W__", str(42 if len(annual_vals) <= 5 else 34))
        return "".join(lines), private_annual_js

    js = """
	var _c=echarts.init(document.getElementById('chart-p3'));
	_c.setOption({
	animationDuration:600,animationEasing:'cubicOut',
	title:cTitle('年度收益率（%）'),
	tooltip:Object.assign({},TIP,{trigger:'axis',axisPointer:{type:'shadow'},formatter:function(ps){
	  var labels=__FULL_LABELS__;
	  var idx=ps[0].dataIndex;
	  var s=(labels[idx]||ps[0].axisValue+'年')+'<br/>';
	  ps.forEach(function(p){if(p.value!=null&&!isNaN(p.value))s+=p.marker+p.seriesName+': <b>'+Number(p.value).toFixed(2)+'%</b><br/>';});
	  return s;
	}}),
	legend:{show:__LEGEND_SHOW__,data:['基金收益','沪深300'],left:'center',top:24,textStyle:{color:'#666',fontSize:10},itemWidth:12,itemHeight:8},
	grid:{left:52,right:24,top:48,bottom:48},
	xAxis:{type:'category',data:__LABELS__,name:'年度',nameLocation:'middle',nameGap:28,nameTextStyle:{color:'#888',fontSize:10},axisLabel:AX_L,axisLine:AX_LINE,axisTick:{show:false}},
	yAxis:{type:'value',axisLabel:Object.assign({},AX_L,{formatter:'{value}%'}),splitLine:SPLIT,name:'收益率 (%)',nameTextStyle:{color:'#888',fontSize:10}},
	series:[
	{name:'基金收益',type:'bar',itemStyle:{color:'#c23b22'},data:(function(){
	var v=__VALS__;return v.map(function(x){return{value:x,itemStyle:{color:x>=0?'#c23b22':'#3a7d45',borderRadius:x>=0?[3,3,0,0]:[0,0,3,3]}};});})(),barWidth:'__BAR_W__%',
	label:{show:true,position:'top',fontSize:8,color:'#666',formatter:function(p){return p.value==null||isNaN(p.value)?'':Number(p.value).toFixed(1)+'%';}}},
	{name:'沪深300',type:'bar',itemStyle:{color:'#9aa0a6'},data:(function(){
	var v=__HS300_VALS__;return v.map(function(x){return x==null?null:{value:x,itemStyle:{color:'#9aa0a6',borderRadius:x>=0?[3,3,0,0]:[0,0,3,3]}};});})(),barWidth:'__BAR_W__%',
	barGap:'18%',label:{show:false}}
	]
	});
	""".replace("__LABELS__", J(labels)).replace("__FULL_LABELS__", J(annual_full_labels)).replace("__VALS__", J(vals)).replace("__HS300_VALS__", J(hs300_vals if has_hs300 else [])).replace("__LEGEND_SHOW__", "true" if has_hs300 else "false").replace("__BAR_W__", str(30 if has_hs300 and len(vals) <= 5 else 24 if has_hs300 and len(vals) <= 12 else 20 if has_hs300 else 40 if len(vals) <= 5 else 34 if len(vals) <= 12 else 28))

    result = []

    # ── 净值/回撤双面板图 ──
    if fund_name:
        op_range = rd_get("任职区间", "运作区间", default="")
        start_year = 2016
        if op_range:
            m = re.match(r"(\d{4})", str(op_range))
            if m:
                start_year = max(int(m.group(1)), 2016)
        dates, fund_nav, idx_close = load_nav_and_index(fund_name, start_year, nav_dir_override)
        if dates and fund_nav and idx_close:
            js_b = """
(function(){
var _c=echarts.init(document.getElementById('chart-p3b'));
var _dates=__DATES__;
var _N=_dates.length;
var _rawNav=__FUND_NAV__;
var _rawIdx=__IDX_CLOSE__;
var _vs=0,_ve=_N-1;

function rebuild(s,e){
  _vs=s;_ve=e;
  var navB=_rawNav[s],idxB=_rawIdx[s];
  var nav=new Array(_N),idx=new Array(_N),exc=new Array(_N);
  var fdd=new Array(_N),idd=new Array(_N);
  var navPk=0,idxPk=0;
  var navMaxI=-1,navMaxV=-Infinity;
  var fddMinI=-1,fddMinV=Infinity;
  for(var i=0;i<_N;i++){
    if(i<s||i>e){nav[i]=idx[i]=exc[i]=fdd[i]=idd[i]=null;continue;}
    nav[i]=+((_rawNav[i]/navB-1)*100).toFixed(2);
    idx[i]=+((_rawIdx[i]/idxB-1)*100).toFixed(2);
    exc[i]=+(nav[i]-idx[i]).toFixed(2);
    navPk=Math.max(navPk,_rawNav[i]/navB);
    idxPk=Math.max(idxPk,_rawIdx[i]/idxB);
    fdd[i]=+((_rawNav[i]/navB/navPk-1)*100).toFixed(2);
    idd[i]=+((_rawIdx[i]/idxB/idxPk-1)*100).toFixed(2);
    if(nav[i]>navMaxV){navMaxV=nav[i];navMaxI=i;}
    if(fdd[i]<fddMinV){fddMinV=fdd[i];fddMinI=i;}
  }
  var mkNav=[],mkFdd=[];
  if(navMaxI>=0) mkNav.push({name:'收益最高',xAxis:_dates[navMaxI],yAxis:navMaxV,
    symbol:'circle',symbolSize:5,itemStyle:{color:'#c23b22'},
    label:{show:true,position:'top',formatter:navMaxV.toFixed(1)+'%',color:'#c23b22',fontSize:9,fontWeight:600,distance:4}});
  if(fddMinI>=0) mkFdd.push({name:'回撤最大',xAxis:_dates[fddMinI],yAxis:fddMinV,
    symbol:'circle',symbolSize:5,itemStyle:{color:'#c23b22'},
    label:{show:true,position:'bottom',formatter:fddMinV.toFixed(1)+'%',color:'#c23b22',fontSize:9,fontWeight:600,distance:4}});
  _c.setOption({series:[{data:nav,markPoint:{data:mkNav,silent:true,animation:false}},{data:idx},{data:exc},{data:fdd,markPoint:{data:mkFdd,silent:true,animation:false}},{data:idd}]});
}

_c.setOption({
animationDuration:800,animationEasing:'cubicOut',
axisPointer:{link:[{xAxisIndex:'all'}],lineStyle:{color:'rgba(0,0,0,.16)',width:0.8}},
grid:[{left:56,right:56,top:48,height:260},{left:56,right:56,top:340,bottom:52}],
xAxis:[{type:'category',data:__DATES__,gridIndex:0,axisLabel:{show:false},axisLine:AX_LINE,axisTick:{show:false},boundaryGap:false},
       {type:'category',data:__DATES__,gridIndex:1,axisLine:AX_LINE,axisTick:{show:false},boundaryGap:false,
        axisLabel:{color:'#666',fontSize:10,rotate:0,
        interval:function(idx){
          if(idx===_vs||idx===_ve)return true;
          var vis=_ve-_vs+1,step=Math.max(1,Math.floor(vis/8));
          return(idx-_vs)%step===0;
        }}}],
yAxis:[{type:'value',scale:true,gridIndex:0,splitNumber:8,axisLabel:Object.assign({},AX_L,{formatter:'{value}%'}),splitLine:Object.assign({},SPLIT,{lineStyle:{color:'rgba(0,0,0,.055)',width:0.7}}),name:'累计收益率 (%)',nameTextStyle:{color:'#888',fontSize:10}},
       {type:'value',gridIndex:1,splitNumber:8,axisLabel:Object.assign({},AX_L,{formatter:'{value}%'}),splitLine:Object.assign({},SPLIT,{lineStyle:{color:'rgba(0,0,0,.055)',width:0.7}}),name:'回撤 (%)',nameTextStyle:{color:'#888',fontSize:10}}],
dataZoom:[{type:'slider',xAxisIndex:[0,1],bottom:6,height:16,borderColor:'#d4c4a8',fillerColor:'rgba(61,90,152,.08)',handleStyle:{color:'#3d5a98'},textStyle:{color:'#888',fontSize:9},start:0,end:100},{type:'inside',xAxisIndex:[0,1]}],
title:{show:true,text:'累计收益与回撤分析',left:'center',top:4,textStyle:{color:'#1a1a1a',fontSize:13,fontWeight:600}},
tooltip:Object.assign({},TIP,{trigger:'axis',axisPointer:{type:'cross',crossStyle:{color:'rgba(0,0,0,.12)',width:1}},formatter:function(ps){
  var s=ps[0].axisValue+'<br/>';
  ps.forEach(function(p){if(p.value!=null)s+=p.marker+p.seriesName+': <b>'+p.value.toFixed(2)+'%</b><br/>';});return s;}}),
	legend:{data:['基金收益','沪深300','超额收益','基金回撤','沪深300回撤'],left:'center',top:22,textStyle:{color:'#666',fontSize:10},itemWidth:14,itemHeight:8},
	series:[
	  {name:'基金收益',type:'line',xAxisIndex:0,yAxisIndex:0,data:[],symbol:'none',lineStyle:{width:1.6,color:'#c23b22'},
	   connectNulls:false,itemStyle:{color:'#c23b22'},z:10},
  {name:'沪深300',type:'line',xAxisIndex:0,yAxisIndex:0,data:[],symbol:'none',lineStyle:{width:1.2,color:'#3d5a98'},
   connectNulls:false,itemStyle:{color:'#3d5a98'},z:5},
  {name:'超额收益',type:'line',xAxisIndex:0,yAxisIndex:0,data:[],symbol:'none',lineStyle:{width:0.9,color:'#b0b0b0'},
   connectNulls:false,itemStyle:{color:'#b0b0b0'},areaStyle:{color:{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:'rgba(176,176,176,0.15)'},{offset:0.5,color:'rgba(176,176,176,0.02)'},{offset:1,color:'rgba(176,176,176,0.15)'}]}},
   markLine:{silent:true,symbol:'none',lineStyle:{color:'rgba(0,0,0,.15)',type:'dashed',width:1},data:[{yAxis:0}]},z:8},
  {name:'基金回撤',type:'line',xAxisIndex:1,yAxisIndex:1,data:[],symbol:'none',lineStyle:{width:1.6,color:'#c23b22'},
   connectNulls:false,itemStyle:{color:'#c23b22'},areaStyle:{color:{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:'rgba(194,59,34,0.02)'},{offset:1,color:'rgba(194,59,34,0.16)'}]}},
   z:10},
  {name:'沪深300回撤',type:'line',xAxisIndex:1,yAxisIndex:1,data:[],symbol:'none',lineStyle:{width:1.2,color:'#3d5a98'},
   connectNulls:false,itemStyle:{color:'#3d5a98'},areaStyle:{color:{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:'rgba(61,90,152,0.015)'},{offset:1,color:'rgba(61,90,152,0.075)'}]}},
   z:5}
]
});
rebuild(0,_N-1);
_c.on('datazoom',function(){
  var dz=_c.getOption().dataZoom[0];
  var s=Math.round(dz.start/100*(_N-1));
  var e=Math.round(dz.end/100*(_N-1));
  e=Math.min(e,_N-1);
  if(e<=s)return;
  rebuild(s,e);
});
})();
""".replace("__DATES__", J(dates)).replace("__FUND_NAV__", J(fund_nav)).replace("__IDX_CLOSE__", J(idx_close))

            result.append({"flex": 1.8, "column_items": [{"id": "chart-p3b", "js": js_b, "height": "500px"}]})

    private_metrics_html, private_annual_js = private_performance_assets()
    if result and private_metrics_html:
        result[0].setdefault("column_items", []).append({"html_block": private_metrics_html})
    elif private_metrics_html:
        result.append({"flex": 1.8, "column_items": [{"html_block": private_metrics_html}]})

    # 右侧：年度收益率图（压缩）+ 公募指标表格 + 私募年度收益图，垂直堆叠
    right_items = [
        {"id": "chart-p3", "js": js, "height": "280px"},
        {"html_block": metrics_table},
    ]
    if private_annual_js:
        right_items.append({"id": "chart-p3-private-annual", "js": private_annual_js, "height": "230px"})
    result.append({"flex": 1.2, "column_items": right_items})

    return result


def _p4_amount_text(value):
    try:
        num = float(value)
    except (TypeError, ValueError):
        return escape(str(value or ""))
    if math.isfinite(num) and num.is_integer():
        return f"{int(num):,}"
    return f"{num:,.1f}"


def _p4_rank_items(items, limit=10):
    if not isinstance(items, dict):
        return []
    numeric = []
    for name, value in items.items():
        try:
            num = float(value)
        except (TypeError, ValueError):
            continue
        numeric.append((str(name), num))
    if not numeric:
        return []

    positives = sorted((item for item in numeric if item[1] >= 0), key=lambda item: item[1], reverse=True)[:5]
    negatives = sorted((item for item in numeric if item[1] < 0), key=lambda item: item[1])[:5]
    return positives + [None] * (5 - len(positives)) + negatives + [None] * (5 - len(negatives))


def _p4_detail_table_html(yearly, detail_key, title):
    years = sorted(yearly.keys())
    columns = [_p4_rank_items(yearly[year].get(detail_key, {})) for year in years]
    max_rows = max((len(items) for items in columns), default=0)
    if not years or max_rows == 0:
        return ""

    html = [
        '<div class="p4-detail-table-block">',
        f'<div class="p4-detail-title">{escape(title)}</div>',
        '<div class="p4-detail-scroll">',
        '<table class="p4-detail-table">',
        '<thead><tr>',
    ]
    html.extend(f"<th>{escape(str(year).rstrip('年'))}年</th>" for year in years)
    html.extend(["</tr></thead>", "<tbody>"])
    for row_idx in range(max_rows):
        html.append("<tr>")
        for col in columns:
            if row_idx >= len(col):
                html.append('<td class="p4-empty">—</td>')
                continue
            item = col[row_idx]
            if item is None:
                html.append('<td class="p4-empty">—</td>')
                continue
            name, value = item
            cls = "p4-profit" if value >= 0 else "p4-loss"
            safe_name = escape(name)
            safe_attr = escape(name, quote=True)
            html.append(f'<td class="{cls}" data-name="{safe_attr}">{safe_name}<br><span>{_p4_amount_text(value)}</span></td>')
        html.append("</tr>")
    html.extend(["</tbody></table>", "</div>", "</div>"])
    return "".join(html)


def _p4_cumulative_items(yearly, detail_key):
    totals = {}
    for yd in yearly.values():
        items = yd.get(detail_key, {})
        if not isinstance(items, dict):
            continue
        for name, value in items.items():
            try:
                num = float(value)
            except (TypeError, ValueError):
                continue
            totals[str(name)] = totals.get(str(name), 0.0) + num
    positives = sorted((item for item in totals.items() if item[1] >= 0), key=lambda item: item[1], reverse=True)[:5]
    negatives = sorted((item for item in totals.items() if item[1] < 0), key=lambda item: item[1])[:5]
    selected = []
    seen = set()
    for item in positives + negatives:
        if item[0] not in seen:
            selected.append(item)
            seen.add(item[0])
    return selected


def _p4_matrix_table_html(yearly, detail_key, row_label, title):
    years = sorted(yearly.keys())
    rows = _p4_cumulative_items(yearly, detail_key)
    if not years or not rows:
        return ""

    html = [
        '<div class="p4-detail-table-block">',
        f'<div class="p4-detail-title">{escape(title)}</div>',
        '<div class="p4-detail-scroll">',
        '<table class="p4-detail-table p4-matrix-table">',
        '<thead><tr>',
        f"<th>{escape(row_label)}</th>",
    ]
    html.extend(f"<th>{escape(str(year).rstrip('年'))}年</th>" for year in years)
    html.extend(["</tr></thead>", "<tbody>"])
    for name, total in rows:
        safe_name = escape(name)
        total_cls = "p4-profit" if total >= 0 else "p4-loss"
        html.append(f'<tr><td class="p4-row-head">{safe_name}<br><span class="{total_cls}">{_p4_amount_text(total)}</span></td>')
        for year in years:
            value = yearly.get(year, {}).get(detail_key, {}).get(name)
            if value is None:
                html.append('<td class="p4-empty">—</td>')
                continue
            try:
                num = float(value)
            except (TypeError, ValueError):
                html.append('<td class="p4-empty">—</td>')
                continue
            cls = "p4-profit" if num >= 0 else "p4-loss"
            html.append(f'<td class="{cls}"><span>{_p4_amount_text(num)}</span></td>')
        html.append("</tr>")
    html.extend(["</tbody></table>", "</div>", "</div>"])
    return "".join(html)


def _p4_table_row_html(*tables):
    blocks = [table for table in tables if table]
    if not blocks:
        return ""
    return '<div class="p4-detail-table-row">' + "".join(blocks) + "</div>"


def chart_part4(data):
    if "年度盈亏(万元)" not in data:
        print("  [chart_part4] 警告: 缺少年度盈亏数据，跳过图表")
        return []
    ind_members = {key: set(values) for key, values in data.get("行业个股映射", {}).items()}
    yearly = data["年度盈亏(万元)"]
    quarterly = data.get("季度盈亏(万元)", {})
    years = sorted(yearly.keys())
    detail_row = _p4_table_row_html(
        _p4_detail_table_html(yearly, "一级", "年度一级行业盈亏明细（万元）"),
        _p4_detail_table_html(yearly, "个股", "年度个股盈亏明细（万元）"),
    )
    matrix_row = _p4_table_row_html(
        _p4_matrix_table_html(yearly, "一级", "一级行业", "累计盈亏前五/后五一级行业年度明细（万元）"),
        _p4_matrix_table_html(yearly, "个股", "个股", "累计盈亏前五/后五个股年度明细（万元）"),
    )
    overview = [{"html_block": detail_row}, {"html_block": matrix_row}]
    overview = [item for item in overview if item["html_block"]]

    per_year = {}
    for year_key in years:
        yd = yearly[year_key]
        year_num = year_key.rstrip("年")
        ind_data = yd.get("一级", {})
        stk_data = yd.get("个股", {})
        qtrs = {}
        for qkey, qd in quarterly.items():
            if qkey.startswith(year_num):
                q_label = qkey.replace(year_num, "")
                q_total = sum(qd.get("大类", {}).values())
                qtrs[q_label] = q_total
        if not ind_data and not stk_data and not qtrs:
            continue

        chart_ids = []
        js_list = []
        cid_ind = f"chart-p4y-{year_num}-ind"
        cid_stk = f"chart-p4y-{year_num}-stk"

        # 一级行业盈亏
        if ind_data:
            sorted_ind = sorted(ind_data.items(), key=lambda x: x[1], reverse=True)
            ind_names = [it[0] for it in sorted_ind]
            ind_bar = [{"value": it[1], "name": it[0], "itemStyle": {"color": "#c23b22" if it[1] >= 0 else "#3a7d45"}} for it in sorted_ind]
            chart_ids.append(cid_ind)
            js_list.append(f"""
var _c=echarts.init(document.getElementById('{cid_ind}'));
_c.setOption({{animationDuration:400,animationEasing:'cubicOut',
title:{{show:true,text:'{year_num}年 一级行业盈亏（万元）',left:'center',top:2,textStyle:{{color:'#1a1a1a',fontSize:12,fontWeight:600}}}},
tooltip:Object.assign({{}},TIP,{{trigger:'axis',axisPointer:{{type:'shadow'}},
  formatter:function(ps){{var p=ps[0];return p.name+'<br/><b>'+p.value.toLocaleString()+'</b> 万元';}}}}),
grid:{{left:50,right:8,top:28,bottom:52}},
xAxis:{{type:'category',data:{J(ind_names)},axisLabel:{{color:'#666',fontSize:9,rotate:45,interval:0}},axisLine:AX_LINE,axisTick:{{show:false}}}},
yAxis:{{type:'value',axisLabel:{{color:'#999',fontSize:9}},splitLine:SPLIT}},
series:[{{type:'bar',data:{J(ind_bar)},emphasis:{{itemStyle:{{opacity:1}}}}}}]
}});
""")

        # 个股盈亏TOP10
        stk_bar = []
        stk_names = []
        if stk_data:
            sorted_stk = sorted(stk_data.items(), key=lambda x: x[1], reverse=True)
            profit_stk = [s for s in sorted_stk if s[1] >= 0][:10]
            loss_stk = [s for s in sorted_stk if s[1] < 0][-10:]
            top_stk = profit_stk + loss_stk
            seen = set()
            unique = []
            for item in top_stk:
                if item[0] not in seen:
                    seen.add(item[0])
                    unique.append(item)
            top_stk = unique
            stk_names = [it[0] for it in top_stk]
            stk_bar = [{"value": it[1], "name": it[0], "itemStyle": {"color": "#c23b22" if it[1] >= 0 else "#3a7d45"}} for it in top_stk]
            chart_ids.append(cid_stk)
            stk_to_ind = {}
            for sn in stk_names:
                for ind_name, members in ind_members.items():
                    if sn in members:
                        stk_to_ind[sn] = ind_name
                        break
            js_list.append(f"""
var _c=echarts.init(document.getElementById('{cid_stk}'));
_c.setOption({{animationDuration:400,animationEasing:'cubicOut',
title:{{show:true,text:'{year_num}年 个股盈亏TOP10（万元）',left:'center',top:2,textStyle:{{color:'#1a1a1a',fontSize:12,fontWeight:600}}}},
tooltip:Object.assign({{}},TIP,{{trigger:'axis',axisPointer:{{type:'shadow'}},
  formatter:function(ps){{var p=ps[0];return p.name+'<br/><b>'+p.value.toLocaleString()+'</b> 万元';}}}}),
grid:{{left:50,right:8,top:28,bottom:52}},
xAxis:{{type:'category',data:{J(stk_names)},axisLabel:{{color:'#666',fontSize:9,rotate:45,interval:0}},axisLine:AX_LINE,axisTick:{{show:false}}}},
yAxis:{{type:'value',axisLabel:{{color:'#999',fontSize:9}},splitLine:SPLIT}},
series:[{{type:'bar',data:{J(stk_bar)}}}]
}});
window._p4y{year_num}={{stk:_c,names:{J(stk_names)},s2i:{J(stk_to_ind)}}};
""")

        # 行业悬停→个股高亮交互
        if ind_data and stk_data:
            js_list.append(f"""
(function(){{
var _ic=echarts.getInstanceByDom(document.getElementById('{cid_ind}'));
var _sd=window._p4y{year_num};
if(!_ic||!_sd)return;
var _ob={J(stk_bar)};
function hl(ind){{
  var nd=_ob.map(function(item){{
    var m=_sd.s2i[item.name]===ind;
    return{{value:item.value,name:item.name,
      itemStyle:m?{{
        color:item.value>=0?'#e8715e':'#5cb06a',borderColor:'#333',borderWidth:1
      }}:{{
        color:item.value>=0?'rgba(194,59,34,0.12)':'rgba(58,125,69,0.12)'
      }},
      label:m?{{show:true,position:item.value>=0?'top':'bottom',fontSize:9,color:'#333',fontWeight:600,formatter:item.value.toLocaleString()+'万'}}:{{show:false}}
    }};
  }});
  _sd.stk.setOption({{series:[{{data:nd}}]}});
}}
function reset(){{
  _sd.stk.setOption({{series:[{{data:_ob}}]}});
}}
var _indNames=_ic.getOption().xAxis[0].data;
_ic.on('updateAxisPointer',function(e){{
  if(e.axesInfo&&e.axesInfo.length){{
    var idx=e.axesInfo[0].value;
    var nm=(idx!=null&&_indNames[idx])?_indNames[idx]:null;
    if(nm)hl(nm);else reset();
  }}else{{reset();}}
}});
}})();
""")

        # 大类盈亏
        big_data = yd.get("大类", {})
        if big_data:
            sorted_big = sorted(big_data.items(), key=lambda x: x[1], reverse=True)
            big_names = [it[0] for it in sorted_big]
            big_bar = [{"value": it[1], "itemStyle": {"color": "#c23b22" if it[1] >= 0 else "#3a7d45"}} for it in sorted_big]
            cid_big = f"chart-p4y-{year_num}-big"
            chart_ids.append(cid_big)
            js_list.append(f"""
var _c=echarts.init(document.getElementById('{cid_big}'));
_c.setOption({{animationDuration:400,animationEasing:'cubicOut',
title:{{show:true,text:'{year_num}年 大类盈亏（万元）',left:'center',top:0,textStyle:{{color:'#1a1a1a',fontSize:10,fontWeight:600}}}},
tooltip:Object.assign({{}},TIP,{{trigger:'axis',axisPointer:{{type:'shadow'}},
  formatter:function(ps){{var p=ps[0];return p.name+'<br/><b>'+p.value.toLocaleString()+'</b> 万元';}}}}),
grid:{{left:50,right:6,top:28,bottom:34}},
xAxis:{{type:'category',data:{J(big_names)},axisLabel:{{color:'#666',fontSize:10,interval:0}},axisLine:AX_LINE,axisTick:{{show:false}}}},
yAxis:{{type:'value',axisLabel:{{color:'#999',fontSize:9}},splitLine:SPLIT}},
series:[{{type:'bar',barMaxWidth:28,data:{J(big_bar)},label:{{show:true,position:'top',fontSize:7,color:'#8b949e',formatter:function(p){{return p.value.toLocaleString();}}}}}}]
}});
""")

        # 季度盈亏
        if qtrs:
            q_labels = sorted(qtrs.keys())
            q_vals = [qtrs[q] for q in q_labels]
            q_bar = [{"value": v, "itemStyle": {"color": "#c23b22" if v >= 0 else "#3a7d45"}} for v in q_vals]
            cid = f"chart-p4y-{year_num}-qtr"
            chart_ids.append(cid)
            js_list.append(f"""
var _c=echarts.init(document.getElementById('{cid}'));
_c.setOption({{animationDuration:400,animationEasing:'cubicOut',
title:{{show:true,text:'{year_num}年 季度盈亏（万元）',left:'center',top:0,textStyle:{{color:'#1a1a1a',fontSize:10,fontWeight:600}}}},
tooltip:Object.assign({{}},TIP,{{trigger:'axis',axisPointer:{{type:'shadow'}}}}),
grid:{{left:50,right:6,top:28,bottom:34}},
xAxis:{{type:'category',data:{J(q_labels)},axisLabel:{{color:'#666',fontSize:10,interval:0}},axisLine:AX_LINE,axisTick:{{show:false}}}},
yAxis:{{type:'value',axisLabel:{{color:'#999',fontSize:9}},splitLine:SPLIT}},
series:[{{type:'bar',barMaxWidth:28,data:{J(q_bar)},label:{{show:true,position:'top',fontSize:7,color:'#8b949e',formatter:function(p){{return p.value.toLocaleString();}}}}}}]
}});
""")

        if chart_ids:
            divs = []
            # Separate big+qtr (stack vertically) from other charts
            sm_ids = [c for c in chart_ids if ("-qtr" in c or "-big" in c)]
            main_ids = [c for c in chart_ids if c not in sm_ids]
            for cid in main_ids:
                divs.append(f'<div class="p4-yc" id="{cid}"></div>')
            if sm_ids:
                inner = "".join(f'<div class="p4-yc-sm-item" id="{cid}"></div>' for cid in sm_ids)
                stack_style = ' style="height:260px"' if not main_ids else ""
                divs.append(f'<div class="p4-yc-stack"{stack_style}>{inner}</div>')
            per_year[year_num] = {
                "charts_html": f'<div class="p4-year-charts-row">{"".join(divs)}</div>',
                "js_list": js_list,
            }

    return {"overview": overview, "per_year": per_year}




def inject_per_year_charts(text_html, per_year_data):
    """将逐年图表注入 Part4：每年标题下先横排图表，再跟文字。"""
    if not per_year_data:
        return text_html, []
    year_heading = re.compile(r'(<h[23]>.*?(\d{4})年.*?</h[23]>)', re.DOTALL)
    matches = list(year_heading.finditer(text_html))
    if not matches:
        return text_html, []
    all_js = []
    result = []
    prev_end = 0
    for i, m in enumerate(matches):
        year_num = m.group(2)
        heading_html = m.group(1)
        content_start = m.end()
        content_end = matches[i + 1].start() if i + 1 < len(matches) else len(text_html)
        result.append(text_html[prev_end:m.start()])
        content_html = text_html[content_start:content_end]
        if year_num in per_year_data:
            pyd = per_year_data[year_num]
            result.append(
                f'{heading_html}{pyd["charts_html"]}{content_html}'
            )
            all_js.extend(pyd["js_list"])
        else:
            result.append(heading_html)
            result.append(content_html)
        prev_end = content_end
    result.append(text_html[prev_end:])
    return ''.join(result), all_js


def _case_chart_id(stock_name, suffix):
    safe = re.sub(r"\W+", "_", str(stock_name), flags=re.UNICODE).strip("_") or "stock"
    return f"chart-p9case-{safe}-{suffix}"


def _case_period_date(period):
    period = str(period or "")
    if len(period) == 8:
        return f"{period[:4]}-{period[4:6]}-{period[6:8]}"
    return period


def _case_period_label(period):
    period = str(period or "")
    if len(period) == 8:
        quarter = {"0331": "Q1", "0630": "Q2", "0930": "Q3", "1231": "Q4"}.get(period[4:])
        if quarter:
            return f"{period[:4]}{quarter}"
    return period


def _case_roe_pct(value):
    num = safe_float(value, None)
    if num is None:
        return None
    if abs(num) <= 1:
        num *= 100
    return round(num, 2)


def _case_bar(values):
    return [
        {
            "value": value,
            "itemStyle": {"color": "#c23b22" if safe_float(value) >= 0 else "#3a7d45"},
        }
        for value in values
    ]


def _case_signed_bar(values, positive=True):
    return [
        {
            "value": value if (safe_float(value) >= 0) == positive else None,
            "itemStyle": {"color": "#c23b22" if positive else "#3a7d45"},
        }
        for value in values
    ]


def _build_part9_case_chart(item):
    name = str(item.get("股票名称") or "").strip()
    if not name:
        return None

    hold_points = item.get("持仓轨迹", []) or []
    price_points = item.get("月度价格", []) or []
    q_pnl_points = item.get("季度盈亏(万元)", []) or []
    finance_points = item.get("财务指标", []) or []

    price_dates = [row.get("日期", "") for row in price_points if row.get("日期")]
    hold_dates = [
        _case_period_date(row.get("报告期"))
        for row in hold_points
        if _case_period_date(row.get("报告期"))
    ]
    dates = sorted(set(price_dates) | set(hold_dates))
    price_map = {row.get("日期"): safe_float(row.get("收盘价"), None) for row in price_points}
    weight_map = {}
    shares_map = {}
    for row in hold_points:
        d = _case_period_date(row.get("报告期"))
        if not d:
            continue
        weight_map[d] = safe_float(row.get("占基金净值比"), None)
        shares_map[d] = safe_float(row.get("持仓数量(万股)"), None)

    price_vals = [price_map.get(d) for d in dates]
    weight_vals = [weight_map.get(d) for d in dates]
    shares_vals = [shares_map.get(d) for d in dates]
    weight_points = [
        {"name": d, "value": [d, weight_map[d]]}
        for d in dates
        if d in weight_map and weight_map[d] is not None
    ]

    q_labels = [row.get("季度标签") or row.get("季度") for row in q_pnl_points if row.get("季度标签") or row.get("季度")]
    q_vals = [safe_float(row.get("盈亏(万元)")) for row in q_pnl_points if row.get("季度标签") or row.get("季度")]
    if not (dates and (any(v is not None for v in weight_vals) or any(v is not None for v in shares_vals)) and q_labels):
        return None

    finance_labels = []
    pe_vals = []
    roe_vals = []
    for row in finance_points:
        label = _case_period_label(row.get("报告期")) or row.get("日期")
        pe = safe_float(row.get("PE"), None)
        roe = _case_roe_pct(row.get("ROE"))
        if not label or (pe is None and roe is None):
            continue
        finance_labels.append(label)
        pe_vals.append(round(pe, 2) if pe is not None else None)
        roe_vals.append(roe)
    has_finance_chart = bool(finance_labels and (any(v is not None for v in pe_vals) or any(v is not None for v in roe_vals)))

    weight_id = _case_chart_id(name, "weight")
    shares_id = _case_chart_id(name, "shares")
    pnl_id = _case_chart_id(name, "pnl")
    pe_roe_id = _case_chart_id(name, "pe-roe")
    safe_title = name.replace("'", "\\'")
    pe_roe_js = ""
    if has_finance_chart:
        pe_roe_js = f"""
var cf=echarts.init(document.getElementById('{pe_roe_id}'));
var financeLabels={J(finance_labels)};
var peVals={J(pe_vals)};
var roeVals={J(roe_vals)};
cf.setOption({{animationDuration:500,animationEasing:'cubicOut',tooltip:tTip(),
title:{{show:true,text:'PE-ROE季度变化｜{safe_title}',left:'center',top:2,textStyle:{{color:'#1a1a1a',fontSize:12,fontWeight:700}}}},
legend:{{data:['PE','ROE'],left:'center',top:26,textStyle:{{color:'#666',fontSize:9}},itemWidth:12,itemHeight:7}},
grid:{{left:48,right:44,top:52,bottom:38}},
xAxis:{{type:'category',data:financeLabels,axisLabel:Object.assign({{}},AX_L,{{rotate:30,interval:'auto'}}),axisLine:AX_LINE,axisTick:{{show:false}}}},
yAxis:[{{type:'value',scale:true,axisLabel:AX_L,splitLine:SPLIT,name:'PE',nameTextStyle:{{color:'#888',fontSize:9}}}},
       {{type:'value',scale:true,axisLabel:Object.assign({{}},AX_L,{{formatter:'{{value}}%'}}),splitLine:{{show:false}},name:'ROE',nameTextStyle:{{color:'#888',fontSize:9}}}}],
series:[
{{name:'PE',type:'line',data:peVals,connectNulls:true,smooth:0.25,symbol:'circle',symbolSize:3,lineStyle:{{width:1.4,color:'#8a6f3d'}},itemStyle:{{color:'#8a6f3d'}}}},
{{name:'ROE',type:'line',yAxisIndex:1,data:roeVals,connectNulls:true,smooth:0.25,symbol:'circle',symbolSize:3,lineStyle:{{width:1.4,color:'#2f7d62'}},itemStyle:{{color:'#2f7d62'}}}}
]}});"""
    js = f"""
(function(){{
var dates={J(dates)};
var price={J(price_vals)};
var weight={J(weight_vals)};
var weightPoints={J(weight_points)};
var shares={J(shares_vals)};
var qLabels={J(q_labels)};
var qVals={J(q_vals)};
var qPosBars={J(_case_signed_bar(q_vals, True))};
var qNegBars={J(_case_signed_bar(q_vals, False))};
var cw=echarts.init(document.getElementById('{weight_id}'));
cw.setOption({{animationDuration:500,animationEasing:'cubicOut',tooltip:tTip(),
title:{{show:true,text:'{safe_title}｜重仓权重与股价',left:'center',top:2,textStyle:{{color:'#1a1a1a',fontSize:12,fontWeight:700}}}},
legend:{{data:['配置比例','个股收盘价'],left:'center',top:26,textStyle:{{color:'#666',fontSize:9}},itemWidth:12,itemHeight:7}},
grid:{{left:48,right:46,top:58,bottom:42}},
xAxis:{{type:'category',data:dates,axisLabel:Object.assign({{}},AX_L,{{rotate:26,interval:'auto'}}),axisLine:AX_LINE,axisTick:{{show:false}}}},
yAxis:[{{type:'value',axisLabel:Object.assign({{}},AX_L,{{formatter:'{{value}}%'}}),splitLine:SPLIT,name:'权重',nameTextStyle:{{color:'#888',fontSize:9}}}},
       {{type:'value',scale:true,axisLabel:AX_L,splitLine:{{show:false}},name:'股价',nameTextStyle:{{color:'#888',fontSize:9}}}}],
series:[
{{name:'配置比例',type:'line',data:weight,connectNulls:true,smooth:0.28,symbol:'none',lineStyle:{{width:1.1,color:'#b23a48',opacity:.72}},itemStyle:{{color:'#b23a48'}},emphasis:{{focus:'series',lineStyle:{{width:2}}}}}},
{{name:'权重披露点',type:'scatter',data:weightPoints,yAxisIndex:0,symbol:'circle',symbolSize:9,z:12,itemStyle:{{color:'#b23a48',borderColor:'#fff',borderWidth:2,shadowBlur:5,shadowColor:'rgba(178,58,72,.35)'}},labelLayout:{{hideOverlap:true}},label:{{show:true,position:'top',distance:4,fontSize:7,color:'#8f2633',fontWeight:600,formatter:function(p){{var v=Array.isArray(p.value)?p.value[1]:p.value;return v==null?'':Number(v).toFixed(1)+'%';}}}}}},
{{name:'个股收盘价',type:'line',yAxisIndex:1,data:price,connectNulls:true,smooth:true,symbol:'none',lineStyle:{{width:1.5,color:'#2f7ed8'}},itemStyle:{{color:'#2f7ed8'}}}}
]}});
var cs=echarts.init(document.getElementById('{shares_id}'));
cs.setOption({{animationDuration:500,animationEasing:'cubicOut',tooltip:tTip(),
title:{{show:true,text:'{safe_title}｜重仓数量与股价',left:'center',top:2,textStyle:{{color:'#1a1a1a',fontSize:12,fontWeight:700}}}},
legend:{{data:['持股数量','个股收盘价'],left:'center',top:26,textStyle:{{color:'#666',fontSize:9}},itemWidth:12,itemHeight:7}},
grid:{{left:56,right:46,top:50,bottom:42}},
xAxis:{{type:'category',data:dates,axisLabel:Object.assign({{}},AX_L,{{rotate:26,interval:'auto'}}),axisLine:AX_LINE,axisTick:{{show:false}}}},
yAxis:[{{type:'value',axisLabel:AX_L,splitLine:SPLIT,name:'万股',nameTextStyle:{{color:'#888',fontSize:9}}}},
       {{type:'value',scale:true,axisLabel:AX_L,splitLine:{{show:false}},name:'股价',nameTextStyle:{{color:'#888',fontSize:9}}}}],
series:[
{{name:'持股数量',type:'bar',data:shares,barMaxWidth:16,itemStyle:{{color:'rgba(178,58,72,.72)',borderRadius:[3,3,0,0]}}}},
{{name:'个股收盘价',type:'line',yAxisIndex:1,data:price,connectNulls:true,smooth:true,symbol:'none',lineStyle:{{width:1.5,color:'#2f7ed8'}},itemStyle:{{color:'#2f7ed8'}}}}
]}});
var cp=echarts.init(document.getElementById('{pnl_id}'));
cp.setOption({{animationDuration:500,animationEasing:'cubicOut',
title:{{show:true,text:'盈亏估算（万元）｜{safe_title}',left:'center',top:2,textStyle:{{color:'#1a1a1a',fontSize:14,fontWeight:700}}}},
tooltip:Object.assign({{}},TIP,{{trigger:'axis',axisPointer:{{type:'shadow'}},formatter:function(ps){{var p=(ps||[]).find(function(x){{return x.value!=null;}})||ps[0];return p.name+'<br/><b>'+Number(p.value).toLocaleString()+'</b> 万元';}}}}),
grid:{{left:58,right:24,top:42,bottom:44}},
xAxis:{{type:'category',data:qLabels,axisLabel:Object.assign({{}},AX_L,{{rotate:35,interval:0}}),axisLine:AX_LINE,axisTick:{{show:false}}}},
yAxis:{{type:'value',axisLabel:AX_L,splitLine:SPLIT,name:'万元',nameTextStyle:{{color:'#888',fontSize:10}}}},
series:[
{{type:'bar',data:qPosBars,barMaxWidth:24,barGap:'-100%',label:{{show:true,position:'top',distance:3,fontSize:9,color:'#555',formatter:function(p){{return p.value==null?'':Number(p.value).toLocaleString();}}}}}},
{{type:'bar',data:qNegBars,barMaxWidth:24,barGap:'-100%',label:{{show:true,position:'bottom',distance:3,fontSize:9,color:'#555',formatter:function(p){{return p.value==null?'':Number(p.value).toLocaleString();}}}}}}
]
}});
{pe_roe_js}
}})();
"""
    right_html = (
        f"""
  <div class="p9-case-right">
    <div class="p9-case-chart small" id="{pnl_id}"></div>
    <div class="p9-case-chart small" id="{pe_roe_id}"></div>
  </div>"""
        if has_finance_chart
        else f'\n  <div class="p9-case-chart large" id="{pnl_id}"></div>'
    )
    html = f"""
<div class="p9-case-charts" data-stock="{escape(name)}">
  <div class="p9-case-left">
    <div class="p9-case-chart small" id="{weight_id}"></div>
    <div class="p9-case-chart small" id="{shares_id}"></div>
  </div>
{right_html}
</div>"""
    return {"stock": name, "html": html, "js": js}


def part9_case_inline_charts(data):
    charts = []
    for item in data.get("案例个股", []) or []:
        chart = _build_part9_case_chart(item)
        if chart:
            charts.append(chart)
    return charts


def inject_part9_case_charts(text_html, case_charts):
    if not case_charts:
        return text_html, []
    by_stock = {item["stock"]: item for item in case_charts}
    used = set()
    heading_re = re.compile(r"(<h[34][^>]*>)(.*?)(</h[34]>)", re.S)
    matches = list(heading_re.finditer(text_html))
    if not matches:
        return text_html, []

    result = []
    js_list = []
    prev_end = 0
    for idx, match in enumerate(matches):
        result.append(text_html[prev_end:match.end()])
        heading_text = re.sub(r"<[^>]+>", "", match.group(2))
        matched_stock = None
        for stock in by_stock:
            if stock not in used and stock and stock in heading_text:
                matched_stock = stock
                break
        if matched_stock:
            block_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text_html)
            block_html = text_html[match.end():block_end]
            result.append(block_html)
            result.append(by_stock[matched_stock]["html"])
            js_list.append(by_stock[matched_stock]["js"])
            used.add(matched_stock)
            prev_end = block_end
        else:
            prev_end = match.end()
    result.append(text_html[prev_end:])
    return "".join(result), js_list


def chart_part5(data):
    if "年度换手(倍)" not in data or "仓位与集中度" not in data:
        print("  [chart_part5] 警告: 缺少组合管理数据，跳过图表")
        return []
    turnover = data["年度换手(倍)"]
    positions = data["仓位与集中度"]

    t_labels = [y.replace("年", "") for y in sorted(turnover.keys())]
    t_vals = [round(turnover[y]["基金换手率"], 2) for y in sorted(turnover.keys())]

    hp = [str(p["报告期"])[:4] + "/" + str(p["报告期"])[4:6] for p in positions]
    pos_v = [pct(p["股票仓位"]) for p in positions]
    ind5 = [pct(p["前五大行业集中度"]) for p in positions]
    stk10 = [pct(p["前十大个股集中度"]) for p in positions]
    gt1 = [p.get("占比大于1%个股数", p.get("占比大于1%的个股数", 0)) for p in positions]
    pos_min = 0
    pos_max = 100
    gt1_max = math.ceil(max(gt1) + 3) if gt1 else 10

    js_a = """
var _c=echarts.init(document.getElementById('chart-p5a'));
_c.setOption({animationDuration:600,animationEasing:'cubicOut',
title:cTitle('年度换手率（倍）'),
tooltip:Object.assign({},TIP,{trigger:'axis',formatter:function(p){return p[0].axisValue+'<br/>'+p[0].marker+'<b>'+p[0].value.toFixed(2)+' 倍</b>';}}),
legend:{data:['年度换手率'],left:'center',top:28,textStyle:{color:'#666',fontSize:10},itemWidth:16,itemHeight:8},
grid:{left:52,right:24,top:62,bottom:52},dataZoom:dzS(),
xAxis:{type:'category',data:__T_LABELS__,name:'年度',nameLocation:'middle',nameGap:36,nameTextStyle:{color:'#888',fontSize:10},axisLabel:Object.assign({},AX_L,{fontSize:9}),axisLine:AX_LINE,axisTick:{show:false}},
yAxis:{type:'value',axisLabel:Object.assign({},AX_L,{formatter:'{value}x'}),splitLine:SPLIT,name:'换手率（倍）',nameTextStyle:{color:'#888',fontSize:10}},
series:[{name:'年度换手率',type:'line',data:__T_VALS__,smooth:true,...DOT,
itemStyle:{color:'#58a6ff',borderColor:'#161b22',borderWidth:1.5},lineStyle:{width:2.2,color:'#58a6ff'},
areaStyle:{color:new echarts.graphic.LinearGradient(0,0,0,1,[{offset:0,color:'rgba(88,166,255,.18)'},{offset:1,color:'rgba(88,166,255,.01)'}])},
label:suffixLblSkipExtremes(1,__T_VALS__,'x',2,'#555'),
markPoint:mkPt('#58a6ff'),
markLine:{silent:true,symbol:'none',lineStyle:{color:'#f85149',type:'dashed',width:1,opacity:.45},label:{fontSize:8,color:'#8b949e',formatter:'均值 {c}x'},data:[{type:'average'}]}}]
});
""".replace("__T_LABELS__", J(t_labels)).replace("__T_VALS__", J(t_vals))

    js_b = """
var _c=echarts.init(document.getElementById('chart-p5b'));
_c.setOption({animationDuration:600,animationEasing:'cubicOut',tooltip:tTip(),
title:cTitle('仓位与集中度变动'),
legend:{data:['股票仓位','前5行业集中度','前10个股集中度','占比>1%个股数'],left:'center',top:28,textStyle:{color:'#666',fontSize:9},itemWidth:14,itemHeight:7},
grid:{left:52,right:52,top:64,bottom:56},dataZoom:dzS(),
xAxis:{type:'category',data:__HP__,name:'报告期',nameLocation:'middle',nameGap:44,nameTextStyle:{color:'#888',fontSize:10},axisLabel:Object.assign({},AX_L,{rotate:32,interval:0}),axisLine:AX_LINE,axisTick:{show:false}},
yAxis:[{type:'value',min:__POS_MIN__,max:__POS_MAX__,axisLabel:Object.assign({},AX_L,{formatter:'{value}%'}),splitLine:SPLIT,name:'%',nameTextStyle:{color:'#8b949e',fontSize:9}},{type:'value',min:0,max:__GT1_MAX__,axisLabel:{color:'rgba(139,148,158,.6)',fontSize:8,formatter:'{value}'},splitLine:{show:false},name:'只',nameTextStyle:{color:'#8b949e',fontSize:9}}],
series:[
{name:'股票仓位',type:'line',data:__POS__,smooth:true,...DOT,itemStyle:{color:'#58a6ff'},lineStyle:{width:1.8,color:'#58a6ff'},z:3,label:sparseLbl(4)},
{name:'前5行业集中度',type:'line',data:__IND5__,smooth:true,...DOT,itemStyle:{color:'#d29922'},lineStyle:{width:1.8,color:'#d29922'},z:2,label:sparseLbl(4)},
{name:'前10个股集中度',type:'line',data:__STK10__,smooth:true,symbol:'diamond',symbolSize:4,itemStyle:{color:'#3a7d45'},lineStyle:{width:1.8,color:'#3a7d45'},z:1,label:sparseLbl(4)},
{name:'占比>1%个股数',type:'bar',yAxisIndex:1,data:__GT1__,barWidth:12,itemStyle:{color:'rgba(163,113,247,.16)',borderRadius:[3,3,0,0]},z:0}]
});
""".replace("__HP__", J(hp)).replace("__POS__", J(pos_v)).replace("__IND5__", J(ind5)).replace("__STK10__", J(stk10)).replace("__GT1__", J(gt1)).replace("__POS_MIN__", str(pos_min)).replace("__POS_MAX__", str(pos_max)).replace("__GT1_MAX__", str(gt1_max))

    return [{"id": "chart-p5a", "js": js_a}, {"id": "chart-p5b", "js": js_b}]


def chart_part6(data):
    if "估值与盈利" not in data:
        print("  [chart_part6] 警告: 缺少组合特征数据，跳过图表")
        return []
    records = data["估值与盈利"]
    hp = [str(r["报告期"])[:4] + "/" + str(r["报告期"])[4:6] for r in records]
    pe = [r["加权平均PE"] for r in records]
    pb = [r["加权平均PB"] for r in records]
    sz = [r["加权平均SIZE"] for r in records]
    roe = [pct(r["加权平均ROE"]) for r in records]
    div = [pct(r["加权平均股息率"]) for r in records]

    js_a = """
var _c=echarts.init(document.getElementById('chart-p6a'));
_c.setOption({animationDuration:600,animationEasing:'cubicOut',tooltip:tTip(),
title:cTitle('组合加权PE与市值(SIZE)变动'),
legend:{data:['加权PE','加权市值(SIZE)'],left:'center',top:28,textStyle:{color:'#666',fontSize:10},itemWidth:16,itemHeight:8},
grid:{left:52,right:52,top:64,bottom:56},dataZoom:dzS(),
xAxis:{type:'category',data:__HP__,name:'报告期',nameLocation:'middle',nameGap:44,nameTextStyle:{color:'#888',fontSize:10},axisLabel:Object.assign({},AX_L,{rotate:32,interval:0}),axisLine:AX_LINE,axisTick:{show:false}},
yAxis:[{type:'value',axisLabel:AX_L,splitLine:SPLIT,name:'PE（倍）',nameTextStyle:{color:'#888',fontSize:10}},{type:'value',axisLabel:Object.assign({},AX_L,{formatter:function(v){return v>=1000?(v/1000).toFixed(1)+'k':v;}}),splitLine:{show:false},name:'市值（亿元）',nameTextStyle:{color:'#888',fontSize:10}}],
series:[
{name:'加权PE',type:'line',data:__PE__,smooth:true,...DOT,itemStyle:{color:'#58a6ff'},lineStyle:{width:2,color:'#58a6ff'},markPoint:mkPt('#58a6ff'),z:2,label:sparseLbl(4)},
{name:'加权市值(SIZE)',type:'bar',yAxisIndex:1,data:__SZ__,barWidth:14,itemStyle:{color:'rgba(210,153,34,.38)',borderRadius:[3,3,0,0]},z:1}]
});
""".replace("__HP__", J(hp)).replace("__PE__", J(pe)).replace("__SZ__", J(sz))

    js_b = """
var _c=echarts.init(document.getElementById('chart-p6b'));
_c.setOption({animationDuration:600,animationEasing:'cubicOut',tooltip:tTip(),
title:cTitle('组合加权PB、ROE与股息率'),
legend:{data:['加权PB','加权ROE','股息率'],left:'center',top:28,textStyle:{color:'#666',fontSize:10},itemWidth:14,itemHeight:8},
grid:{left:52,right:52,top:64,bottom:56},dataZoom:dzS(),
xAxis:{type:'category',data:__HP__,name:'报告期',nameLocation:'middle',nameGap:44,nameTextStyle:{color:'#888',fontSize:10},axisLabel:Object.assign({},AX_L,{rotate:32,interval:0}),axisLine:AX_LINE,axisTick:{show:false}},
yAxis:[{type:'value',axisLabel:AX_L,splitLine:SPLIT,name:'PB（倍）',nameTextStyle:{color:'#888',fontSize:10}},{type:'value',axisLabel:Object.assign({},AX_L,{formatter:'{value}%'}),splitLine:{show:false},name:'ROE / 股息率 (%)',nameTextStyle:{color:'#888',fontSize:10}}],
series:[
{name:'加权PB',type:'line',data:__PB__,smooth:true,...DOT,itemStyle:{color:'#58a6ff'},lineStyle:{width:2,color:'#58a6ff'},markPoint:mkPt('#58a6ff'),z:2,label:sparseLbl(4)},
{name:'加权ROE',type:'bar',yAxisIndex:1,data:__ROE__,barWidth:14,itemStyle:{color:'rgba(63,185,80,.32)',borderRadius:[3,3,0,0]},z:1},
{name:'股息率',type:'line',yAxisIndex:1,data:__DIV__,smooth:true,...DOT,itemStyle:{color:'#d29922'},lineStyle:{width:2,color:'#d29922',type:'dashed'},z:3}]
});
""".replace("__HP__", J(hp)).replace("__PB__", J(pb)).replace("__ROE__", J(roe)).replace("__DIV__", J(div))

    return [{"id": "chart-p6a", "js": js_a}, {"id": "chart-p6b", "js": js_b}]


def chart_part7(data):
    for key in ["行业配置", "行业配置偏好", "行业累计盈亏(万元)"]:
        if key not in data:
            print(f"  [chart_part7] 警告: 缺少 {key}，跳过图表")
            return []
    alloc = data["行业配置"]
    big_raw = alloc.get("大类板块", [])
    pref = data["行业配置偏好"]
    cum_pnl = data["行业累计盈亏(万元)"]

    # 大类面积 — 年报
    ye = [r for r in big_raw if str(r["报告期"]).endswith("1231")]
    periods = sorted(set(r["报告期"] for r in ye))
    bigs = sorted(set(r["大类"] for r in ye))
    p_labels = [str(p)[:4] for p in periods]
    area_series = []
    for i, cat in enumerate(bigs):
        cm = {r["报告期"]: pct(r["占基金净值比"]) for r in ye if r["大类"] == cat}
        sd = [round(cm.get(p, 0), 2) for p in periods]
        area_series.append({"name": cat, "type": "line", "stack": "Total", "areaStyle": {"opacity": 0.4},
                            "data": sd, "smooth": True, "itemStyle": {"color": sector_color(cat, i)}})

    # 行业偏好
    ps = sorted(pref, key=lambda x: x["次数"])
    p_names = [x["申万一级"] for x in ps]
    p_vals = [x["次数"] for x in ps]

    # 一级累计盈亏 — 盈利降序排左，亏损排在右侧
    lp = cum_pnl.get("一级", {})
    profits = sorted([(k, v) for k, v in lp.items() if v >= 0], key=lambda x: -x[1])
    losses = sorted([(k, v) for k, v in lp.items() if v < 0], key=lambda x: x[1], reverse=True)
    sp = profits + losses
    lpn = [x[0] for x in sp]
    lpv = [x[1] for x in sp]

    js_a = """
var _p7aChart=echarts.init(document.getElementById('chart-p7a'));
var _p7aSeries=__AREA_SERIES__;
var _p7aFocus=null;
function _p7aBuildOption(name){
  var series=JSON.parse(JSON.stringify(_p7aSeries));
  if(name){
    series=series.map(function(s){
      var focused=s.name===name;
      delete s.stack;
      if(focused){
        var color=(s.itemStyle&&s.itemStyle.color)||'#c23b22';
        s.lineStyle={width:2,type:'dashed',color:color,opacity:1};
        s.areaStyle={opacity:0.30};
        s.itemStyle=Object.assign({},s.itemStyle||{},{opacity:1});
      }else{
        s.data=(s.data||[]).map(function(){return null;});
        s.lineStyle={opacity:0};
        s.areaStyle={opacity:0};
        s.itemStyle=Object.assign({},s.itemStyle||{},{opacity:0});
        s.silent=true;
      }
      return s;
    });
  }
  return {animationDuration:name?180:300,animationEasing:'cubicOut',tooltip:tTip(),
    title:cTitle(name?('大类板块配置占比变动（年报）｜'+name):'大类板块配置占比变动（年报）'),
    legend:{show:false,data:__BIGS__,selectedMode:false},
    grid:{left:52,right:20,top:64,bottom:50},
    xAxis:{type:'category',data:__P_LABELS__,name:'年度',nameLocation:'middle',nameGap:28,nameTextStyle:{color:'#888',fontSize:10},axisLabel:AX_L,axisLine:AX_LINE,axisTick:{show:false}},
    yAxis:{type:'value',max:100,axisLabel:Object.assign({},AX_L,{formatter:'{value}%'}),splitLine:SPLIT,name:'占基金净值比 (%)',nameTextStyle:{color:'#888',fontSize:10}},
    series:series};
}
function _p7aInstallHoverLegend(){
  var host=document.getElementById('chart-p7a');
  if(!host||host.querySelector('.p7a-hover-legend'))return;
  host.style.position='relative';
  var legend=document.createElement('div');
  legend.className='p7a-hover-legend';
  legend.style.cssText='position:absolute;left:0;right:0;top:31px;z-index:5;display:flex;align-items:center;justify-content:center;gap:13px;font-size:10px;color:#666;line-height:1;pointer-events:auto;';
  _p7aSeries.forEach(function(s){
    var item=document.createElement('span');
    var color=(s.itemStyle&&s.itemStyle.color)||'#c23b22';
    item.setAttribute('data-name',s.name);
    item.style.cssText='display:inline-flex;align-items:center;gap:4px;cursor:default;white-space:nowrap;';
    item.innerHTML='<i style="display:inline-block;width:14px;height:2px;border-top:2px dashed '+color+'"></i><b style="display:inline-block;width:7px;height:7px;border-radius:50%;background:'+color+'"></b><span>'+s.name+'</span>';
    item.addEventListener('mouseenter',function(){_p7aApplyFocus(s.name);});
    item.addEventListener('mouseleave',function(){_p7aApplyFocus(null);});
    legend.appendChild(item);
  });
  host.appendChild(legend);
}
function _p7aApplyFocus(name){
  _p7aFocus=name;
  _p7aChart.setOption(_p7aBuildOption(name), true);
  _p7aInstallHoverLegend();
}
_p7aChart.setOption(_p7aBuildOption(null), true);
_p7aInstallHoverLegend();
_p7aChart.on('mouseover',function(params){
  if(params && params.componentType==='legend' && params.name){
    _p7aApplyFocus(params.name);
  }
});
_p7aChart.on('mouseout',function(params){
  if(params && params.componentType==='legend'){
    _p7aApplyFocus(null);
  }
});
_p7aChart.getZr().on('globalout',function(){
  _p7aApplyFocus(null);
});
""".replace("__BIGS__", J(bigs)).replace("__P_LABELS__", J(p_labels)).replace("__AREA_SERIES__", J(area_series))

    js_b = """
var _c=echarts.init(document.getElementById('chart-p7b'));
_c.setOption({animationDuration:600,animationEasing:'cubicOut',
title:cTitle('行业配置偏好（出现次数）'),
tooltip:Object.assign({},TIP,{trigger:'axis',axisPointer:{type:'shadow'},formatter:function(p){return p[0].name+'<br/>出现 <b>'+p[0].value+'</b> 次';}}),
legend:{data:['行业频次'],left:'center',top:28,textStyle:{color:'#666',fontSize:10},itemWidth:14,itemHeight:8},
grid:{left:100,right:40,top:58,bottom:20},
xAxis:{type:'value',name:'出现次数',nameLocation:'middle',nameGap:30,nameTextStyle:{color:'#888',fontSize:10},axisLabel:AX_L,splitLine:SPLIT},
yAxis:{type:'category',data:__P_NAMES__,name:'行业',nameTextStyle:{color:'#888',fontSize:10},axisLabel:Object.assign({},AX_L,{fontSize:10}),axisLine:AX_LINE,axisTick:{show:false}},
series:[{name:'行业频次',type:'bar',data:__P_VALS__,barWidth:8,
itemStyle:{color:new echarts.graphic.LinearGradient(0,0,1,0,[{offset:0,color:'#c23b22'},{offset:1,color:'#e05540'}]),borderRadius:[0,5,5,0]},
label:{show:true,position:'right',fontSize:9,color:'#8b949e',formatter:'{c}次'}}]
});
""".replace("__P_NAMES__", J(p_names)).replace("__P_VALS__", J(p_vals))

    n_ind = len(lpn)
    # 动态旋转：行业少时不转，行业多时旋转避免重叠
    rotate = 0 if n_ind <= 10 else 35 if n_ind <= 20 else 55
    font_size = 13 if n_ind <= 15 else 12 if n_ind <= 25 else 11
    bar_w = min(30, max(8, 600 // n_ind))

    js_c = """
var _c=echarts.init(document.getElementById('chart-p7c'));
_c.setOption({animationDuration:600,animationEasing:'cubicOut',
title:cTitle('一级行业累计盈亏（万元）'),
tooltip:Object.assign({},TIP,{trigger:'axis',axisPointer:{type:'shadow'},formatter:function(p){
  var r=p.filter(function(x){return x.value!=null&&x.value!==0;});
  if(!r.length)return '';
  return r[0].name+'<br/><b>'+r[0].value.toLocaleString()+'</b> 万元';}}),
legend:{data:[{name:'盈利',itemStyle:{color:'#c23b22'}},{name:'亏损',itemStyle:{color:'#3a7d45'}}],left:'center',top:28,textStyle:{color:'#666',fontSize:10},itemWidth:14,itemHeight:8},
grid:{left:60,right:20,top:56,bottom:__P7C_BOTTOM__},
xAxis:{type:'category',data:__LPN__,axisLabel:Object.assign({},AX_L,{rotate:__P7C_ROTATE__,fontSize:__P7C_FONT__,interval:0}),axisLine:AX_LINE,axisTick:{show:false}},
yAxis:{type:'value',name:'万元',nameTextStyle:{color:'#888',fontSize:10},axisLabel:AX_L,splitLine:SPLIT},
series:[
{name:'盈利',type:'bar',barGap:'-100%',data:(function(){
var v=__LPV__;return v.map(function(x){return x>=0?{value:x,itemStyle:{color:'#c23b22',borderRadius:[4,4,0,0]}}:{value:null};});})(),
barWidth:__P7C_BAR_W__,label:{show:true,position:'top',distance:4,fontSize:__P7C_LBL_FONT__,color:'#c23b22',formatter:function(p){return p.value!=null?(p.value>=10000||p.value<=-10000?(p.value/10000).toFixed(1)+'万':p.value.toLocaleString()):'';}}},
{name:'亏损',type:'bar',data:(function(){
var v=__LPV__;return v.map(function(x){return x<0?{value:x,itemStyle:{color:'#3a7d45',borderRadius:[0,0,4,4]}}:{value:null};});})(),
barWidth:__P7C_BAR_W__,label:{show:true,position:'bottom',distance:4,fontSize:__P7C_LBL_FONT__,color:'#3a7d45',formatter:function(p){return p.value!=null?(p.value<=-10000?(p.value/10000).toFixed(1)+'万':p.value.toLocaleString()):'';}}}
]
});
""".replace("__LPN__", J(lpn)).replace("__LPV__", J(lpv))
    # 底部留白根据旋转角度动态调整
    bottom = 28 if rotate == 0 else 50 if rotate <= 35 else 72
    js_c = js_c.replace("__P7C_ROTATE__", str(rotate)).replace("__P7C_FONT__", str(font_size)).replace("__P7C_LBL_FONT__", str(font_size - 1)).replace("__P7C_BAR_W__", str(bar_w)).replace("__P7C_BOTTOM__", str(bottom))

    # 纵向柱状图固定高度，不再按行业数线性增长
    top_row_h = 320
    h_a = top_row_h
    h_b = top_row_h
    h_c = 320

    # ── 热力表格：一级行业持仓比例（全量，半年报+年报） ──
    ind_raw = alloc.get("一级行业", [])
    heatmap_html = ""
    if ind_raw:
        pivot = {}
        for r in ind_raw:
            p = str(r["报告期"])
            ind = r.get("申万一级", "")
            pct_v = pct(r.get("占基金净值比", "0"))
            pivot.setdefault(p, {})[ind] = pct_v

        periods = sorted(pivot.keys())
        periods = [p for p in periods if p.endswith("0630") or p.endswith("1231")]
        all_inds = sorted(set(ind for p in periods for ind in pivot.get(p, {})))
        nonzero_inds = [ind for ind in all_inds if any(pivot.get(p, {}).get(ind, 0) > 0 for p in periods)]
        # 按平均仓位降序排列
        nonzero_inds.sort(key=lambda ind: -sum(pivot.get(p, {}).get(ind, 0) for p in periods))

        row_labels = []
        for p in periods:
            yr = p[:4]
            q = "Q2" if p.endswith("0630") else "Q4"
            row_labels.append(f"{yr}{q}")

        matrix = []
        for p in periods:
            row = [round(pivot.get(p, {}).get(ind, 0), 2) for ind in nonzero_inds]
            matrix.append(row)

        max_val = max(max(row) for row in matrix) if matrix else 10

        # 构建 HTML 表格
        def heat_color(val, mx):
            """红色渐变：底色偏暖，越高越深红"""
            if val <= 0:
                return ""
            ratio = min(val / mx, 1.0) if mx > 0 else 0
            # 从暖粉到深红
            r = 255
            g = int(245 - ratio * 130)
            b = int(235 - ratio * 155)
            alpha = 0.35 + ratio * 0.55
            return f"background-color:rgba({r},{g},{b},{alpha:.2f});"

        th_style = 'style="font-weight:600;font-size:11px;color:#444;padding:6px 4px;text-align:center;white-space:nowrap;border-bottom:2px solid #d4c4a8;cursor:pointer"'
        sticky_th_style = 'style="position:sticky;left:0;z-index:3;background:#fff9f0;font-weight:600;font-size:11px;color:#444;padding:6px 8px;min-width:72px;text-align:center;vertical-align:middle;white-space:nowrap;border-bottom:2px solid #d4c4a8;box-shadow:2px 0 4px rgba(0,0,0,.04)"'
        sticky_td_style = 'style="position:sticky;left:0;z-index:2;background:#fffdf9;font-size:11px;font-weight:500;padding:5px 8px;min-width:72px;white-space:nowrap;text-align:center;vertical-align:middle;box-shadow:2px 0 4px rgba(0,0,0,.04)"'
        td_style_tpl = 'style="font-size:11px;text-align:center;padding:5px 4px;white-space:nowrap;{bg}"'

        rows_html = ""
        # 表头行
        rows_html += f"<tr><th {sticky_th_style}>行业名称</th>"
        for j, lbl in enumerate(row_labels):
            rows_html += f'<th {th_style} data-sort-col="{j}" title="点击按该报告期配置比例排序">{lbl}</th>'
        rows_html += "</tr>"
        # 数据行
        for i, ind in enumerate(nonzero_inds):
            rows_html += "<tr>"
            rows_html += f'<td {sticky_td_style}>{ind}</td>'
            for j in range(len(row_labels)):
                v = matrix[j][i]
                bg = heat_color(v, max_val)
                if v > 0:
                    txt = f"{v:.2f}%"
                else:
                    txt = "—"
                    bg = ""
                rows_html += f'<td data-value="{v}" {td_style_tpl.format(bg=bg)}>{txt}</td>'
            rows_html += "</tr>"

        heatmap_html = f'''<div style="margin-top:16px;overflow-x:auto">
<div style="font-size:13px;font-weight:600;color:#1a1a1a;text-align:center;margin-bottom:8px">一级行业持仓比例热力表</div>
<table class="p7-heatmap-table" style="border-collapse:collapse;margin:0 auto;border:1px solid #d4c4a8">
{rows_html}
</table></div>'''

    return_list = [
        {"id": "chart-p7a", "js": js_a, "height": f"{h_a}px"},
        {"id": "chart-p7b", "js": js_b, "height": f"{h_b}px"},
        {"id": "chart-p7c", "js": js_c, "height": f"{h_c}px"},
    ]
    if heatmap_html:
        return_list.append({"id": "heatmap-p7d", "js": "", "html_block": heatmap_html})
    return return_list


def chart_part8(data):
    for key in ["个股配置偏好", "个股累计盈亏(万元)"]:
        if key not in data:
            print(f"  [chart_part8] 警告: 缺少 {key}，跳过图表")
            return []
    pref = data["个股配置偏好"]
    cum_pnl = data["个股累计盈亏(万元)"]

    # 偏好：先取TOP20，再只保留4次及以上的
    t20 = sorted(pref, key=lambda x: x["次数"], reverse=True)[:20]
    qualified = [x for x in t20 if x["次数"] >= 4]
    qualified.sort(key=lambda x: x["次数"])
    fn = [x["股票名称"] for x in qualified]
    fv = [x["次数"] for x in qualified]
    h_a = max(200, len(fn) * 26 + 70)

    # 盈亏TOP10+BOT10 — 从上到下：赚最多→亏最多
    sp = sorted(cum_pnl.items(), key=lambda x: x[1], reverse=True)
    items = sp[:10] + sp[-10:]
    items.reverse()
    pn = [x[0] for x in items]
    pv = [x[1] for x in items]

    js_a = """
var _c=echarts.init(document.getElementById('chart-p8a'));
_c.setOption({animationDuration:600,animationEasing:'cubicOut',
title:cTitle('个股配置偏好 TOP20（出现次数）'),
tooltip:Object.assign({},TIP,{trigger:'axis',axisPointer:{type:'shadow'},formatter:function(p){return p[0].name+'<br/>出现 <b>'+p[0].value+'</b> 次';}}),
legend:{data:['个股频次'],left:'center',top:28,textStyle:{color:'#666',fontSize:10},itemWidth:14,itemHeight:8},
grid:{left:100,right:60,top:44,bottom:56},
xAxis:{type:'value',name:'出现次数',nameLocation:'middle',nameGap:30,nameTextStyle:{color:'#888',fontSize:10},axisLabel:AX_L,splitLine:SPLIT},
yAxis:{type:'category',data:__FN__,axisLabel:Object.assign({},AX_L,{fontSize:10}),axisLine:AX_LINE,axisTick:{show:false}},
series:[{name:'个股频次',type:'bar',data:__FV__,barWidth:10,
itemStyle:{color:new echarts.graphic.LinearGradient(0,0,1,0,[{offset:0,color:'#3d5a98'},{offset:1,color:'#5a78b8'}]),borderRadius:[0,4,4,0]},
label:{show:true,position:'right',fontSize:9,color:'#8b949e',formatter:'{c}次'}}]
});
""".replace("__FN__", J(fn)).replace("__FV__", J(fv))

    js_b = """
var _c=echarts.init(document.getElementById('chart-p8b'));
_c.setOption({animationDuration:600,animationEasing:'cubicOut',
title:cTitle('个股累计盈亏 TOP10盈利 / TOP10亏损（万元）'),
tooltip:Object.assign({},TIP,{trigger:'axis',axisPointer:{type:'shadow'},formatter:function(p){
  var r=p.filter(function(x){return x.value!=null&&x.value!==0;});
  if(!r.length)return '';
  return r[0].name+'<br/><b>'+r[0].value.toLocaleString()+'</b> 万元';}}),
legend:{data:[{name:'盈利',itemStyle:{color:'#c23b22'}},{name:'亏损',itemStyle:{color:'#3a7d45'}}],left:'center',top:28,textStyle:{color:'#666',fontSize:10},itemWidth:14,itemHeight:8},
grid:{left:__P8B_LEFT__,right:__P8B_RIGHT__,top:68,bottom:46},
xAxis:{type:'value',name:'累计盈亏（万元）',nameLocation:'middle',nameGap:__P8B_NG__,nameTextStyle:{color:'#888',fontSize:10},axisLabel:Object.assign({},AX_L,{formatter:(function(){var s=__P8B_STEP__,mn=__P8B_MIN__,mx=__P8B_MAX__;return function(v){if(v===0||v===mn||v===mx)return v.toLocaleString();if(Math.abs(v)%(s*3)===0)return v.toLocaleString();return '';};})()}),splitLine:SPLIT,interval:__P8B_STEP__,min:__P8B_MIN__,max:__P8B_MAX__},
yAxis:{type:'category',data:__PN__,axisLabel:Object.assign({},AX_L,{fontSize:10}),axisLine:AX_LINE,axisTick:{show:false}},
series:[
{name:'盈利',type:'bar',barGap:'-100%',stack:'pnl',data:(function(){
var v=__PV__;return v.map(function(x){return x>=0?{value:x,itemStyle:{color:'#c23b22',borderRadius:[0,4,4,0]}}:{value:null};});})(),
barWidth:10,label:{show:true,position:'right',distance:6,fontSize:9,color:'#c23b22',formatter:function(p){return p.value!=null?'+'+p.value.toLocaleString():'';}}},
{name:'亏损',type:'bar',stack:'pnl2',data:(function(){
var v=__PV__;return v.map(function(x){return x<0?{value:x,itemStyle:{color:'#3a7d45',borderRadius:[4,0,0,4]}}:{value:null};});})(),
barWidth:10,label:{show:true,position:'left',distance:6,fontSize:9,color:'#3a7d45',formatter:function(p){return p.value!=null?p.value.toLocaleString():'';}}}
]
});
""".replace("__PN__", J(pn)).replace("__PV__", J(pv))
    # 动态计算grid left/right、nameGap、xAxis min
    max_abs = max(abs(v) for v in pv) if pv else 1000
    max_neg = min(pv) if pv else 0
    max_len = len(f"{max_abs:,}")
    right_pad = max(60, max_len * 9 + 20)
    # xAxis: interval强制刻度间距，min比最负值多一个刻度用于留白
    def nice_step(v):
        a = abs(v)
        if a <= 0: return 1000
        mag = 10 ** int(len(str(int(a))) - 1)
        return max(1, mag // 2) if a < mag else mag
    step = nice_step(max_neg) if max_neg < 0 else nice_step(max_abs)
    if max_neg < 0:
        x_min = (int(max_neg / step) - 2) * step
    else:
        x_min, step = 0, nice_step(max_abs)
    x_max = math.ceil(max_abs / step) * step
    js_b = js_b.replace("__P8B_LEFT__", "100").replace("__P8B_RIGHT__", str(right_pad)).replace("__P8B_NG__", str(right_pad // 3)).replace("__P8B_MIN__", str(x_min)).replace("__P8B_STEP__", str(step)).replace("__P8B_MAX__", str(x_max))

    h_b = max(400, len(pn) * 26 + 80)

    # ── 前十大持仓变化表格 ──
    holdings = data.get("重仓个股", [])
    holdings_html = ""
    if holdings:
        # 按报告期分组，每期按占比排序取前10
        from collections import defaultdict
        period_stocks = defaultdict(list)
        for r in holdings:
            period_stocks[str(r["报告期"])].append({
                "name": r["股票名称"],
                "pct": pct(r.get("占基金净值比", "0")),
            })
        for p in period_stocks:
            period_stocks[p].sort(key=lambda x: -x["pct"])
            period_stocks[p] = period_stocks[p][:10]

        # 所有报告期按时间倒序排列，最新报告期显示在最上方
        all_periods = sorted(period_stocks.keys(), reverse=True)
        # 动态配色：按频次排序 + 黄金角度分布色相，最大化区分度
        stock_freq = {}
        for ps in period_stocks.values():
            for s in ps:
                stock_freq[s["name"]] = stock_freq.get(s["name"], 0) + 1
        # 按频次降序
        sorted_stocks = sorted(stock_freq, key=lambda x: -stock_freq[x])

        def stock_color(idx):
            hue = (idx * 137.508 + 8) % 360  # 黄金角度，保证相邻项色差最大
            return f"hsl({hue:.0f}, 34%, 83%)", f"hsl({hue:.0f}, 46%, 24%)"

        name_color = {name: stock_color(i) for i, name in enumerate(sorted_stocks)}

        # 表头
        rows_html = '<tr><th class="holdings-period-head">报告期</th>'
        for rank in range(1, 11):
            rows_html += f"<th>第{rank}大</th>"
        rows_html += "</tr>"

        for p in all_periods:
            yr = p[:4]
            suffix = p[4:]
            if suffix == "0331":
                label = f"{yr}Q1"
            elif suffix == "0630":
                label = f"{yr}Q2"
            elif suffix == "0930":
                label = f"{yr}Q3"
            elif suffix == "1231":
                label = f"{yr}Q4"
            else:
                label = p
            rows_html += f'<tr><td class="holdings-period">{escape(label)}</td>'
            stocks = period_stocks[p]
            for rank in range(10):
                if rank < len(stocks):
                    s = stocks[rank]
                    bg_color, fg_color = name_color[s["name"]]
                    stock_name = escape(str(s["name"]))
                    stock_attr = escape(str(s["name"]), quote=True)
                    pct_label = f'{s["pct"]:.2f}%'
                    rows_html += (
                        f'<td class="stk" data-stock="{stock_attr}" title="{stock_attr} {pct_label}" '
                        f'style="--stk-bg:{bg_color};--stk-fg:{fg_color}">'
                        f'<div class="stk-name">{stock_name}</div>'
                        f'<div class="stk-pct">{pct_label}</div>'
                        '</td>'
                    )
                else:
                    rows_html += '<td class="holdings-empty">—</td>'
            rows_html += "</tr>"

        holdings_html = f'''<div class="holdings-table-wrap holdings-heatmap-block">
<div class="holdings-heatmap-title">前十大持仓变化 <span>悬停查看个股轨迹</span></div>
<div class="holdings-heatmap-scroll">
<table class="holdings-heatmap-table">
{rows_html}
</table>
</div></div>'''

    return_list = [
        {"id": "chart-p8a", "js": js_a, "height": f"{h_a}px"},
        {"id": "chart-p8b", "js": js_b, "height": f"{h_b}px"},
    ]
    if holdings_html:
        return_list.append({"id": "holdings-p8c", "js": "", "html_block": holdings_html})
    return return_list


# ═══════════════════════════════════════════════
# HTML 生成
# ═══════════════════════════════════════════════

def generate_html(
    fund_name,
    report_file_override=None,
    out_html_override=None,
    data_dir_override=None,
    web_asset_dir_override=None,
    data_cutoff_date_override=None,
    nav_data_dir_override=None,
):
    data_dir = data_dir_override or os.path.join(ROOT, "深度分析素材", "按Part拆分数据", fund_name)
    fund_out_dir = os.path.join(ROOT, "output", fund_name)
    report_file = report_file_override or find_report(os.path.join(ROOT, "output"), fund_name)
    out_html = out_html_override or os.path.join(fund_out_dir, "report.html")
    web_asset_dir = web_asset_dir_override or os.path.join(ROOT, "ai_assistant", "web")
    web_asset_url = _relative_output_url(out_html, web_asset_dir)
    export_word_api = "report.docx" if out_html_override else _relative_output_url(out_html, os.path.join(ROOT, "api", "export-word"))

    code, fund_short_name, manager, op_range = get_fund_info(data_dir)
    title_text = f"{manager} · 深度分析报告 — {fund_short_name}（{code}）"
    cutoff_text = _format_cn_date(data_cutoff_date_override or load_nav_index_cutoff_date(nav_data_dir_override))
    actual_nav_date = load_nav_index_cutoff_date(nav_data_dir_override)
    subtitle_text = f"{fund_short_name}<span class=\"sep\">|</span>{op_range}<span class=\"sep\">|</span>研究截止日{cutoff_text}<span class=\"sep\">|</span>净值曲线截至{_format_cn_date(actual_nav_date)}"
    if data_dir_override:
        fund_switcher, fund_switcher_options = "", "[]"
    else:
        fund_switcher, fund_switcher_options = fund_switcher_html(fund_name)
    assistant_context = {
        "fund_id": assistant_fund_id(fund_name),
        "fund_name": fund_name,
        "fund_code": code,
        "manager": manager,
    }
    assistant_context_script = (
        "<script>window.FUND_CONTEXT = "
        + json.dumps(assistant_context, ensure_ascii=False)
        + ";</script>"
    )

    p2 = load_json(data_dir, "part2_规模变动.json")
    p3 = load_json(data_dir, "part3_业绩分析.json")
    p4 = load_json(data_dir, "part4_盈亏分析.json")
    p5 = load_json(data_dir, "part5_组合管理.json")
    p6 = load_json(data_dir, "part6_组合特征.json")
    p7 = load_json(data_dir, "part7_行业配置.json")
    p8 = load_json(data_dir, "part8_个股配置.json")
    p9 = load_json(data_dir, "part9_个股案例.json")
    p9_case_charts = part9_case_inline_charts(p9)

    report = load_report(report_file)
    report_parts = split_report_parts(report)
    # 实验平台仅处理公募报告，不隐式读取旧项目的私募补充材料。
    p9_private_holdings = ""

    charts = {
        "part2": chart_part2(p2),
        "part3": chart_part3(p3, fund_name, nav_data_dir_override),
        "part5": chart_part5(p5),
        "part6": chart_part6(p6),
        "part7": chart_part7(p7),
        "part8": chart_part8(p8),
        "part9": [{"html_block": p9_private_holdings}] if p9_private_holdings else [],
    }
    # Part4 特殊处理：返回 overview + per_year
    p4_result = chart_part4(p4)
    p4_per_year = None
    if isinstance(p4_result, dict) and "overview" in p4_result:
        charts["part4"] = p4_result["overview"]
        p4_per_year = p4_result.get("per_year")
    else:
        charts["part4"] = p4_result

    part_meta = {
        "Part1：基本概况（从业经历+投资框架+投资实践）": ("part1", "基本概况", "从业经历 · 投资框架 · 投资实践"),
        "Part2：规模变动（基金规模+管理规模）": ("part2", "规模变动", "基金规模 · 管理规模"),
        "Part3：业绩分析（业绩超额+回撤控制+持有体验）": ("part3", "业绩分析", "超额收益 · 回撤控制 · 持有体验"),
        "Part4：盈亏分析（逐年分析+结合超额+投资逻辑）": ("part4", "盈亏分析", "逐年盈亏 · 投资逻辑交叉验证"),
        "Part5：组合管理（交易换手+仓位择时+持股集中度）": ("part5", "组合管理", "换手率 · 仓位择时 · 集中度"),
        "Part6：组合特征（市值分布+估值分布+盈利与分红）": ("part6", "组合特征", "市值 · 估值 · 盈利与分红"),
        "Part7：行业配置（配置偏好+盈亏贡献+配置调整）": ("part7", "行业配置", "偏好 · 盈亏 · 调整"),
        "Part8：个股配置（配置偏好+盈利贡献+配置调整）": ("part8", "个股配置", "持仓频次 · 盈亏 · 调整"),
        PART9_TITLE: ("part9", "案例分析", "分级案例 · 横向专题 · 框架验证"),
        PART10_TITLE: ("part10", "投资能力", "中观轮动 · 选股能力"),
        "总结：投资追溯与复盘分析": ("summary", "投资复盘总结", "知行果评价 · 能力圈 · 配置建议"),
    }

    base_tab_defs = [
        ("part1", "基本概况"), ("part2", "规模变动"), ("part3", "业绩分析"),
        ("part4", "盈亏分析"), ("part5", "组合管理"), ("part6", "组合特征"),
        ("part7", "行业配置"), ("part8", "个股配置"), ("part9", "案例分析"),
        ("part10", "投资能力"), ("summary", "投资复盘"),
    ]
    present_tab_keys = {
        part_meta[pk][0]
        for pk in report_parts
        if pk in part_meta
    }
    tab_defs = [item for item in base_tab_defs if item[0] in present_tab_keys]
    first_tab_key = tab_defs[0][0] if tab_defs else "part1"
    tab_order_json = json.dumps([key for key, _label in tab_defs], ensure_ascii=False)

    part_label_by_key = {}
    part_index = 1
    for key, _label in tab_defs:
        if key == "summary":
            part_label_by_key[key] = "总结"
        else:
            part_label_by_key[key] = f"Part{part_index}"
            part_index += 1

    tab_btns = []
    for i, (key, label) in enumerate(tab_defs):
        cls = ' class="active"' if i == 0 else ''
        tab_btns.append(
            f'<button{cls} onclick="switchTab(\'{key}\')">'
            f'<span class="part-label">{part_label_by_key[key]}</span>'
            f'<span class="part-name">{label}</span></button>'
        )
    tab_nav = '<div class="tab-nav">\n' + '\n'.join(tab_btns) + '\n</div>'

    panels = []
    p9_inline_js = []
    for pk in report_parts:
        if pk not in part_meta:
            continue
        tab_key, short_title, badge_text = part_meta[pk]
        part_md = report_parts[pk].lstrip("\n")
        # 提取Part总结
        summary_match = re.search(r'^【总结】(.+)$', part_md, re.MULTILINE)
        summary_text = ""
        if summary_match:
            summary_text = collapse_repeated_summary_terms(summary_match.group(1).strip())
            part_md = re.sub(r'^【总结】.+$\n?', '', part_md, flags=re.MULTILINE)
        # 把开头的 # 一级标题降为 ## 二级标题（tab 面板已展示 part 标题）
        if part_md.startswith("# ") and not part_md.startswith("## "):
            part_md = re.sub(r"^# ", "## ", part_md, count=1)
        text_html = normalize_subheadings_html(md_to_html(part_md))
        if tab_key == "part6" and p6.get("最新季度快照"):
            snapshot = p6["最新季度快照"]
            def display(value):
                return "暂无数据" if value is None else escape(str(value))
            metrics = ["样本净值覆盖率", "加权平均PE", "加权平均PB", "加权平均ROE", "加权平均股息率"]
            rows = md_to_html("| 指标 | 数值 |\n|---|---|\n" + "\n".join(f"| {key} | {display(snapshot.get(key))} |" for key in metrics))
            full_periods = [str(row.get("报告期", "")) for row in p6.get("估值与盈利", [])]
            full_date = max(full_periods) if full_periods else "暂无完整持仓"
            note = f"完整持仓分析截至{full_date}；下列快照截至{snapshot.get('报告期', '')}，仅为前十大样本，不能当作全组合。"
            text_html = '<div class="quarterly-snapshot"><h3>最新季度前十大快照</h3><p>' + escape(note) + '</p>' + rows + '<p>加权指标按有效样本重新归一化；PE裁剪至0—200，PB裁剪至0—20，ROE裁剪至−50%—100%。有效样本和裁剪影响权重见下表。</p>' + md_to_html("| 指标 | 有效样本 | 有效市值权重 | 裁剪影响净值权重 |\n|---|---|---|---|\n" + "\n".join(f"| {key} | {display(value.get('有效样本数'))} | {display(value.get('有效市值权重'))} | {display(value.get('裁剪影响净值权重'))} |" for key, value in snapshot.get("指标覆盖", {}).items())) + '</div>' + text_html
        if tab_key == "part8" and p8.get("最新季度快照"):
            snapshot = p8["最新季度快照"]
            text_html = '<p class="quarterly-snapshot">最新季度持仓日期：' + escape(str(snapshot.get("报告期", ""))) + '；季度披露为前十大样本，未进入前十大不代表清仓。</p>' + text_html
        if tab_key in {"part4", "part9"}:
            text_html = '<p class="data-method-note">持仓盈亏沿用原有披露数据估算方法，不代表真实交易流水或精确损益。</p>' + text_html
        # Part4: 注入逐年图表到对应年份段
        if tab_key == "part4" and p4_per_year:
            text_html, _ = inject_per_year_charts(text_html, p4_per_year)
        if tab_key == "part9" and p9_case_charts:
            text_html = (
                '<p class="p9-case-note">说明：本章案例股选取累计盈利前五与累计亏损前五，共十只股票；'
                '后文的分级、专题和复盘均基于这十只案例股展开。</p>'
                + text_html
            )
            text_html, p9_case_js = inject_part9_case_charts(text_html, p9_case_charts)
            p9_inline_js.extend(p9_case_js)
        active = ' active' if tab_key == first_tab_key else ''

        chart_html = ""
        extra_html = ""
        if tab_key in charts:
            clist = charts[tab_key]
            # 分离 ECharts 图表和纯 HTML 块
            echarts_list = [ch for ch in clist if ch.get("js")]
            html_blocks = [ch for ch in clist if ch.get("html_block")]
            column_items_list = [ch for ch in clist if ch.get("column_items")]

            n = len(echarts_list)
            if column_items_list:
                # flex 布局：echarts 图表 + column_items 并排，左右高度对齐
                chart_html = '<div class="chart-row" style="align-items:stretch">'
                for ch in echarts_list:
                    f = ch.get("flex", 1)
                    h = ch.get("height", "500px")
                    chart_html += f'<div style="flex:{f};min-width:0"><div class="chart-inner" id="{ch["id"]}" style="height:{h}"></div></div>'
                for col in column_items_list:
                    f = col.get("flex", 1)
                    chart_html += f'<div style="flex:{f};min-width:0;display:flex;flex-direction:column;gap:12px;justify-content:flex-start">'
                    for item in col["column_items"]:
                        if item.get("js"):
                            h = item.get("height", "320px")
                            chart_html += f'<div class="chart-inner" id="{item["id"]}" style="height:{h}"></div>'
                        elif item.get("html_block"):
                            chart_html += item["html_block"]
                    chart_html += '</div>'
                chart_html += '</div>'
            elif n == 1:
                h = echarts_list[0].get("height", "500px")
                chart_html = f'<div class="chart-row"><div class="chart-inner tall" id="{echarts_list[0]["id"]}" style="height:{h}"></div></div>'
            elif n == 2 and echarts_list[0].get("stacked"):
                for ch in echarts_list:
                    h = ch.get("height", "400px")
                    chart_html += f'<div class="chart-row"><div class="chart-inner tall" id="{ch["id"]}" style="height:{h}"></div></div>\n'
            elif n == 2:
                f0 = echarts_list[0].get("flex", 1)
                f1 = echarts_list[1].get("flex", 1)
                chart_html = '<div class="chart-row">'
                chart_html += f'<div style="flex:{f0};min-width:0"><div class="chart-inner" id="{echarts_list[0]["id"]}"></div></div>'
                chart_html += f'<div style="flex:{f1};min-width:0"><div class="chart-inner" id="{echarts_list[1]["id"]}"></div></div>'
                chart_html += '</div>'
            elif n == 3:
                h3 = echarts_list[2].get("height", "400px")
                chart_html = '<div class="chart-row">'
                h1 = echarts_list[0].get("height", "400px")
                h2 = echarts_list[1].get("height", "400px")
                chart_html += f'<div class="chart-half"><div class="chart-inner" id="{echarts_list[0]["id"]}" style="height:{h1}"></div></div>'
                chart_html += f'<div class="chart-half"><div class="chart-inner" id="{echarts_list[1]["id"]}" style="height:{h2}"></div></div>'
                chart_html += '</div>\n'
                chart_html += '<div class="chart-row">'
                chart_html += f'<div class="chart-inner" id="{echarts_list[2]["id"]}" style="height:{h3}"></div>'
                chart_html += '</div>'
            elif n >= 4:
                chart_html = '<div class="chart-row">'
                chart_html += f'<div class="chart-half"><div class="chart-inner" id="{echarts_list[0]["id"]}"></div></div>'
                chart_html += f'<div class="chart-half"><div class="chart-inner" id="{echarts_list[1]["id"]}"></div></div>'
                chart_html += '</div>\n'
                h2 = echarts_list[2].get("height", "400px")
                chart_html += f'<div class="chart-row"><div class="chart-inner" id="{echarts_list[2]["id"]}" style="height:{h2}"></div></div>\n'
                for i in range(3, n):
                    hi = echarts_list[i].get("height", "400px")
                    chart_html += f'<div class="chart-row"><div class="chart-inner" id="{echarts_list[i]["id"]}" style="height:{hi}"></div></div>\n'

            # 纯 HTML 块（如热力表格）
            for blk in html_blocks:
                extra_html += blk["html_block"]

        title_base = f"{part_label_by_key.get(tab_key, '')} {short_title}".strip()
        title_display = f"{title_base}：{summary_text}" if summary_text else title_base
        panels.append(f'''<div class="tab-panel{active}" id="tab-{tab_key}">
  <div class="card">
    <div class="card-head">
      <div class="head-left">
        <button class="btn-toggle" onclick="toggleCard(this)">−</button>
        <span class="title">{title_display}</span>
      </div>
      <span class="badge">{badge_text}</span>
    </div>
    <div class="card-body">
      <div class="prose">{text_html}</div>
      {chart_html}
      {extra_html}
    </div>
  </div>
</div>''')

    panels_html = '\n\n'.join(panels)

    # 图表JS按Tab分组
    chart_js_map = {}
    for pkey, clist in charts.items():
        js_list = [ch["js"] for ch in clist if ch.get("js")]
        for ch in clist:
            if ch.get("column_items"):
                for item in ch["column_items"]:
                    if item.get("js"):
                        js_list.append(item["js"])
        chart_js_map[pkey] = js_list
    # Part4: 添加逐年图表JS
    if p4_per_year:
        for _yn, pyd in sorted(p4_per_year.items()):
            chart_js_map.setdefault("part4", []).extend(pyd["js_list"])
    if p9_inline_js:
        chart_js_map.setdefault("part9", []).extend(p9_inline_js)
    chart_js_map = filter_chart_js_for_existing_targets(chart_js_map, panels_html)

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="icon" href="data:,">
<title>{title_text}</title>
<script src="{web_asset_url}/echarts.min.js"></script>
<link rel="stylesheet" href="{web_asset_url}/sidebar.css?v=20260625-chat-ui">
<style>
  :root {{
    --bg: #f6f1e8; --card-bg: #fffdf8; --border: #1f1f1f;
    --text: #1a1a1a; --text-dim: #4a4a4a; --text-bright: #1a1a1a;
    --positive: #c23b22; --negative: #3a7d45; --accent: #e3120b;
    --accent-dim: rgba(227,18,11,0.10); --warn: #b78410; --warn-bg: rgba(183,132,16,0.1);
    --purple: #8b6b47; --radius: 6px; --radius-sm: 4px;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: "Microsoft YaHei","PingFang SC","Helvetica Neue",sans-serif;
    background: var(--bg);
    background-image: repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(212,196,168,0.03) 2px,rgba(212,196,168,0.03) 4px);
    color: var(--text); padding: 0 32px 72px;
    display: flex; flex-direction: column; align-items: center; gap: 0;
    line-height: 1.65; font-size: 16px;
  }}
  .master-header {{ text-align: center; padding: 28px 20px 16px; width: 100%; max-width: 1180px; }}
  .master-header .fund-code {{ font-size: 13px; color: var(--text-dim); letter-spacing: 0.5px; margin-bottom: 8px; font-weight: 500; }}
  .master-header h1 {{ font-size: 36px; font-weight: 700; margin: 4px 0 12px; letter-spacing: -0.5px; }}
  .master-header h1 .mgr-name {{ color: var(--accent); }}
  .master-header .sub-title {{ font-size: 15px; color: var(--text-dim); }}
  .master-header .sub-title .sep {{ color: var(--border); margin: 0 6px; }}

  .metrics-table {{
    width: 100%; border-collapse: collapse; font-size: 12px;
    background: var(--card-bg); border: 1px solid var(--border); border-radius: var(--radius-sm);
  }}
  .metrics-table td {{ padding: 7px 10px; border-bottom: 1px solid var(--border); }}
  .metrics-table tr:last-child td {{ border-bottom: none; }}
  .metrics-table th {{ padding: 7px 8px; border-bottom: 1px solid var(--border); color: var(--text-dim); font-size: 12px; font-weight: 600; text-align: center; background: #fafaf5; }}
  .markdown-table-wrap {{ overflow-x: auto; margin: 12px 0 16px; }}
  .markdown-table {{ width: 100%; border-collapse: collapse; font-size: 13px; line-height: 1.5; background: #fff; border: 1px solid var(--border); }}
  .markdown-table th {{ padding: 8px 10px; border-bottom: 1px solid var(--border); color: var(--text-bright); font-weight: 700; text-align: left; background: #fafaf5; white-space: nowrap; }}
  .markdown-table td {{ padding: 8px 10px; border-bottom: 1px solid var(--border); color: var(--text); vertical-align: top; }}
  .markdown-table tr:last-child td {{ border-bottom: none; }}
  .mt-lbl {{ color: var(--text-dim); font-weight: 500; white-space: nowrap; width: 25%; }}
  .mt-val {{ font-weight: 700; font-size: 13px; }}
  .mt-val.pos {{ color: var(--positive); }}
  .mt-val.neg-color {{ color: var(--negative); }}
  .mt-val.accent {{ color: var(--accent); }}
  .mt-val.warn {{ color: var(--warn); }}
  .mt-val.bright {{ color: var(--text-bright); }}
  .private-performance-block {{ margin-top: 14px; }}
  .private-performance-title {{ font-size: 14px; font-weight: 700; color: var(--text-bright); margin: 0 0 6px; }}
  .private-performance-table {{ font-size: 11px; }}
  .private-performance-table td {{ padding: 7px 8px; text-align: center; vertical-align: middle; }}
  .private-performance-table td:first-child {{ font-weight: 700; color: var(--text-bright); white-space: nowrap; }}
  .private-performance-wide {{ margin-top: 0; }}
  .private-holdings-block {{ margin-top: 16px; }}
  .private-holdings-scroll {{ width: 100%; overflow-x: auto; }}
	  .private-holdings-table {{ min-width: 520px; font-size: 12px; }}
	  .private-holdings-table th, .private-holdings-table td {{ text-align: center; white-space: nowrap; }}
	  .private-holdings-table td:first-child {{ font-weight: 700; color: var(--text-bright); text-align: left; }}
	  .private-holdings-note {{ margin-top: 5px; color: var(--text-dim); font-size: 12px; }}
	  .holdings-heatmap-block {{ margin-top: 20px; }}
	  .holdings-heatmap-title {{
	    margin: 0 0 10px; text-align: center;
	    font-size: 17px; font-weight: 800; color: var(--text-bright);
	  }}
	  .holdings-heatmap-title span {{
	    margin-left: 6px; font-size: 12px; font-weight: 500; color: var(--text-dim);
	  }}
	  .holdings-heatmap-scroll {{ width: 100%; overflow-x: auto; padding-bottom: 2px; }}
	  .holdings-heatmap-table {{
	    min-width: 920px; margin: 0 auto; border-collapse: collapse;
	    background: #fffdf8; border: 1px solid var(--border);
	    font-size: 12px; line-height: 1.25;
	  }}
	  .holdings-heatmap-table th,
	  .holdings-heatmap-table td {{
	    border: 1px solid rgba(31,31,31,.22);
	    padding: 7px 8px; text-align: center; white-space: nowrap;
	  }}
	  .holdings-heatmap-table th {{
	    background: #f1e6d5; color: #2f2922; font-weight: 800;
	  }}
	  .holdings-heatmap-table .holdings-period,
	  .holdings-heatmap-table .holdings-period-head {{
	    min-width: 74px; background: #faf3e8; color: var(--text-bright);
	    font-weight: 800;
	  }}
	  .holdings-heatmap-table .stk {{
	    min-width: 78px; background: var(--stk-bg); color: var(--stk-fg);
	    font-weight: 800; cursor: pointer; transition: opacity .15s ease, filter .15s ease;
	  }}
	  .holdings-heatmap-table .stk:hover {{ filter: saturate(1.08) brightness(.98); }}
	  .holdings-heatmap-table .stk-name {{ font-size: 12px; }}
	  .holdings-heatmap-table .stk-pct {{ margin-top: 2px; font-size: 10px; font-weight: 700; opacity: .72; }}
	  .holdings-heatmap-table .holdings-empty {{ color: #b2a894; background: #fbf8f1; }}

	  .tab-nav {{
    width: 100%; max-width: 1180px; display: flex; gap: 0;
    padding: 10px 0 20px; position: sticky; top: 0; z-index: 100; background: var(--bg);
  }}
  .tab-nav button {{
    flex: 1; padding: 13px 10px; font-size: 13px; font-weight: 500;
    color: var(--text-dim); background: var(--card-bg);
    border: 1px solid var(--border); cursor: pointer; transition: all 0.2s ease;
    font-family: inherit; letter-spacing: 0.3px;
  }}
  .tab-nav button:first-child {{ border-radius: var(--radius) 0 0 var(--radius); }}
  .tab-nav button:last-child {{ border-radius: 0 var(--radius) var(--radius) 0; }}
  .tab-nav button.active {{
    background: var(--accent-dim); color: var(--accent); border-color: var(--accent); z-index: 2;
  }}
  .tab-nav button:hover:not(.active) {{ color: var(--text-bright); border-color: var(--text-dim); }}
  .tab-panel {{ display: none; width: 100%; max-width: 1180px; }}
  .tab-panel.active {{ display: block; }}

  .card {{
    background: var(--card-bg); border: 1px solid var(--border);
    border-radius: var(--radius); overflow: hidden;
    box-shadow: 0 2px 8px rgba(44,36,22,0.08);
  }}
  .card-head {{
    display: flex; align-items: center; justify-content: space-between;
    padding: 16px 24px; border-bottom: 1px solid var(--border);
    background: #fafaf5; user-select: none;
  }}
  .card-head .head-left {{ display: flex; align-items: center; gap: 14px; }}
  .card-head .title {{ font-size: 16px; font-weight: 600; color: var(--text-bright); }}
  .card-head .badge {{ font-size: 12px; color: var(--text-dim); background: #f0ebe0; padding: 4px 12px; border-radius: 14px; }}
  .btn-toggle {{
    background: none; border: 1px solid var(--border); color: var(--text-dim);
    width: 28px; height: 28px; border-radius: var(--radius-sm);
    cursor: pointer; font-size: 15px; line-height: 1;
    display: flex; align-items: center; justify-content: center;
    transition: all 0.15s ease; flex-shrink: 0;
  }}
  .btn-toggle:hover {{ color: var(--accent); border-color: var(--accent); }}
  .card.collapsed .card-body {{ display: none; }}
  .card.collapsed .card-head {{ border-bottom: none; }}
  .card-body {{ padding: 20px 24px; }}

  .prose {{ font-size: 15px; color: var(--text); line-height: 1.75; margin-bottom: 20px; }}
  .prose h2 {{ font-size: 18px; font-weight: 700; color: var(--accent); margin: 22px 0 10px; padding-bottom: 6px; border-bottom: 1px solid var(--border); }}
  .prose h3 {{ font-size: 18px; font-weight: 700; color: var(--accent); margin: 22px 0 10px; padding-bottom: 6px; border-bottom: 1px solid var(--border); }}
  .prose h4 {{
    display: block; max-width: 100%;
    font-size: 16px; font-weight: 800; color: #8f2016;
    margin: 18px 0 10px; padding: 0;
    line-height: 1.35;
  }}
  .prose h5 {{
    font-size: 15.5px; font-weight: 800; color: var(--text-bright);
    margin: 16px 0 8px; line-height: 1.45;
  }}
  .prose p {{ margin-bottom: 10px; text-align: justify; }}
  .prose strong {{ color: var(--text-bright); font-weight: 700; }}
  .prose ul {{ list-style: none; padding-left: 0; margin: 8px 0 12px; }}
  .prose ul li {{ padding: 4px 0 4px 20px; position: relative; font-size: 14px; }}
  .prose ul li::before {{ content: '•'; position: absolute; left: 4px; color: var(--accent); font-weight: 700; }}

  .chart-row {{ display: flex; gap: 16px; margin-top: 16px; }}
  .chart-half {{ flex: 1; min-width: 0; }}
  .chart-inner {{ width: 100%; height: 400px; background: #fafaf7; border-radius: var(--radius-sm); border: 1px solid var(--border); }}
  .chart-inner.tall {{ height: 500px; }}
  .p9-case-note {{ margin: 0 0 16px; padding: 10px 12px; border-left: 3px solid var(--accent); background: rgba(194,59,34,.06); color: #444; font-size: 15px; line-height: 1.75; }}
  .p9-case-charts {{
    display: grid; grid-template-columns: minmax(0, 1.08fr) minmax(0, .92fr);
    gap: 10px; margin: 8px 0 16px; padding: 8px;
    background: #fafaf7; border: 1px solid var(--border); border-radius: var(--radius-sm);
  }}
  .p9-case-left {{ display: grid; grid-template-rows: 1fr 1fr; gap: 6px; min-width: 0; }}
  .p9-case-right {{ display: grid; grid-template-rows: 1fr 1fr; gap: 6px; min-width: 0; }}
  .p9-case-chart {{ min-width: 0; background: #fffdf8; border: 1px solid rgba(17,17,17,.18); border-radius: var(--radius-sm); }}
  .p9-case-chart.small {{ height: 220px; }}
  .p9-case-chart.large {{ height: 446px; }}
  .p4-year-charts-row {{ display: flex; gap: 8px; margin: 10px 0 8px; }}
  .p4-yc {{ flex: 1; min-width: 0; height: 260px; background: #fafaf7; border-radius: var(--radius-sm); border: 1px solid var(--border); }}
  .p4-yc-stack {{ flex: 0.5; min-width: 100px; display: flex; flex-direction: column; gap: 4px; }}
  .p4-yc-sm-item {{ flex: 1; min-height: 0; background: #fafaf7; border-radius: var(--radius-sm); border: 1px solid var(--border); }}
  .p4-detail-table-row {{ display: flex; gap: 16px; margin-top: 14px; align-items: flex-start; }}
  .p4-detail-table-block {{
    flex: 1; min-width: 0; background: rgba(255,253,248,.82);
  }}
  .p4-detail-title {{
    margin-bottom: 6px; text-align: center; color: #111;
    font-size: 15px; font-weight: 800;
    font-family: Georgia, "Times New Roman", "Microsoft YaHei", serif;
  }}
  .p4-detail-scroll {{ width: 100%; overflow-x: auto; border: 1px solid rgba(17,17,17,.22); }}
  .p4-detail-table {{
    width: 100%; border-collapse: collapse; table-layout: fixed;
    background: #fffdf8; font-size: 11px; line-height: 1.16;
  }}
  .p4-detail-table th {{
    padding: 8px 5px; background: #efe7d7; color: #24211d;
    border-right: 1px solid rgba(36,33,29,.24);
    font-weight: 700; text-align: center;
  }}
  .p4-detail-table th:last-child {{ border-right: none; }}
  .p4-detail-table td {{
    height: 38px; padding: 4px 3px; text-align: center; vertical-align: middle;
    border-top: 1px solid rgba(17,17,17,.16); border-right: 1px solid rgba(17,17,17,.12);
    color: #222; background: #fffdf8; word-break: keep-all; overflow-wrap: anywhere;
  }}
  .p4-detail-table td:last-child {{ border-right: none; }}
  .p4-detail-table tr:nth-child(even) td {{ background: #faf6ed; }}
  .p4-detail-table td span {{ display: inline-block; margin-top: 1px; font-size: 10.5px; font-weight: 700; opacity: .78; }}
  .p4-detail-table .p4-loss {{ color: #3a7d45; }}
  .p4-detail-table .p4-profit {{ color: #c23b22; }}
  .p4-detail-table .p4-empty {{ color: rgba(17,17,17,.72); }}
  .p4-matrix-table .p4-row-head {{
    width: 54px; white-space: nowrap;
    font-weight: 800; color: #111; background: #faf6ed;
    position: sticky; left: 0; z-index: 1;
  }}
  .p4-matrix-table th {{ padding: 7px 3px; white-space: nowrap; }}
  .p4-matrix-table td {{ padding: 4px 2px; }}
  .p4-detail-table td[data-name] {{ transition: opacity .14s ease, filter .14s ease; }}
  .p4-detail-table.is-filtering td[data-name]:not(.is-active) {{ opacity: .32; filter: grayscale(.12); }}
  .p4-detail-table td.is-active {{ opacity: 1; filter: none; }}
  .footnote {{ font-size: 12px; color: var(--text-dim); text-align: center; padding: 20px 0; max-width: 1180px; width: 100%; }}

  @media (max-width: 640px) {{
    .chart-row {{ flex-direction: column; }}
    .chart-inner {{ height: 340px; }}
    .chart-inner.tall {{ height: 420px; }}
    .p9-case-charts {{ grid-template-columns: 1fr; }}
    .p9-case-chart.small, .p9-case-chart.large {{ height: 220px; }}
    .p4-year-charts-row {{ flex-direction: column; }}
    .p4-yc {{ height: 220px; }}
    .p4-yc-stack {{ flex: 1; }}
    .p4-yc-sm-item {{ min-height: 120px; }}
    .p4-detail-table-row {{ flex-direction: column; gap: 14px; }}
    .p4-detail-table-block {{ width: 100%; }}
    .p4-detail-table {{ min-width: 560px; }}
    .tab-nav button {{ font-size: 11px; padding: 10px 6px; }}
    body {{ font-size: 14px; padding: 0 12px 40px; }}
		}}

  html, body {{ height: 100%; }}
  body {{
    padding: 0; overflow: hidden; display: block; background: var(--bg);
  }}
  .report-shell {{
    display: grid; grid-template-columns: 184px minmax(0, 1fr);
    grid-template-rows: auto minmax(0, 1fr);
    width: 100%; height: 100vh; overflow: hidden;
  }}
  .report-sidebar {{
    min-height: 0; overflow: hidden; padding: 0 10px 14px;
    background: linear-gradient(180deg, rgba(227,18,11,.08), transparent 22%), #f8f1e5;
    border-right: 2px solid #111; box-shadow: 4px 0 0 rgba(227,18,11,.14), 10px 0 18px rgba(0,0,0,.05);
    display: flex; flex-direction: column;
  }}
  .sidebar-title {{
    position: relative; font-family: Georgia, "Times New Roman", "Microsoft YaHei", serif;
    font-size: 20px; font-weight: 700; color: #fff; text-align: center;
    margin: 0 -10px 12px; letter-spacing: .4px; line-height: 1.2;
    padding: 15px 10px 14px; background: var(--accent); border-bottom: 2px solid #111;
  }}
  .sidebar-title::after {{
    content: ''; position: absolute; left: 14px; right: 14px; bottom: 5px; height: 1px;
    background: rgba(255,255,255,.65);
  }}
  .master-header {{
    grid-column: 1 / -1; max-width: none; text-align: center; padding: 12px 32px 10px;
    width: 100%; border-bottom: 2px solid #111; background: #f8f1e5;
    position: relative; min-height: 67px; padding-left: 212px; padding-right: 128px;
  }}
  .manager-filter {{
    position: absolute; left: 0; top: 50%; transform: translateY(-50%);
    z-index: 160; width: 184px; padding: 0; font-family: Georgia, "Times New Roman", "Microsoft YaHei", serif;
  }}
  .manager-toggle {{
    width: 80%; min-height: 34px; margin: 0 auto;
    display: grid; grid-template-columns: 1fr 18px; align-items: center; gap: 6px;
    border: 1px solid rgba(17,17,17,.28); border-left: 2px solid #e3120b;
    background: transparent; color: #111; padding: 5px 8px 5px 10px; cursor: pointer; text-align: left;
    font-family: inherit;
  }}
  .manager-toggle:hover,
  .manager-filter.open .manager-toggle {{ background: rgba(255,253,248,.48); }}
  .manager-toggle strong {{ display: block; font-size: 12px; line-height: 1.15; white-space: nowrap; }}
  .manager-caret {{ font-size: 13px; color: #e3120b; text-align: center; transition: transform .16s ease; }}
  .manager-filter.open .manager-caret {{ transform: rotate(180deg); }}
  .manager-select {{
    display: none; position: absolute; left: 10%; top: calc(100% + 6px); z-index: 170; width: 80%;
    background: rgba(248,241,229,.78); border: 1px solid rgba(17,17,17,.35);
    box-shadow: 0 14px 24px rgba(0,0,0,.10); backdrop-filter: blur(5px);
    max-height: min(54vh, 500px); overflow: hidden;
  }}
  .manager-filter.open .manager-select {{ display: block; }}
  .manager-search {{
    width: calc(100% - 16px); border: 1px solid rgba(17,17,17,.22); outline: 0;
    background: rgba(255,253,248,.72); margin: 8px 8px 6px; padding: 7px 8px;
    color: #111; font-size: 12px; font-weight: 700; text-align: left; font-family: inherit;
  }}
  .manager-options {{
    max-height: calc(min(54vh, 500px) - 52px); overflow-y: auto; overscroll-behavior: contain;
  }}
  .manager-options::-webkit-scrollbar {{ width: 6px; }}
  .manager-options::-webkit-scrollbar-track {{ background: rgba(17,17,17,.06); }}
  .manager-options::-webkit-scrollbar-thumb {{ background: rgba(17,17,17,.28); border-radius: 999px; }}
  .manager-option {{
    display: block; width: 100%; padding: 9px 10px; border: 0; border-top: 1px solid rgba(17,17,17,.13);
    background: transparent; color: #111; text-align: left; cursor: pointer; font-family: inherit;
  }}
  .manager-option:hover {{ background: rgba(227,18,11,.07); }}
  .manager-option.active {{ background: rgba(17,17,17,.88); color: #fff; box-shadow: inset 3px 0 0 #e3120b; }}
  .manager-option span {{ display: block; font-size: 12px; font-weight: 700; line-height: 1.18; }}
  .manager-option small {{ display: none; }}
  .manager-options.empty::after {{
    content: '未找到匹配基金'; display: block; padding: 10px; font-size: 12px; color: rgba(17,17,17,.58); text-align: center;
  }}
  .master-header h1 {{
    font-family: Georgia, "Times New Roman", "Microsoft YaHei", serif;
    font-size: 28px; font-weight: 700; margin: 0 0 4px;
    letter-spacing: 0; line-height: 1.2; color: #111;
  }}
  .master-header h1 .mgr-name {{ color: var(--accent); }}
  .master-header .sub-title {{ font-size: 13px; color: var(--text-dim); line-height: 1.35; }}
  .master-header .sub-title .sep {{ color: #111; margin: 0 6px; }}
  .report-main {{
    min-width: 0; min-height: 0; overflow-y: auto; scroll-behavior: smooth;
    padding: 0 32px 72px; display: flex; flex-direction: column; align-items: center; gap: 0;
  }}
  .tab-nav {{
    position: relative; width: 100%; max-width: none; flex: 1; min-height: 0;
    display: flex; flex-direction: column; gap: 0; padding: 0;
    border-top: 2px solid #111; border-bottom: 2px solid #111;
    background: rgba(255,253,248,.62);
  }}
  .tab-nav::before {{
    display: none;
  }}
  .tab-nav button {{
    position: relative; flex: 1; width: 100%; min-height: 0; padding: 7px 10px;
    font-size: 13px; font-weight: 600; color: #111; background: transparent;
    border: 0; border-bottom: 1px solid #111; cursor: pointer;
    transition: all 0.16s ease; font-family: Georgia, "Times New Roman", "Microsoft YaHei", serif;
    letter-spacing: 0; text-align: center; border-radius: 0; box-shadow: none;
    z-index: 1; display: flex; flex-direction: column; justify-content: center; align-items: center;
  }}
  .tab-nav button:first-child, .tab-nav button:last-child {{ border-radius: 0; }}
  .tab-nav button:last-child {{ border-bottom: 0; }}
  .tab-nav button::after {{
    display: none;
  }}
  .tab-nav button::before {{
    display: none;
  }}
  .tab-nav button span {{ display: block; }}
  .tab-nav button .part-label {{ font-size: 10px; font-weight: 700; color: var(--accent); text-transform: uppercase; letter-spacing: .5px; }}
  .tab-nav button .part-name {{ margin-top: 1px; font-size: 14px; line-height: 1.12; }}
  .tab-nav button:hover:not(.active) {{ color: #111; background: rgba(227,18,11,.08); border-color: transparent; }}
  .tab-nav button:hover:not(.active)::before {{ background: rgba(227,18,11,.35); }}
  .tab-nav button.active {{ background: #111; color: #fff; z-index: 2; box-shadow: 0 0 0 1px #111; }}
  .tab-nav button.active::before {{ background: rgba(227,18,11,.8); }}
  .tab-nav button.active::after {{ background: var(--accent); border-color: rgba(17,17,17,.7); box-shadow: 0 0 0 3px #111; }}
  .tab-nav button.active .part-label {{ color: #f4c7c2; }}
  .sidebar-export {{ margin-top: 12px; padding-top: 12px; border-top: 2px solid #111; }}
  .export-word-link {{
    display: flex; align-items: center; justify-content: center; gap: 6px;
    width: 100%; min-height: 38px; border: 1px solid #111;
    background: rgba(255,253,248,.72); color: #111; text-decoration: none;
    font-size: 13px; font-weight: 700; letter-spacing: .4px;
    font-family: Georgia, "Times New Roman", "Microsoft YaHei", serif;
    transition: all .16s ease;
  }}
  .export-word-link::before {{ content: '↓'; color: var(--accent); font-size: 15px; line-height: 1; }}
  .export-word-link:hover {{ background: #111; color: #fff; box-shadow: inset 4px 0 0 var(--accent); }}
  .tab-panel {{ display: block; width: 100%; max-width: 1180px; margin-bottom: 22px; scroll-margin-top: 24px; }}
  .tab-panel.active {{ display: block; }}
  #tab-part1 .chart-inner {{ height: 320px !important; }}
  #tab-part2 #chart-p2 {{ height: 400px !important; }}
  #tab-part4 .card-body > .chart-row .chart-inner,
  #tab-part5 .card-body > .chart-row .chart-inner,
  #tab-part6 .card-body > .chart-row .chart-inner {{ height: 320px !important; }}
  .back-top {{
    position: fixed; right: 18px; bottom: 18px; z-index: 80;
    width: 34px; height: 34px; border-radius: 50%;
    border: 1px solid rgba(17,17,17,.18); background: rgba(248,241,229,.72);
    color: rgba(17,17,17,.45); cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    font-size: 18px; line-height: 1; font-family: Georgia, "Times New Roman", serif;
    box-shadow: 0 4px 14px rgba(0,0,0,.06); transition: all .16s ease;
  }}
  .back-top:hover {{ color: rgba(227,18,11,.76); border-color: rgba(227,18,11,.35); background: rgba(248,241,229,.9); }}
  @media (max-width: 860px) {{
    body {{ overflow: auto; padding: 0; }}
    .report-shell {{ display: block; height: auto; overflow: visible; }}
    .master-header {{ padding: 60px 12px 10px; min-height: 110px; }}
    .manager-filter {{ left: 12px; top: 10px; transform: none; width: min(260px, calc(100% - 24px)); }}
    .report-sidebar {{
      position: sticky; top: 0; z-index: 100; padding: 10px 12px;
      border-right: none; border-bottom: 1px solid var(--border); display: block;
    }}
    .sidebar-title {{ display: none; }}
    .report-main {{ height: auto; overflow: visible; padding: 0 12px 40px; }}
    .tab-nav {{ flex-direction: row; gap: 8px; overflow-x: auto; padding-bottom: 2px; border: 0; }}
    .tab-nav::before, .tab-nav button::after {{ display: none; }}
    .tab-nav button {{ min-width: 104px; text-align: center; font-size: 11px; padding: 9px 8px; }}
    .tab-nav button .part-label {{ font-size: 10px; }}
    .back-top {{ right: 12px; bottom: 12px; width: 32px; height: 32px; }}
  }}
</style>
</head>
<body>

<div class="report-shell">
<div class="master-header">
  {fund_switcher}
  <h1><span class="mgr-name">{manager}</span> · 深度分析</h1>
  <div class="sub-title">{subtitle_text}</div>
</div>
<aside class="report-sidebar">
  <div class="sidebar-title">内容导航</div>
  {tab_nav}
  <div class="sidebar-export">
    <a class="export-word-link" href="{export_word_api}?fund={quote(fund_name, safe='')}" download>导出 WORD</a>
  </div>
</aside>
<main class="report-main">
{panels_html}
<div class="footnote">数据来源：基金定期报告 ｜ 仅供分析参考，不构成投资建议</div>
</main>
</div>
<button class="back-top" type="button" onclick="scrollReportTop()" title="返回顶部" aria-label="返回顶部">↑</button>

<script>
{EC_SHARED}
var CHART_JS = {json.dumps(chart_js_map, ensure_ascii=False)};
var REPORT_PART_ORDER = {tab_order_json};
var FIRST_REPORT_PART = {json.dumps(first_tab_key, ensure_ascii=False)};
var REPORT_FUND_OPTIONS = {fund_switcher_options};
var _inited = {{}};
var _pjaxLoading = false;
var _reportMainForSpy = null;
var _windowPartSpyBound = false;

function initChartsForTab(tk) {{
  if (_inited[tk]) return;
  var jsList = CHART_JS[tk];
  if (!jsList) return;
  var panel = document.getElementById('tab-' + tk);
  var containers = panel ? Array.from(panel.querySelectorAll('.chart-inner,.p4-yc,.p4-yc-sm-item,.p9-case-chart')) : [];
  var notReady = containers.some(function(el) {{ return el.clientWidth <= 0 || el.clientHeight <= 0; }});
  if (notReady) {{
    setTimeout(function() {{ initChartsForTab(tk); }}, 80);
    return;
  }}
  var ok = true;
  jsList.forEach(function(js) {{ try {{ eval(js); }} catch(e) {{ ok = false; console.error('Chart error:', e); }} }});
  if (ok) _inited[tk] = true;
}}

function initAllReportCharts() {{
  Object.keys(CHART_JS).forEach(function(key) {{ initChartsForTab(key); }});
  requestAnimationFrame(function() {{
    resizeCharts(document);
    setTimeout(function() {{ resizeCharts(document); }}, 180);
    setTimeout(function() {{ resizeCharts(document); }}, 520);
  }});
}}

function resizeCharts(scope) {{
  (scope || document).querySelectorAll('.chart-inner,.p4-yc,.p4-yc-sm-item,.p9-case-chart').forEach(function(el) {{
    var inst = echarts.getInstanceByDom(el);
    if (inst) inst.resize();
  }});
}}

function disposeReportCharts() {{
  if (!window.echarts) return;
  document.querySelectorAll('.chart-inner,.p4-yc,.p4-yc-sm-item,.p9-case-chart').forEach(function(el) {{
    var inst = echarts.getInstanceByDom(el);
    if (inst) inst.dispose();
  }});
}}

function setActivePartNav(key) {{
  document.querySelectorAll('.tab-nav button').forEach(function(b) {{ b.classList.remove('active'); }});
  var idx = REPORT_PART_ORDER.indexOf(key);
  var btns = document.querySelectorAll('.tab-nav button');
  if (idx >= 0 && btns[idx]) btns[idx].classList.add('active');
}}

function switchTab(key) {{
  var panel = document.getElementById('tab-' + key);
  if (!panel) return;
  setActivePartNav(key);
  initChartsForTab(key);
  var main = document.querySelector('.report-main');
  if (main) {{
    var top = panel.getBoundingClientRect().top - main.getBoundingClientRect().top + main.scrollTop - 8;
    main.scrollTo({{ top: top, behavior: 'smooth' }});
  }} else {{
    panel.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
  }}
  setTimeout(function() {{
    resizeCharts(panel);
  }}, 180);
}}

function toggleCard(btn) {{
  var card = btn.closest('.card');
  card.classList.toggle('collapsed');
  btn.textContent = card.classList.contains('collapsed') ? '+' : '−';
  if (!card.classList.contains('collapsed')) {{
    setTimeout(function() {{
      card.querySelectorAll('.chart-inner').forEach(function(el) {{
        var inst = echarts.getInstanceByDom(el);
        if (inst) inst.resize();
      }});
    }}, 80);
  }}
}}

function bindHoldingsTables(scope) {{
  (scope || document).querySelectorAll('.holdings-table-wrap:not([data-bound])').forEach(function(tbl) {{
    tbl.setAttribute('data-bound', '1');
    var pinned = null;
    function highlight(name) {{
      tbl.querySelectorAll('.stk').forEach(function(el) {{
        el.style.opacity = (!name || el.getAttribute('data-stock') === name) ? '1' : '0.12';
      }});
    }}
    tbl.addEventListener('mouseover', function(e) {{
      if (pinned) return;
      var s = e.target.closest('.stk');
      if (s) highlight(s.getAttribute('data-stock'));
    }});
    tbl.addEventListener('mouseout', function(e) {{
      if (!pinned && e.target.closest('.stk')) highlight(null);
    }});
    tbl.addEventListener('click', function(e) {{
      var s = e.target.closest('.stk');
      if (!s) {{
        pinned = null;
        highlight(null);
        return;
      }}
      var name = s.getAttribute('data-stock');
      pinned = pinned === name ? null : name;
      highlight(pinned);
    }});
  }});
}}

function bindPart4DetailTables(scope) {{
  (scope || document).querySelectorAll('.p4-detail-table:not(.p4-matrix-table):not([data-bound])').forEach(function(table) {{
    table.setAttribute('data-bound', '1');
    var pinned = null;
    function highlight(name) {{
      table.classList.toggle('is-filtering', Boolean(name));
      table.querySelectorAll('td[data-name]').forEach(function(cell) {{
        cell.classList.toggle('is-active', Boolean(name) && cell.getAttribute('data-name') === name);
      }});
    }}
    table.addEventListener('mouseover', function(e) {{
      if (pinned) return;
      var cell = e.target.closest('td[data-name]');
      if (cell) highlight(cell.getAttribute('data-name'));
    }});
    table.addEventListener('mouseout', function(e) {{
      if (!pinned && e.target.closest('td[data-name]')) highlight(null);
    }});
    table.addEventListener('click', function(e) {{
      var cell = e.target.closest('td[data-name]');
      if (!cell) {{
        pinned = null;
        highlight(null);
        return;
      }}
      var name = cell.getAttribute('data-name');
      pinned = pinned === name ? null : name;
      highlight(pinned);
    }});
  }});
}}

		function bindPart7Heatmaps(scope) {{
	  (scope || document).querySelectorAll('.p7-heatmap-table:not([data-bound])').forEach(function(table) {{
    table.setAttribute('data-bound', '1');
    var originalRows = Array.prototype.slice.call(table.querySelectorAll('tr')).slice(1);
    var currentCol = null;
    var currentDir = 'default';
    table.querySelectorAll('th[data-sort-col]').forEach(function(th) {{
      th.addEventListener('click', function() {{
        var col = parseInt(th.getAttribute('data-sort-col'), 10);
        if (currentCol !== col) {{
          currentCol = col;
          currentDir = 'desc';
        }} else if (currentDir === 'desc') {{
          currentDir = 'asc';
        }} else if (currentDir === 'asc') {{
          currentCol = null;
          currentDir = 'default';
        }} else {{
          currentCol = col;
          currentDir = 'desc';
        }}
        var rows = currentDir === 'default' ? originalRows.slice() : Array.prototype.slice.call(table.querySelectorAll('tr')).slice(1);
        if (currentDir !== 'default') {{
          rows.sort(function(a, b) {{
            var av = parseFloat(a.children[col + 1].getAttribute('data-value') || '0');
            var bv = parseFloat(b.children[col + 1].getAttribute('data-value') || '0');
            return currentDir === 'desc' ? bv - av : av - bv;
          }});
        }}
        rows.forEach(function(row) {{ table.tBodies[0].appendChild(row); }});
        table.querySelectorAll('th[data-sort-col]').forEach(function(x) {{
          x.textContent = x.textContent.replace(/[↓↑]$/, '');
        }});
        if (currentDir !== 'default') {{
          th.textContent = th.textContent.replace(/[↓↑]$/, '') + (currentDir === 'desc' ? '↓' : '↑');
        }}
      }});
    }});
	  }});
	}}

	function partSpyAnchorY(main) {{
	  var rect = main ? main.getBoundingClientRect() : {{ top: 0, height: window.innerHeight }};
	  var offset = Math.min(Math.max(rect.height * 0.28, 120), 220);
	  return rect.top + offset;
	}}

	function detectActivePart() {{
	  var main = document.querySelector('.report-main');
	  var panels = Array.from(document.querySelectorAll('.tab-panel'));
	  if (!panels.length) return;
	  var anchorY = partSpyAnchorY(main);
	  var best = panels[0];
	  panels.forEach(function(panel) {{
	    var rect = panel.getBoundingClientRect();
	    if (rect.top <= anchorY) {{
	      best = panel;
	    }}
	    if (rect.top <= anchorY && rect.bottom > anchorY) {{
	      best = panel;
	    }}
	  }});
	  setActivePartNav(best.id.replace(/^tab-/, ''));
	}}

function scrollReportTop() {{
  var main = document.querySelector('.report-main');
  if (main) main.scrollTo({{ top: 0, behavior: 'smooth' }});
  else window.scrollTo({{ top: 0, behavior: 'smooth' }});
  setActivePartNav(FIRST_REPORT_PART);
}}

function openManagerDropdown() {{
  var filter = document.getElementById('manager-filter');
  if (filter) filter.classList.add('open');
}}

function closeManagerDropdown() {{
  var filter = document.getElementById('manager-filter');
  if (filter) filter.classList.remove('open');
}}

function toggleManagerDropdown() {{
  var filter = document.getElementById('manager-filter');
  if (filter) filter.classList.toggle('open');
}}

function filterManagers(value) {{
  var needle = String(value || '').trim().toLowerCase();
  var visible = 0;
  document.querySelectorAll('.manager-option').forEach(function(btn) {{
    var key = btn.getAttribute('data-key');
    var meta = (REPORT_FUND_OPTIONS || []).find(function(item) {{ return item.key === key; }}) || {{}};
    var haystack = [meta.manager, meta.fund, meta.label, meta.fund_name, key].join(' ').toLowerCase();
    var show = !needle || haystack.indexOf(needle) >= 0;
    btn.style.display = show ? '' : 'none';
    if (show) visible += 1;
  }});
  var options = document.getElementById('manager-options');
  if (options) options.classList.toggle('empty', visible === 0);
}}

function handleManagerSearchKey(event) {{
  if (event.key === 'Escape') closeManagerDropdown();
  if (event.key === 'Enter') {{
    var first = Array.from(document.querySelectorAll('.manager-option')).find(function(btn) {{
      return btn.style.display !== 'none';
    }});
    if (first) selectReportFund(first.getAttribute('data-key'));
  }}
}}

function selectReportFund(key) {{
  var meta = (REPORT_FUND_OPTIONS || []).find(function(item) {{ return item.key === key; }});
  if (!meta) return;
  if (meta.active) {{
    closeManagerDropdown();
    return;
  }}
  pjaxSwitchReport(meta.url);
}}

function findReportScript(doc) {{
  return Array.from(doc.scripts).find(function(script) {{
    return script.textContent && script.textContent.indexOf('var CHART_JS = ') >= 0;
  }});
}}

function extractAssignedJson(scriptText, name, nextMarker) {{
  var marker = 'var ' + name + ' = ';
  var start = scriptText.indexOf(marker);
  if (start < 0) throw new Error('Missing ' + name);
  start += marker.length;
  var end = scriptText.indexOf(nextMarker, start);
  if (end < 0) throw new Error('Missing end marker for ' + name);
  return JSON.parse(scriptText.slice(start, end));
}}

function extractFundContext(doc) {{
  var script = Array.from(doc.scripts).find(function(item) {{
    return item.textContent && item.textContent.trim().indexOf('window.FUND_CONTEXT = ') === 0;
  }});
  if (!script) return null;
  var marker = 'window.FUND_CONTEXT = ';
  var text = script.textContent;
  var start = text.indexOf(marker);
  var end = text.indexOf(';', start);
  if (start < 0 || end < 0) return null;
  return JSON.parse(text.slice(start + marker.length, end));
}}

function parseReportHtml(html) {{
  var doc = new DOMParser().parseFromString(html, 'text/html');
  var reportScript = findReportScript(doc);
  if (!reportScript) throw new Error('目标报告缺少图表脚本');
  var scriptText = reportScript.textContent;
  return {{
    doc: doc,
    title: doc.title,
    chartJs: extractAssignedJson(scriptText, 'CHART_JS', ';\\nvar REPORT_PART_ORDER'),
    partOrder: extractAssignedJson(scriptText, 'REPORT_PART_ORDER', ';\\nvar FIRST_REPORT_PART'),
    firstPart: extractAssignedJson(scriptText, 'FIRST_REPORT_PART', ';\\nvar REPORT_FUND_OPTIONS'),
    fundOptions: extractAssignedJson(scriptText, 'REPORT_FUND_OPTIONS', ';\\nvar _inited'),
    fundContext: extractFundContext(doc)
  }};
}}

function replaceReportNode(selector, nextDoc) {{
  var current = document.querySelector(selector);
  var next = nextDoc.querySelector(selector);
  if (!current || !next) throw new Error('无法替换页面区域：' + selector);
  current.replaceWith(next);
  return next;
}}

function applyReportPayload(payload, targetUrl, shouldPushState) {{
  disposeReportCharts();
  document.title = payload.title || document.title;
  replaceReportNode('.master-header', payload.doc);
  replaceReportNode('.report-sidebar', payload.doc);
  var nextMain = replaceReportNode('.report-main', payload.doc);
  CHART_JS = payload.chartJs;
  REPORT_PART_ORDER = payload.partOrder;
  FIRST_REPORT_PART = payload.firstPart;
  REPORT_FUND_OPTIONS = payload.fundOptions;
  _inited = {{}};
  window.CHART_JS = CHART_JS;
  window.REPORT_PART_ORDER = REPORT_PART_ORDER;
  window.FIRST_REPORT_PART = FIRST_REPORT_PART;
  window.REPORT_FUND_OPTIONS = REPORT_FUND_OPTIONS;
  if (payload.fundContext) {{
    window.FUND_CONTEXT = payload.fundContext;
    window.dispatchEvent(new CustomEvent('fund-context-change', {{ detail: payload.fundContext }}));
  }}
  if (nextMain) nextMain.scrollTop = 0;
  initAllReportCharts();
  bindHoldingsTables(nextMain);
  bindPart4DetailTables(nextMain);
  bindPart7Heatmaps(nextMain);
  bindReportScrollSpy();
  setTimeout(function() {{
    resizeCharts(document);
    detectActivePart();
  }}, 120);
  if (shouldPushState) {{
    history.pushState({{ reportUrl: targetUrl }}, '', targetUrl);
  }}
}}

function pjaxSwitchReport(targetUrl, options) {{
  options = options || {{}};
  var shouldPushState = options.push !== false;
  if (!window.fetch || !window.DOMParser || _pjaxLoading) {{
    window.location.href = targetUrl;
    return;
  }}
  _pjaxLoading = true;
  document.body.classList.add('report-pjax-loading');
  closeManagerDropdown();
  fetch(targetUrl, {{ credentials: 'same-origin', headers: {{ 'X-Mutual-Fund-PJAX': '1' }} }})
    .then(function(resp) {{
      if (!resp.ok) throw new Error('报告加载失败：' + resp.status);
      return resp.text();
    }})
    .then(function(html) {{
      applyReportPayload(parseReportHtml(html), targetUrl, shouldPushState);
    }})
    .catch(function(err) {{
      console.warn('PJAX report switch failed, fallback to full navigation:', err);
      window.location.href = targetUrl;
    }})
    .finally(function() {{
      _pjaxLoading = false;
      document.body.classList.remove('report-pjax-loading');
    }});
}}

var _partSpyTicking = false;
	function schedulePartSpy() {{
	  if (_partSpyTicking) return;
	  _partSpyTicking = true;
	  var run = function() {{
	    detectActivePart();
	    _partSpyTicking = false;
	  }};
	  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(run);
	  else setTimeout(run, 16);
	}}

function bindReportScrollSpy() {{
  var main = document.querySelector('.report-main');
  if (main && main !== _reportMainForSpy) {{
    main.addEventListener('scroll', schedulePartSpy, {{ passive: true }});
    _reportMainForSpy = main;
  }}
  if (!_windowPartSpyBound) {{
    window.addEventListener('scroll', schedulePartSpy, {{ passive: true }});
    _windowPartSpyBound = true;
  }}
}}

window.addEventListener('resize', function() {{ resizeCharts(document); }});
function initReportRuntime() {{
  initAllReportCharts();
  bindHoldingsTables(document);
  bindPart4DetailTables(document);
  bindPart7Heatmaps(document);
  bindReportScrollSpy();
  document.addEventListener('click', function(event) {{
    var filter = document.getElementById('manager-filter');
    if (filter && !filter.contains(event.target)) closeManagerDropdown();
  }});
  if (window.history && history.replaceState) {{
    history.replaceState({{ reportUrl: location.href }}, '', location.href);
  }}
  window.addEventListener('popstate', function() {{
    pjaxSwitchReport(location.href, {{ push: false }});
  }});
  detectActivePart();
}}
if (document.readyState === 'loading') window.addEventListener('load', initReportRuntime);
else initReportRuntime();
</script>
{assistant_context_script}
<script src="{web_asset_url}/sidebar.js?v=20260625-chat-ui"></script>
</body>
</html>'''

    os.makedirs(os.path.dirname(out_html), exist_ok=True)
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"已生成: {out_html}")
    print(f"文件大小: {os.path.getsize(out_html)/1024:.1f} KB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fund", required=True, help="基金名称，如：刘旭_大成高鑫A")
    parser.add_argument("--report-file", default=None, help="可选：指定 report.md 路径")
    parser.add_argument("--out-html", default=None, help="可选：指定输出 HTML 路径")
    args = parser.parse_args()
    generate_html(args.fund, report_file_override=args.report_file, out_html_override=args.out_html)
