#!/usr/bin/env python3
"""Build Database C: relic deep-date database (day-level state-change history).

目标：为每个遗物（安魂遗物除外）建立"状态变化"（登场/入库）的历史时间戳，
精度达到天级（北京时间）。由多个数据源交叉构建。

数据源：
  1. wiki Module:Void/data          - 每个遗物的 Introduced / Vaulted 版本号、IsBaro、Tier
  2. wiki Update 索引页 (13..43+)   - 版本号 -> 游戏部署日期（YYYY-MM-DD，美国游戏日期）
  3. @wfcd/patchlogs (npm/jsDelivr) - 版本号 -> 精确 ISO 时间戳（UTC，用于换算北京时间）
  4. 本地 data/relics.json          - urlName 键名（页面读取的键）
  5. 世界状态 API (warframestat.us) - vaultTrader 当前瓦奇娅出售的遗物（当期阿耶）
     + warframe-items Relics.json   - uniqueName -> urlName 映射

输出：
  data/relic-deep-date.json          - 完整逐遗物事件历史（数据库本体）+ varziaRelics
  data/relic-deep-date-summary.json  - 页面用摘要（KV 友好，小体积）+ varziaRelics
  data/update-versions.json          - 版本号->日期 映射缓存（供监控/调试）

日期精度说明：
  - 有 patchlogs UTC 时间戳的事件：gameDate = UTC+8 北京时间（精确，gameDateTZ="UTC+8"）
  - 无 UTC 时间戳的事件（wiki-only）：gameDate = 美国游戏日期（gameDateTZ="US-ET"）
    此时无法精确转换为北京时间（美东夏令时/冬令时 + DE 发布时间不确定），
    直接使用 wiki 记录的美国日期，不做不精确的转换。

用法：
  python build_deep_date_db.py           # 全量构建（版本映射有缓存时直接复用）
  python build_deep_date_db.py --refresh # 强制重新抓取 wiki 更新索引页
"""

import html as html_mod
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(REPO_ROOT, "data")

WIKI_BASE = "https://wiki.warframe.com/w/"
MODULE_URL = WIKI_BASE + "Module:Void/data"
PATCHLOGS_URL = "https://cdn.jsdelivr.net/npm/@wfcd/patchlogs/data/patchlogs.json"
REQUEST_DELAY = 0.4

ENTRY_RE = re.compile(r'\["((?:Lith|Meso|Neo|Axi|Requiem|Vanguard) [^"]+)"\][ \t]*=[ \t]*\{')
HEADING_RE = re.compile(
    r"^(?:Update|Hotfix|U|H)[ .]?(\d+(?:\.\d+){1,3})\s*(?:\([^)]*\))?$")
# 合并标题：'Hotfix 40.0.5 + 40.0.5.1'（两个版本同日发布）
COMBINED_HEADING_RE = re.compile(
    r"^(?:Update|Hotfix|U|H)[ .]?(\d+(?:\.\d+){1,3})\s*\+\s*(\d+(?:\.\d+){1,3})\s*$")
MAJOR_HEADING_RE = re.compile(r"^(?:Update|Hotfix|U|H)[ .]?(\d{1,2})\s*(?:\([^)]*\))?$")
DATE_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2})(?:st|nd|rd|th)?,\s+(\d{4})")

MONTHS_FULL = [
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December"]

# 命名更新：wiki 遗物页将其渲染为 'Name 0.0 (date)'，日期以 wiki 渲染为准（美国日期）
NAMED_UPDATE_DATES = {
    "Specters of the Rail": "2016-07-08",   # Void Relics 系统上线（wiki 遗物页标注日期）
    "The Silver Grove": "2016-08-19",       # The Silver Grove 更新（patchlogs 同名条目）
}

# 特殊掉落物兜底：warframe-items 缺失 urlName 或与 wiki 命名不一致时使用
# (urlName, 中文名)
SPECIAL_REWARD_MAP = {
    "Forma Blueprint": ("forma_blueprint", "Forma 蓝图"),
    "Burston Prime Receiver": ("burston_prime_receiver", None),   # zh 从 item-names-zh 查
    "Acceltra Prime Blueprint": ("acceltra_prime_blueprint", None),
}

UPDATE_INDEX_MAJORS = list(range(13, 44))  # 13..43；44+ 由索引页导航动态发现


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_text(url, timeout=90, retries=3):
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Ws-Web-relic/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            last_err = e
            time.sleep(1.0 * (attempt + 1))
    raise last_err


def fetch_json(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "Ws-Web-relic/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def parse_wiki_module(html):
    """解析 Module:Void/data 的 Lua 源码 -> {遗物名: {introduced, vaulted, isBaro, tier, rewards}}"""
    start = html.find("RelicData = {")
    if start < 0:
        raise RuntimeError("Module:Void/data: RelicData section not found")
    body = html[start:]
    relics = {}
    for m in ENTRY_RE.finditer(body):
        name = m.group(1)
        tier = name.split(" ", 1)[0]
        i = m.end()
        depth = 1
        while i < len(body) and depth > 0:
            if body[i] == "{":
                depth += 1
            elif body[i] == "}":
                depth -= 1
            i += 1
        block = body[m.end():i]
        intr = re.search(r'Introduced[ \t]*=[ \t]*"([^"]+)"', block)
        vault = re.search(r'Vaulted[ \t]*=[ \t]*"([^"]+)"', block)
        # 提取 Drops 掉落物（括号深度计数，避免非贪婪只取第一个条目）
        dpos = block.find("Drops = {")
        rewards = []
        if dpos >= 0:
            dstart = dpos + len("Drops = {")
            depth = 1
            j = dstart
            while j < len(block) and depth > 0:
                if block[j] == "{":
                    depth += 1
                elif block[j] == "}":
                    depth -= 1
                j += 1
            drops = block[dstart:j]
            for dm in re.finditer(
                r'\{\s*Item\s*=\s*"([^"]+)"(?:\s*,\s*ItemCount\s*=\s*\d+)?'
                r'\s*,\s*Part\s*=\s*"([^"]*)"\s*,\s*Rarity\s*=\s*"([^"]+)"\s*,?\s*\}',
                drops, re.S):
                # wiki 页面 HTML 会把 & 编码为 &amp;，需反转义（如 "Cobra &amp; Crane Prime" -> "Cobra & Crane Prime"）
                item_name = html_mod.unescape(dm.group(1))
                part = html_mod.unescape(dm.group(2))
                rarity = html_mod.unescape(dm.group(3))
                display_name = item_name + (" " + part if part else "")
                rewards.append({
                    "name": display_name,
                    "rarity": rarity,
                })
        relics[name] = {
            "introduced": intr.group(1).strip() if intr else None,
            "vaulted": vault.group(1).strip() if vault else None,
            "isBaro": "IsBaro = true" in block,
            "tier": tier,
            "rewards": rewards,
        }
    return relics


def month_index(name):
    """月份名 -> 1-12；容错 wiki 拼写错误（如 'Feburary'）。"""
    n = (name or "").strip()
    for length in (6, 5, 4, 3):
        pref = n[:length]
        for i, m in enumerate(MONTHS_FULL):
            if m.startswith(pref):
                return i + 1
    return None


def _track_major(major, date, major_first):
    """大版本近似日期 = 该页所有小节中最早的日期（页面按最新优先排列）。"""
    if date < major_first.get(major, "9999-12-31"):
        major_first[major] = date


def parse_update_index(html):
    """解析 Update 索引页 -> ({版本号: {date, name}}, {大版本: 最早小节日期})。"""
    out = {}
    major_first = {}
    sec_re = re.compile(
        r"<(?:h[2-4])[^>]*>(.*?)</(?:h[2-4])>(.*?)(?=<(?:h[2-4])[^>]*>|$)", re.S)
    for heading, tail in sec_re.findall(html):
        heading = re.sub(r"<[^>]+>", "", heading).strip()
        dm = DATE_RE.search(tail)
        if not dm:
            continue
        mi = month_index(dm.group(1))
        if mi is None:
            continue
        date = "%04d-%02d-%02d" % (int(dm.group(3)), mi, int(dm.group(2)))
        title = re.sub(r"<[^>]+>", " ", tail)
        title = re.sub(r"\s+", " ", title).strip()[:120]
        m = HEADING_RE.match(heading)
        if m:
            out[m.group(1)] = {"date": date, "name": heading, "title": title}
            _track_major(m.group(1).split(".")[0], date, major_first)
            continue
        mc = COMBINED_HEADING_RE.match(heading)
        if mc:
            for ver in (mc.group(1), mc.group(2)):
                out[ver] = {"date": date, "name": heading, "title": title}
                _track_major(ver.split(".")[0], date, major_first)
            continue
        mm = MAJOR_HEADING_RE.match(heading)
        if mm:
            out[mm.group(1)] = {"date": date, "name": heading, "title": title,
                                "note": "major section"}
            _track_major(mm.group(1), date, major_first)
    return out, major_first


def fetch_all_update_indexes(known_majors):
    """抓取所有 Update 索引页，合并版本->日期映射；返回 (version_map, majors_seen)。"""
    version_map = {}
    major_dates = {}
    majors = list(known_majors)
    seen = set()
    while majors:
        major = majors.pop(0)
        if major in seen:
            continue
        seen.add(major)
        url = WIKI_BASE + "Update_%d" % major
        try:
            html = fetch_text(url)
        except Exception as e:
            print("  WARN: Update %d fetch failed: %s" % (major, e))
            continue
        page_map, page_major = parse_update_index(html)
        version_map.update(page_map)
        major_dates.update(page_major)
        if len(version_map) % 50 < 10:
            print("    ... Update %d done (%d versions so far)" % (major, len(version_map)))
        for nm in re.findall(r"Update_(\d+)", html):
            nmajor = int(nm)
            if 13 <= nmajor <= 99 and nmajor not in seen:
                majors.append(nmajor)
        time.sleep(REQUEST_DELAY)
    for major, date in major_dates.items():
        if major not in version_map:
            version_map[major] = {"date": date, "name": "Update %s" % major,
                                  "note": "major-version approx (earliest section date)"}
    return version_map, seen


def build_patchlogs_map():
    """@wfcd/patchlogs -> ({版本号: {ts, name}}, 原始 posts 列表)。"""
    try:
        posts = fetch_json(PATCHLOGS_URL)
    except Exception as e:
        print("  WARN: patchlogs fetch failed: %s" % e)
        return {}, []
    ver_re = re.compile(
        r"(?i)(?:^|[^a-z0-9.])(?:update|hotfix|u[ .]?)?(\d{1,2})\.(\d{1,2})(?:\.(\d{1,2}))?(?:\.(\d{1,2}))?(?![0-9.])")
    pmap = {}
    for p in posts:
        name = p.get("name", "")
        m = ver_re.search(name)
        if not m:
            continue
        ver = ".".join(g for g in m.groups() if g)
        if ver not in pmap:
            pmap[ver] = {"ts": p.get("date", "")[:19] + "Z",
                         "name": name[:100]}
    return pmap, posts


def beijing_date(ts_str):
    """UTC ISO 时间戳 -> 北京时间日期 YYYY-MM-DD（UTC+8，精确转换，与 EDT/EST 无关）。"""
    try:
        dt = datetime.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        return (dt + timedelta(hours=8)).strftime("%Y-%m-%d")
    except Exception:
        return None


def resolve_named_version(vstr, raw_posts):
    """命名版本 -> (ts, name) 或 (None, None)。"""
    m = re.match(r"^(.*?)\s+(\d+)$", vstr)
    if not m:
        return None, None
    base, num = m.group(1), m.group(2)
    pats = (r"(?i)\b(?:hotfix|update)\s*%s\b" % num,
            r"(?i)\bu\.?\s*%s\b" % num)
    for pat in pats:
        rx = re.compile(pat)
        for p in raw_posts:
            nm = p.get("name", "")
            if base.lower() in nm.lower() and rx.search(nm):
                ts = p.get("date", "")[:19] + "Z"
                return ts, nm
    return None, None


def version_candidates(ver):
    """生成版本候选：'36.1' -> ['36.1','36.1.0']；'43.0' -> ['43.0','43']。"""
    parts = ver.split(".")
    cands = [ver]
    if len(parts) == 2:
        cands.append(ver + ".0")
        cands.append(parts[0])
    return cands


def version_dates(ver, version_map, patchlogs_map):
    """解析一个版本 -> (gameDate, ts, source, tz)。

    gameDate: 显示日期；有 UTC 时间戳时 = 北京时间（UTC+8），否则 = 美国游戏日期（US-ET）。
    ts: 原始 UTC 时间戳（patchlogs 来源），用于验证/调试。
    tz: "UTC+8"（精确）或 "US-ET"（不确定，仅 wiki 日期）。
    """
    us_date = ts = None
    source = "unresolved"
    for cand in version_candidates(ver):
        if us_date is None and cand in version_map:
            us_date = version_map[cand]["date"]
            source = "wiki-index"
        pl = patchlogs_map.get(cand)
        if pl and pl.get("ts") and ts is None:
            ts = pl["ts"]
    if ts:
        bj_date = beijing_date(ts)
        return bj_date or ts[:10], ts, source, "UTC+8"
    if us_date:
        return us_date, None, source, "US-ET"
    return None, None, "unresolved", None


def _make_event(ev_type, ver, date, ts, source, tz, name=None):
    """构建标准化事件字典。"""
    ev = {"type": ev_type, "version": ver, "gameDate": date, "gameDateTZ": tz,
          "ts": ts, "source": source}
    if name:
        ev["name"] = name
    return ev


def relic_key(name, tier):
    """'Axi A1' -> 'axi_a1_relic'（与 Ws-Web-relic 页面 relics.json 键一致）。"""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") + "_relic"


def load_local_relics():
    path = os.path.join(DATA_DIR, "relics.json")
    if not os.path.exists(path):
        raise SystemExit("ERROR: data/relics.json not found. Run build_relic_db.py first.")
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def fetch_varzia_relics():
    """从世界状态 API 的 vaultTrader 读取当前瓦奇娅出售的遗物（当期阿耶）。

    vaultTrader.inventory 中 credits=1, ducats=null 的条目为遗物（Void Projection）。
    通过 warframe-items Relics.json 的 uniqueName 映射到标准遗物 urlName。
    返回 [(urlName, internalName), ...]。
    """
    # 1) 获取 vaultTrader 数据
    try:
        ws = fetch_json("https://api.warframestat.us/pc", timeout=60)
    except Exception as e:
        print("  WARN: world state API fetch failed: %s" % e)
        return []
    vt = ws.get("vaultTrader", {})
    inventory = vt.get("inventory", [])
    vault_items = [it for it in inventory if it.get("credits") == 1 and it.get("ducats") is None]
    if not vault_items:
        print("  WARN: no vaultTrader relics found")
        return []

    # 2) 构建 uniqueName -> urlName 映射（通过 warframe-items Relics.json）
    try:
        items = fetch_json(
            "https://raw.githubusercontent.com/WFCD/warframe-items/master/data/json/Relics.json",
            timeout=120)
    except Exception as e:
        print("  WARN: warframe-items Relics.json fetch failed: %s" % e)
        return []
    uname_map = {}
    for item in items:
        uname = item.get("uniqueName", "")
        mkt = item.get("marketInfo", {})
        url_name = mkt.get("urlName", "")
        if url_name and "Bronze" in uname:  # Bronze = Intact refinement
            uname_map[uname] = url_name

    # 3) 映射 vaultTrader 遗物（去除路径中 /StoreItems/ 前缀差异）
    results = []
    for vi in vault_items:
        uname = vi.get("uniqueName", "")
        normalized = uname.replace("/StoreItems/", "/")
        url_name = uname_map.get(normalized)
        if url_name:
            results.append((url_name, uname.split("/")[-1]))
        else:
            print("  WARN: unmapped vaultTrader relic: %s" % uname.split("/")[-1])
    return results


def load_version_cache():
    """读取已缓存的 update-versions.json（断点续跑/每日重建用）。"""
    path = os.path.join(DATA_DIR, "update-versions.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            cache = json.load(f)
        return cache.get("versions", {}), cache.get("majors", [])
    return {}, []


def save_version_cache(version_map, majors):
    path = os.path.join(DATA_DIR, "update-versions.json")
    cache = {"generated": now_iso(), "versions": version_map, "majors": sorted(majors)}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    return path


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    print("1/6 Loading wiki Module:Void/data ...")
    wiki_relics = parse_wiki_module(fetch_text(MODULE_URL))
    print("  %d relic entries" % len(wiki_relics))

    print("2/6 Loading wiki Update index pages ...")
    cached_map, cached_majors = load_version_cache()
    if "--refresh" in sys.argv:
        version_map, majors = fetch_all_update_indexes(UPDATE_INDEX_MAJORS)
        save_version_cache(version_map, majors)
    elif cached_map:
        print("  using cached version map (%d versions); --refresh to re-fetch" % len(cached_map))
        version_map = cached_map
        majors = cached_majors
    else:
        version_map, majors = fetch_all_update_indexes(UPDATE_INDEX_MAJORS)
        save_version_cache(version_map, majors)
    print("  %d versions from %d update-index pages" % (len(version_map), len(majors)))

    print("3/6 Loading @wfcd/patchlogs (exact timestamps) ...")
    patchlogs_map, raw_posts = build_patchlogs_map()
    print("  %d versions with exact ts" % len(patchlogs_map))

    print("4/6 Loading local relics.json ...")
    local_relics = load_local_relics()
    print("  %d local relics" % len(local_relics))

    print("5/6 Loading Varzia/Aya relics from world state API ...")
    varzia_relics = fetch_varzia_relics()  # [(urlName, internalName), ...]
    varzia_relic_map = {url: name for url, name in varzia_relics}
    print("  %d Varzia (Aya) relics" % len(varzia_relic_map))

    # 检查所有版本的可解析性
    all_versions = set()
    for r in wiki_relics.values():
        for v in (r["introduced"], r["vaulted"]):
            if v:
                all_versions.add(v)
    unresolved = []
    for v in sorted(all_versions):
        if re.match(r"^\d", v):
            if not any(c in version_map or c in patchlogs_map for c in version_candidates(v)):
                unresolved.append(v)
        elif v not in NAMED_UPDATE_DATES and not re.match(r"^.*\s+\d+$", v):
            unresolved.append(v)
    if unresolved:
        print("  WARN: unresolved versions: %s" % unresolved)

    print("6/6 Building relic deep-date DB ...")
    relics_db = {}
    unmatched = []
    for name, info in wiki_relics.items():
        if info["tier"] == "Requiem":
            continue  # 安魂遗物无入库概念，排除
        key = relic_key(name, info["tier"])
        if key not in local_relics:
            # 兜底：本地 relics.json 可能存在 name 一致但 key 不一致的条目
            # （warframe-items 上游 marketInfo.urlName 与 name 偶发不匹配，如 Axi Y2 -> axi_o7_relic）
            matched_local = next((lk for lk, lv in local_relics.items()
                                  if lv.get("name") == name), None)
            if matched_local:
                key = matched_local
            else:
                unmatched.append(name)
                continue

        def _resolve(vstr):
            if not vstr:
                return None
            if re.match(r"^\d", vstr):
                return vstr
            return None  # 命名版本走下方分支

        events = []
        ver = _resolve(info["introduced"])
        if ver:
            date, ts, src, tz = version_dates(ver, version_map, patchlogs_map)
            events.append(_make_event("released", ver, date, ts, src, tz))
        elif info["introduced"]:
            ov = NAMED_UPDATE_DATES.get(info["introduced"])
            ts, nm = resolve_named_version(info["introduced"], raw_posts)
            if ts:
                bj = beijing_date(ts)
                events.append(_make_event("released", info["introduced"],
                                           bj or ts[:10], ts, "patchlogs-named", "UTC+8", nm))
            elif ov:
                events.append(_make_event("released", info["introduced"],
                                           ov, None, "wiki-named", "US-ET"))
            else:
                events.append(_make_event("released", info["introduced"],
                                           None, None, "unresolved", None))
        ver = _resolve(info["vaulted"])
        if ver:
            date, ts, src, tz = version_dates(ver, version_map, patchlogs_map)
            events.append(_make_event("vaulted", ver, date, ts, src, tz))
        elif info["vaulted"]:
            ov = NAMED_UPDATE_DATES.get(info["vaulted"])
            ts, nm = resolve_named_version(info["vaulted"], raw_posts)
            if ts:
                bj = beijing_date(ts)
                events.append(_make_event("vaulted", info["vaulted"],
                                           bj or ts[:10], ts, "patchlogs-named", "UTC+8", nm))
            elif ov:
                events.append(_make_event("vaulted", info["vaulted"],
                                           ov, None, "wiki-named", "US-ET"))
            else:
                events.append(_make_event("vaulted", info["vaulted"],
                                           None, None, "unresolved", None))
        events.sort(key=lambda e: (e["gameDate"] or "9999-12-31", e["type"] != "released"))

        if info["vaulted"]:
            status = "vaulted"
        elif info["isBaro"]:
            status = "baro"
        else:
            status = "active"

        # 用本地 relics.json 补充 urlName（wiki module 无 urlName，页面中文名依赖它）
        local_relic_info = local_relics.get(key, {})
        local_reward_map = {}
        for lr in local_relic_info.get("rewards", []):
            lname = lr.get("name", "")
            if lname and lname not in local_reward_map:
                local_reward_map[lname] = lr.get("urlName", "")
        enriched_rewards = []
        for wr in info.get("rewards", []):
            wname = wr.get("name", "")
            url_name = local_reward_map.get(wname, "")
            zh = None
            if not url_name and wname in SPECIAL_REWARD_MAP:
                # warframe-items 缺失或与 wiki 命名不一致时兜底（Forma 等特殊掉落物）
                url_name, zh = SPECIAL_REWARD_MAP[wname]
            enriched_rewards.append({
                "name": wname,
                "rarity": wr.get("rarity", ""),
                "urlName": url_name,
                "zh": zh,
            })

        relics_db[key] = {
            "name": name,
            "tier": info["tier"],
            "isBaro": info["isBaro"],
            "status": status,
            "events": events,
            "rewards": enriched_rewards,
        }

    if unmatched:
        print("  WARN: %d wiki relics unmatched in local relics.json (first 20): %s"
              % (len(unmatched), unmatched[:20]))

    # Add Varzia fields to each relic
    for key, r in relics_db.items():
        r["varzia"] = key in varzia_relic_map
        r["varziaSet"] = varzia_relic_map.get(key)  # 内部名称，非套装名

    # 统计日期精度
    n_utc = sum(1 for r in relics_db.values() for e in r["events"] if e.get("gameDateTZ") == "UTC+8")
    n_us = sum(1 for r in relics_db.values() for e in r["events"] if e.get("gameDateTZ") == "US-ET")
    n_none = sum(1 for r in relics_db.values() for e in r["events"] if e.get("gameDate") is None)
    print("  date precision: %d UTC+8 (Beijing exact), %d US-ET (wiki date), %d null" % (n_utc, n_us, n_none))

    full = {
        "generated": now_iso(),
        "note": ("gameDate = 显示日期：gameDateTZ=UTC+8 时为精确北京时间（来自 patchlogs UTC 时间戳），"
                 "gameDateTZ=US-ET 时为美国游戏日期（wiki 来源，无法精确转换北京时间）。"
                 "安魂遗物已排除"),
        "relics": relics_db,
        "varziaRelics": list(varzia_relic_map.keys()),
    }
    full_path = os.path.join(DATA_DIR, "relic-deep-date.json")
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(full, f, ensure_ascii=False, indent=1)
    print("  Wrote %s (%d relics)" % (full_path, len(relics_db)))

    # 页面摘要（KV 友好）：每个遗物一行短数据
    summary_items = {}
    for key, r in relics_db.items():
        released = next((e["gameDate"] for e in r["events"] if e["type"] == "released"), None)
        released_tz = next((e["gameDateTZ"] for e in r["events"] if e["type"] == "released"), None)
        vaults = [e for e in r["events"] if e["type"] == "vaulted"]
        last = r["events"][-1] if r["events"] else None
        summary_items[key] = {
            "name": r["name"],
            "tier": r["tier"],
            "status": r["status"],
            "released": released,
            "releasedTZ": released_tz,
            "vaultedAt": vaults[-1]["gameDate"] if vaults else None,
            "vaultedTZ": vaults[-1]["gameDateTZ"] if vaults else None,
            "lastChange": last["gameDate"] if last else None,
            "varzia": r["varzia"],
            "varziaSet": r.get("varziaSet"),
        }
    summary = {"generated": full["generated"], "items": summary_items, "varziaRelics": list(varzia_relic_map.keys())}
    summary_path = os.path.join(DATA_DIR, "relic-deep-date-summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False)
    print("  Wrote %s (%d items, %.1f KB)"
          % (summary_path, len(summary_items), os.path.getsize(summary_path) / 1024))

    cache = {"generated": full["generated"],
             "versions": {v: version_map[v] for v in version_map}}
    cache_path = os.path.join(DATA_DIR, "update-versions.json")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    print("  Wrote %s" % cache_path)
    print("Done.")


if __name__ == "__main__":
    main()
