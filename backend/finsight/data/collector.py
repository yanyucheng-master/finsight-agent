"""FinSight 数据快照采集器 —— 只依赖 Python 标准库。

用途
----
在能访问国内金融数据源的机器（通常是开发者本机）上运行，把公开数据源的
**原始响应**逐字节保存为“快照”，并为每一次请求记录来源 URL、请求参数、
抓取时间（UTC）、HTTP 状态与 SHA-256 摘要。后端的 SnapshotProvider 读取这些快照，
从而实现：

* 可复现：测试和演示在无网络/受限网络环境下也能使用同一份真实数据；
* 可追溯：系统里任何一个数字都能沿“证据 → 原始响应 → URL + 抓取时间 + 哈希”回溯；
* 不转录：数据从数据源直接落盘，不经人工或模型转写。

数据源（均为公开网页接口，仅用于非商业的研究/竞赛原型，遵守各站点条款，限速访问）
----
* 东方财富 F10：资产负债表 / 利润表 / 现金流量表（按年度、按报告期）、主要财务指标、公司概况、行情、月 K 线
* 巨潮资讯（证监会指定信息披露平台）：证券代码表、定期报告列表、近期公告列表、年度报告 PDF

本模块刻意不 import finsight 包内其它模块，保证在“裸” Python 3.8+ 环境中可直接运行：
    python scripts/collect_snapshots.py --symbols 600519,000651
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

COLLECTOR_VERSION = "0.1.1"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)

EM_F10 = "https://emweb.securities.eastmoney.com/PC_HSF10"
EM_DATACENTER = "https://datacenter.eastmoney.com/securities/api/data/get"
EM_QUOTE = "https://push2.eastmoney.com/api/qt/stock/get"
EM_KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
CNINFO = "https://www.cninfo.com.cn"
CNINFO_STATIC = "https://static.cninfo.com.cn"

# 东方财富 F10 报表接口前缀
STATEMENT_ENDPOINTS = {"balance": "zcfzb", "income": "lrb", "cashflow": "xjllb"}

ANNUAL_REPORT_CATEGORY = "category_ndbg_szsh"

# 年报正文标题：排除摘要、英文版、已取消等
_ANNUAL_TITLE_EXCLUDE = re.compile(r"摘要|英文|English|summary|已取消|取消|说明|公告$|问询|回复|更正公告|提示性", re.I)
_ANNUAL_TITLE_INCLUDE = re.compile(r"年度报告")


# --------------------------------------------------------------------------------------
# 证券代码工具
# --------------------------------------------------------------------------------------

def exchange_of(code: str) -> str:
    """根据 6 位 A 股代码判断交易所：SH / SZ / BJ。"""
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError(f"非法证券代码: {code!r}")
    if code.startswith("92"):
        return "BJ"
    if code.startswith(("60", "68", "90")):
        return "SH"
    if code.startswith(("00", "30", "20")):
        return "SZ"
    if code.startswith(("43", "83", "87", "88")):
        return "BJ"
    raise ValueError(f"无法识别交易所: {code}")


def em_market_id(code: str) -> int:
    """东方财富 secid 的市场编号：沪市 1，深市/北交所 0。"""
    return 1 if exchange_of(code) == "SH" else 0


# --------------------------------------------------------------------------------------
# 快照存储（内容寻址）
# --------------------------------------------------------------------------------------

_EXT_BY_TYPE = (
    ("application/json", "json"),
    ("text/json", "json"),
    ("javascript", "json"),  # 部分接口以 text/javascript 返回 JSON
    ("text/html", "html"),
    ("application/pdf", "pdf"),
    ("text/plain", "txt"),
)


def _guess_ext(content_type: str, body: bytes) -> str:
    ct = (content_type or "").lower()
    for key, ext in _EXT_BY_TYPE:
        if key in ct:
            return ext
    head = body[:8].lstrip()
    if head.startswith(b"%PDF"):
        return "pdf"
    if head[:1] in (b"{", b"["):
        return "json"
    if head[:1] == b"<":
        return "html"
    return "bin"


class SnapshotWriter:
    """把原始响应写入 objects/，并向 manifest.jsonl 追加一条记录。"""

    def __init__(self, root: Path, run_id: str):
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.objects.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "manifest.jsonl"
        self.run_id = run_id

    def save(self, *, provider: str, dataset: str, symbol: Optional[str], request: Dict[str, Any],
             status: Optional[int], content_type: str, body: Optional[bytes], final_url: Optional[str],
             error: Optional[str], meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        record: Dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "run_id": self.run_id,
            "collector_version": COLLECTOR_VERSION,
            "provider": provider,
            "dataset": dataset,
            "symbol": symbol,
            "request": request,
            "final_url": final_url,
            "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": status,
            "content_type": content_type,
            "size": len(body) if body is not None else 0,
            "sha256": None,
            "object": None,
            "error": error,
            "meta": meta or {},
        }
        if body is not None and error is None:
            digest = hashlib.sha256(body).hexdigest()
            ext = _guess_ext(content_type, body)
            rel = Path("objects") / digest[:2] / f"{digest}.{ext}"
            path = self.root / rel
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(path.suffix + ".tmp")
                tmp.write_bytes(body)
                tmp.replace(path)
            record["sha256"] = digest
            record["object"] = rel.as_posix()
        with self.manifest_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record


# --------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------

class Http:
    def __init__(self, delay: float = 0.6, timeout: float = 25.0, retries: int = 2):
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self._last = 0.0
        self._ssl = ssl.create_default_context()

    def _throttle(self) -> None:
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def fetch(self, url: str, *, params: Optional[Dict[str, Any]] = None, form: Optional[Dict[str, Any]] = None,
              headers: Optional[Dict[str, str]] = None) -> Tuple[Dict[str, Any], Optional[int], str, Optional[bytes], Optional[str], Optional[str]]:
        """返回 (request_record, status, content_type, body, final_url, error)。永不抛异常。"""
        full_url = url
        if params:
            full_url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params, safe=",")
        data = urllib.parse.urlencode(form).encode("utf-8") if form is not None else None
        method = "POST" if data is not None else "GET"
        hdrs = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/javascript, text/html, */*; q=0.01",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        }
        if headers:
            hdrs.update(headers)
        request_record = {"method": method, "url": full_url, "form": form}
        last_error: Optional[str] = None
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                req = urllib.request.Request(full_url, data=data, method=method, headers=hdrs)
                with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl) as resp:
                    body = resp.read()
                    encoding = (resp.headers.get("Content-Encoding") or "").lower()
                    if encoding == "gzip":
                        body = gzip.decompress(body)
                    elif encoding == "deflate":
                        try:
                            body = zlib.decompress(body)
                        except zlib.error:
                            body = zlib.decompress(body, -zlib.MAX_WBITS)
                    return request_record, resp.status, resp.headers.get("Content-Type", ""), body, resp.geturl(), None
            except urllib.error.HTTPError as exc:  # 4xx/5xx：记录，不重试 4xx
                last_error = f"HTTP {exc.code}: {exc.reason}"
                if 400 <= exc.code < 500:
                    return request_record, exc.code, "", None, full_url, last_error
            except Exception as exc:  # 网络错误：退避重试
                last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(1.5 * (attempt + 1))
        return request_record, None, "", None, full_url, last_error


# --------------------------------------------------------------------------------------
# 采集逻辑
# --------------------------------------------------------------------------------------

def _json_or_none(body: Optional[bytes]) -> Optional[Any]:
    if body is None:
        return None
    for enc in ("utf-8", "gbk"):
        try:
            return json.loads(body.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None


class Collector:
    def __init__(self, out: Path, *, years: int = 12, report_periods: int = 4, announcement_years: int = 3,
                 pdf_count: int = 1, delay: float = 0.6, log=print):
        self.run_id = dt.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.writer = SnapshotWriter(out, self.run_id)
        self.http = Http(delay=delay)
        self.years = years
        self.report_periods = report_periods
        self.announcement_years = announcement_years
        self.pdf_count = pdf_count
        self.log = log
        self._cninfo_orgs: Optional[Dict[str, Dict[str, Any]]] = None
        self.summary: Dict[str, Dict[str, str]] = {}

    # -- 基础 ----------------------------------------------------------------------
    def _get(self, provider: str, dataset: str, symbol: Optional[str], url: str, *, params=None, form=None,
             headers=None, meta=None) -> Tuple[Dict[str, Any], Optional[bytes]]:
        req, status, ctype, body, final_url, error = self.http.fetch(url, params=params, form=form, headers=headers)
        if error is not None and url.startswith(("https://www.cninfo.com.cn", "https://static.cninfo.com.cn")):
            # 巨潮部分节点仅稳定支持 http，失败时降级重试一次（记录在 meta 中）
            meta = {**(meta or {}), "fallback_from": url, "fallback_error": error}
            url = "http://" + url[len("https://"):]
            req, status, ctype, body, final_url, error = self.http.fetch(url, params=params, form=form, headers=headers)
        if error is None and status is not None and status >= 400:
            error = f"HTTP {status}"
        record = self.writer.save(provider=provider, dataset=dataset, symbol=symbol, request=req, status=status,
                                  content_type=ctype, body=body, final_url=final_url, error=error, meta=meta)
        mark = "ok" if error is None else f"ERR {error}"
        if symbol:
            self.summary.setdefault(symbol, {})
            prev = self.summary[symbol].get(dataset)
            # 同一数据集多次请求：只要有一次失败就标记失败
            if prev is None or prev == "ok":
                self.summary[symbol][dataset] = mark
        self.log(f"  [{provider}] {dataset:<28} {mark}  ({record['size']} bytes)")
        return record, (body if error is None else None)

    # -- 巨潮：代码表 ------------------------------------------------------------------
    def cninfo_orgs(self) -> Dict[str, Dict[str, Any]]:
        if self._cninfo_orgs is None:
            self.log("巨潮资讯：证券代码表")
            _, body = self._get("cninfo", "stock_list", None, f"{CNINFO}/new/data/szse_stock.json")
            data = _json_or_none(body) or {}
            self._cninfo_orgs = {item.get("code"): item for item in data.get("stockList", []) if item.get("code")}
        return self._cninfo_orgs

    # -- 东方财富 ----------------------------------------------------------------------
    def eastmoney(self, code: str) -> None:
        ex = exchange_of(code)
        em_code = f"{ex}{code}"
        # 1) 公司类型（4=一般企业，3=银行，2=保险，1=证券）
        _, html = self._get("eastmoney", "f10_index", code, f"{EM_F10}/NewFinanceAnalysis/Index",
                            params={"type": "web", "code": em_code.lower()})
        company_type = ""
        if html:
            m = re.search(rb'id="hidctype"\s+value="(\d)"', html) or re.search(rb'value="(\d)"\s+id="hidctype"', html)
            if m:
                company_type = m.group(1).decode()
        # 新版 F10 页面不再内嵌 hidctype：依次试探 4(一般企业)/3(银行)/2(保险)/1(证券)，
        # 以“资产负债表报告期列表非空”为准（与 AKShare 的回退策略一致）
        candidates = ([company_type] if company_type else []) + [t for t in ("4", "3", "2", "1") if t != company_type]
        for ctype in candidates:
            _, body = self._get("eastmoney", "company_type_probe", code,
                                f"{EM_F10}/NewFinanceAnalysis/zcfzbDateAjaxNew",
                                params={"companyType": ctype, "reportDateType": "1", "code": em_code},
                                meta={"company_type": ctype})
            probe = _json_or_none(body) or {}
            if probe.get("data"):
                company_type = ctype
                break
        company_type = company_type or "4"
        self.log(f"  公司类型 companyType={company_type}")

        # 2) 三大报表：按年度（reportDateType=1）+ 最近若干报告期（reportDateType=0）
        for statement, prefix in STATEMENT_ENDPOINTS.items():
            for date_type, label, limit in (("1", "annual", self.years), ("0", "report", self.report_periods)):
                if limit <= 0:
                    continue
                meta = {"company_type": company_type, "statement": statement, "report_date_type": date_type}
                _, body = self._get("eastmoney", f"{statement}_dates_{label}", code,
                                    f"{EM_F10}/NewFinanceAnalysis/{prefix}DateAjaxNew",
                                    params={"companyType": company_type, "reportDateType": date_type, "code": em_code},
                                    meta=meta)
                dates_json = _json_or_none(body) or {}
                dates = [str(row.get("REPORT_DATE", ""))[:10] for row in dates_json.get("data", []) or []]
                dates = [d for d in dates if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)][:limit]
                for i in range(0, len(dates), 5):
                    chunk = ",".join(dates[i:i + 5])
                    self._get("eastmoney", f"{statement}_{label}", code,
                              f"{EM_F10}/NewFinanceAnalysis/{prefix}AjaxNew",
                              params={"companyType": company_type, "reportDateType": date_type, "reportType": "1",
                                      "dates": chunk, "code": em_code},
                              meta={**meta, "dates": chunk.split(",")})

        # 3) 东方财富计算的主要财务指标（用于交叉核验我们自己的计算）
        self._get("eastmoney", "main_indicators", code, EM_DATACENTER,
                  params={"type": "RPT_F10_FINANCE_MAINFINADATA", "sty": "APP_F10_MAINFINADATA", "quoteColumns": "",
                          "filter": f'(SECUCODE="{code}.{ex}")', "p": "1", "ps": "200", "sr": "-1",
                          "st": "REPORT_DATE", "source": "HSF10", "client": "PC"})
        # 4) 公司概况（名称、行业、上市日期等）
        self._get("eastmoney", "company_survey", code, f"{EM_F10}/CompanySurvey/PageAjax", params={"code": em_code})
        # 5) 最新行情快照（总市值等，用于估值类指标）
        self._get("eastmoney", "quote", code, EM_QUOTE,
                  params={"fltt": "2", "invt": "2", "secid": f"{em_market_id(code)}.{code}",
                          "fields": "f57,f58,f43,f84,f85,f116,f117,f127,f162,f167,f189"})
        # 6) 月 K 线（不复权），用于历史年末市值
        self._get("eastmoney", "kline_monthly", code, EM_KLINE,
                  params={"fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57",
                          "ut": "7eea3edcaed734bea9cbfc24409ed989", "klt": "103", "fqt": "0",
                          "secid": f"{em_market_id(code)}.{code}", "beg": "19900101", "end": "20500101"})

    # -- 巨潮资讯 ----------------------------------------------------------------------
    def _cninfo_query(self, code: str, dataset: str, *, category: str, start: dt.date, end: dt.date,
                      max_pages: int) -> List[Dict[str, Any]]:
        org = self.cninfo_orgs().get(code)
        if not org:
            self.log(f"  [cninfo] 代码表中找不到 {code}，跳过 {dataset}")
            self.summary.setdefault(code, {})[dataset] = "ERR not in cninfo stock list"
            return []
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": CNINFO,
            "Referer": f"{CNINFO}/new/commonUrl/pageOfSearch?url=disclosure/list/search",
        }
        items: List[Dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            form = {
                "pageNum": str(page), "pageSize": "30", "column": "szse", "tabName": "fulltext", "plate": "",
                "stock": f"{code},{org.get('orgId')}", "searchkey": "", "secid": "", "category": category,
                "trade": "", "seDate": f"{start.isoformat()}~{end.isoformat()}", "sortName": "", "sortType": "",
                "isHLtitle": "true",
            }
            _, body = self._get("cninfo", dataset, code, f"{CNINFO}/new/hisAnnouncement/query", form=form,
                                headers=headers, meta={"page": page, "category": category})
            data = _json_or_none(body) or {}
            batch = data.get("announcements") or []
            items.extend(batch)
            if not data.get("hasMore") or not batch:
                break
        return items

    def cninfo(self, code: str) -> None:
        today = dt.date.today()
        annual = self._cninfo_query(code, "annual_reports", category=f"{ANNUAL_REPORT_CATEGORY};",
                                    start=dt.date(today.year - self.years - 1, 1, 1), end=today, max_pages=10)
        if self.announcement_years > 0:
            self._cninfo_query(code, "announcements", category="",
                               start=dt.date(today.year - self.announcement_years, today.month, 1), end=today,
                               max_pages=40)
        if self.pdf_count <= 0:
            return
        main_reports = [a for a in annual
                        if _ANNUAL_TITLE_INCLUDE.search(a.get("announcementTitle", "") or "")
                        and not _ANNUAL_TITLE_EXCLUDE.search(a.get("announcementTitle", "") or "")
                        and (a.get("adjunctType") or "").upper() == "PDF"]
        main_reports.sort(key=lambda a: a.get("announcementTime") or 0, reverse=True)
        for ann in main_reports[: self.pdf_count]:
            adjunct = ann.get("adjunctUrl")
            if not adjunct:
                continue
            size_kb = ann.get("adjunctSize") or 0
            if size_kb and size_kb > 40 * 1024:
                self.log(f"  [cninfo] 跳过过大的 PDF（{size_kb} KB）：{ann.get('announcementTitle')}")
                continue
            self._get("cninfo", "annual_report_pdf", code, f"{CNINFO_STATIC}/{adjunct}",
                      meta={"announcementId": ann.get("announcementId"),
                            "title": re.sub(r"<[^>]+>", "", ann.get("announcementTitle", "")),
                            "announcementTime": ann.get("announcementTime")})

    # -- 入口 ----------------------------------------------------------------------
    def run(self, symbols: Iterable[str], *, with_cninfo: bool = True) -> Dict[str, Dict[str, str]]:
        symbols = list(dict.fromkeys(s.strip() for s in symbols if s.strip()))
        self.log(f"FinSight 快照采集 run_id={self.run_id}  共 {len(symbols)} 家公司  输出: {self.writer.root}")
        if with_cninfo:
            self.cninfo_orgs()
        for idx, code in enumerate(symbols, 1):
            try:
                exchange_of(code)
            except ValueError as exc:
                self.log(f"[{idx}/{len(symbols)}] 跳过 {code}: {exc}")
                continue
            name = (self._cninfo_orgs or {}).get(code, {}).get("zwjc", "")
            self.log(f"[{idx}/{len(symbols)}] {code} {name}")
            self.eastmoney(code)
            if with_cninfo:
                self.cninfo(code)
        return self.summary


def _print_summary(summary: Dict[str, Dict[str, str]], log=print) -> int:
    failures = 0
    log("\n==== 采集汇总 ====")
    for code, datasets in summary.items():
        bad = {k: v for k, v in datasets.items() if v != "ok"}
        failures += len(bad)
        status = "全部成功" if not bad else f"{len(bad)} 项失败"
        log(f"{code}: {len(datasets)} 个数据集，{status}")
        for k, v in bad.items():
            log(f"    - {k}: {v}")
    return failures


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="FinSight 数据快照采集器（标准库实现）")
    parser.add_argument("--symbols", default="", help="逗号分隔的 6 位 A 股代码，如 600519,000651")
    parser.add_argument("--symbols-file", default="", help="每行一个代码的文本文件")
    parser.add_argument("--out", default="data/snapshots", help="快照输出目录")
    parser.add_argument("--years", type=int, default=12, help="年度报表回溯年数")
    parser.add_argument("--report-periods", type=int, default=4, help="最近报告期（季报/半年报）数量")
    parser.add_argument("--announcement-years", type=int, default=3, help="近期公告回溯年数，0 表示不采集")
    parser.add_argument("--pdf", type=int, default=1, help="下载最近 N 份年度报告 PDF，0 表示不下载")
    parser.add_argument("--delay", type=float, default=0.6, help="请求间隔秒数（礼貌限速）")
    parser.add_argument("--no-cninfo", action="store_true", help="不采集巨潮资讯")
    args = parser.parse_args(argv)

    symbols: List[str] = [s for s in args.symbols.split(",") if s.strip()]
    if args.symbols_file:
        for line in Path(args.symbols_file).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                symbols.append(line)
    if not symbols:
        parser.error("请通过 --symbols 或 --symbols-file 指定至少一个代码")

    log_path = Path(args.out) / "collector.log"
    Path(args.out).mkdir(parents=True, exist_ok=True)
    log_fh = log_path.open("a", encoding="utf-8")

    def log(msg: str) -> None:
        print(msg, flush=True)
        log_fh.write(msg + "\n")
        log_fh.flush()

    collector = Collector(Path(args.out), years=args.years, report_periods=args.report_periods,
                          announcement_years=args.announcement_years, pdf_count=args.pdf, delay=args.delay, log=log)
    summary = collector.run(symbols, with_cninfo=not args.no_cninfo)
    failures = _print_summary(summary, log)
    log(f"完成。manifest: {collector.writer.manifest_path}")
    log_fh.close()
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
