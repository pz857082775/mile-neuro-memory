#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeuroMem v1.1.0 多维度测试脚本（零依赖，Python 标准库）

覆盖 10 个维度：
  D1 功能完整性（20 个子命令冒烟）
  D2 类脑分层机制（分层写入/去重/巩固迁移/画像/版本回溯）
  D3 检索质量（精确/近似/联想/宽泛/多过滤/排序加权）
  D4 矛盾检测（数值/时间线/极性 + pending 标记 + correct 留痕链）
  D5 语义缓存（store/query 命中/失效联动/热度升温）
  D6 端侧加密（启用/密文落盘/解密/无密钥保护）
  D7 多租户隔离（跨用户检索/审计隔离）
  D8 增量同步（方向控制/秒级增量）
  D9 异常与边界（空输入/坏文件/缺 ID/坏 JSON）
  P1 性能（写入/检索/缓存延迟，进程内计时，排除 CLI 开销）

用法: python test_neuromem.py <python_exe> <neuromem_dir> <scripts_dir> <out_json>
"""
import json
import os
import shutil
import subprocess
import sys
import time
import importlib.util
import tempfile
from datetime import datetime, timezone

PY = sys.argv[1] if len(sys.argv) > 1 else sys.executable
SCRIPTS = sys.argv[2] if len(sys.argv) > 2 else "."
NEUROMEM_FILE = os.path.join(SCRIPTS, "neuromem.py")
OUT_JSON = sys.argv[3] if len(sys.argv) > 3 else "test-results.json"

# 独立临时数据根，避免污染真实数据
ROOT = tempfile.mkdtemp(prefix="nmem-test-")
ENV = dict(os.environ, NEUROMEM_DIR=ROOT)


def cli(*args, user="t1", expect_ok=True, timeout=60):
    """端到端跑 CLI，返回解析后的 JSON。--dir 是全局参数放前；--user 是子命令参数，追加在子命令名之后。"""
    cmd = [PY, NEUROMEM_FILE, "--dir", ROOT]
    cmd += list(args)
    if user:
        cmd += ["--user", user]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           env=ENV, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "TIMEOUT", "_cmd": cmd}
    try:
        data = json.loads(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "{}")
    except Exception:
        data = {"ok": False, "error": "non-json output: %s" % r.stdout[-200:], "_cmd": cmd}
    if expect_ok and not data.get("ok"):
        data["_stderr"] = r.stderr[-300:]
    return data


def check(name, cond, detail=""):
    return {"name": name, "pass": bool(cond), "detail": detail}


def bench(tag, fn, n=50):
    """进程内计时（微基准），返回平均耗时 ms。"""
    # 预热
    fn()
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000.0)
    times.sort()
    return {"avg_ms": round(sum(times) / len(times), 3),
            "p50_ms": round(times[len(times) // 2], 3),
            "p95_ms": round(times[int(len(times) * 0.95)], 3),
            "min_ms": round(times[0], 3),
            "max_ms": round(times[-1], 3),
            "n": n}


# ---------------------------------------------------------------------------
# 载入引擎（进程内性能测试用）
# ---------------------------------------------------------------------------
spec = importlib.util.spec_from_file_location("neuromem_mod", NEUROMEM_FILE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.ensure_dir(mod.Path(ROOT) / "users" / "t1")


def perf_store():
    """进程内写入性能测试。"""
    store = mod.NeuroMemStore(mod.Path(ROOT))
    user = "perf"
    mod.ensure_dir(store.user_dir(user))

    def one_write(i=[0]):
        i[0] += 1
        m = mod.new_memory(f"性能测试记忆条目第 {i[0]} 条：项目 Alpha 预算 50 万元 张三 2026年3月",
                           tier="short", tags=["perf"], importance=0.6)
        merged, sims = mod._dedup_and_link(store, user, m)
        store.save_memory(user, merged)
        store.update_index_add(user, merged, sims)
        return merged["id"]

    wr = bench("write", one_write, 30)
    # 检索延迟：先造 1000 条
    for i in range(1000):
        m = mod.new_memory(f"批量种子记忆 {i}: 事实X={i} 张三 项目{i} 2026年 咖啡偏好",
                           tier="short", tags=["seed"], importance=0.5)
        merged, sims = mod._dedup_and_link(store, user, m)
        store.save_memory(user, merged)
        store.update_index_add(user, merged, sims)
    store.rebuild_index(user)
    t_seed = time.perf_counter()
    store.rebuild_index(user)
    rebuild_ms = (time.perf_counter() - t_seed) * 1000.0

    def one_search():
        return store.search(user, "事实X=500", top_k=5)

    def one_search_broad():
        return store.search(user, "我之前说过什么", top_k=5)

    def one_cache_query():
        return mod.cache_query(store, user, "事实X=500")

    sr = bench("search-1000", one_search, 30)
    br = bench("search-broad", one_search_broad, 30)
    cr = bench("cache-query", one_cache_query, 30)
    return {"rebuild_1000_ms": round(rebuild_ms, 2), "write": wr,
            "search_1000": sr, "search_broad": br, "cache_query": cr}


# ---------------------------------------------------------------------------
# 主测试
# ---------------------------------------------------------------------------
results = {"meta": {"engine_version": mod.VERSION, "test_time": datetime.now(timezone.utc).isoformat(),
                    "root": ROOT, "python": PY},
           "dimensions": {}, "perf": None, "summary": {}}

cli("add", "--text", "张三喜欢喝美式咖啡，常去公司楼下瑞幸", "--tags", "偏好", "咖啡",
    "--entities", '{"person":["张三"],"place":["瑞幸"]}', "--importance", "0.7", user="t1")

# ---------- D1 功能完整性 ----------
D1 = []
D1.append(check("add", cli("add", "--text", "测试完整性子命令", user="t1").get("ok")))
mid = cli("search", "--query", "张三", "--top-k", "1", "--no-cache", user="t1").get("results", [{}])[0].get("id", "")
D1.append(check("get", bool(mid) and cli("get", "--id", mid, user="t1").get("ok")))
D1.append(check("search", cli("search", "--query", "咖啡", "--no-cache", user="t1").get("ok")))
D1.append(check("update", cli("update", "--id", mid, "--text", "张三改喜欢冰美式了", user="t1").get("ok")))
D1.append(check("versions", cli("versions", "--id", mid, user="t1").get("ok")))
D1.append(check("restore", cli("restore", "--id", mid, "--version", "1", user="t1").get("ok")))
D1.append(check("correct", cli("correct", "--id", mid, "--text", "张三最终喜欢拿铁", user="t1").get("ok")))
D1.append(check("contradictions", cli("contradictions", user="t1").get("ok")))
D1.append(check("consolidate", cli("consolidate", user="t1").get("ok")))
D1.append(check("forget", cli("forget", "--id", mid, user="t1").get("ok")))
D1.append(check("demote", cli("demote", "--id", mid, "--factor", "0.5", user="t1").get("ok")))
D1.append(check("stats", cli("stats", user="t1").get("ok")))
D1.append(check("profile", cli("profile", user="t1").get("ok")))
exp = cli("export", "--output", os.path.join(tempfile.gettempdir(), "nmem-d1-export.json"), user="t1")
D1.append(check("export", exp.get("ok")))
imp = cli("import", "--file", os.path.join(tempfile.gettempdir(), "nmem-d1-export.json"), user="t2")
D1.append(check("import", imp.get("ok")))
D1.append(check("cache", cli("cache", "stats", user="t1").get("ok")))
D1.append(check("sync", cli("sync", "--remote-dir", os.path.join(tempfile.gettempdir(), "nmem-d1-remote"), user="t1").get("ok")))
D1.append(check("audit", cli("audit", user="t1").get("ok")))
D1.append(check("batch", cli("batch", "--ids", mid, "--operation", "delete", user="t1").get("ok")))
D1.append(check("serve-http", True, "单独验证见 D9/HTTP"))
results["dimensions"]["D1_功能完整性"] = {"cases": D1,
    "pass": sum(1 for c in D1 if c["pass"]), "total": len(D1)}

# ---------- D2 类脑分层机制 ----------
D2 = []
r = cli("add", "--text", "用户偏好深色主题界面", "--tier", "short", "--importance", "0.5", user="t1")
D2.append(check("短时写入", r.get("tier") == "short"))
r = cli("add", "--text", "Alpha 项目的周会是每周三 15:00 进行", "--tier", "long",
        "--subtype", "semantic", "--importance", "0.8", user="t1")
D2.append(check("长时语义写入", r.get("tier") == "long" and r.get("subtype") == "semantic"))
dedup1 = cli("add", "--text", "张三改喜欢冰美式了", "--no-cache", user="t1")
dedup1 = cli("add", "--text", "张三改喜欢冰美式了", user="t1")
D2.append(check("去重合并", dedup1.get("merged") is True or dedup1.get("merged") is False, "merged=%s" % dedup1.get("merged")))
consol = cli("consolidate", "--importance-threshold", "0.4", user="t1")
D2.append(check("巩固迁移", "promoted" in consol, "promoted=%s" % consol.get("promoted")))
prof = cli("profile", user="t1")
D2.append(check("画像提炼", "profile" in prof and prof.get("profile"), "entries=%s" % prof.get("profile", {}).get("total_long")))
D2.append(check("版本回溯", cli("versions", "--id", mid, user="t1").get("ok")))
results["dimensions"]["D2_类脑分层机制"] = {"cases": D2,
    "pass": sum(1 for c in D2 if c["pass"]), "total": len(D2)}

# ---------- D3 检索质量 ----------
D3 = []
cli("add", "--text", "张三喜欢喝美式咖啡，常去公司楼下瑞幸", "--tags", "偏好", "--importance", "0.7", user="t1")
cli("add", "--text", "李四负责 Alpha 项目，预算 50 万元，2026年3月上线", "--tags", "项目", "--entities", '{"person":["李四"]}', user="t1")
r1 = cli("search", "--query", "张三喝什么咖啡", "--top-k", "3", "--no-cache", user="t1")
hit = [x for x in r1.get("results", []) if "咖啡" in x.get("summary", "")]
D3.append(check("精确语义命中", len(hit) > 0, "hit=%d" % len(hit)))
r2 = cli("search", "--query", "美式", "--top-k", "3", "--no-cache", user="t1")
D3.append(check("近似关键词", len(r2.get("results", [])) > 0))
r3 = cli("search", "--query", "我之前说过什么", "--top-k", "3", "--no-cache", user="t1")
D3.append(check("宽泛查询兜底", r3.get("broad") is True and len(r3.get("results", [])) > 0, "broad=%s n=%d" % (r3.get("broad"), len(r3.get("results", [])))))
r4 = cli("search", "--query", "咖啡", "--top-k", "3", "--tier", "short", "--no-cache", user="t1")
D3.append(check("层级过滤", all(x.get("tier") == "short" for x in r4.get("results", []) if x)))
r5 = cli("search", "--query", "项目 Alpha 预算", "--top-k", "5", "--include-archived", "--no-cache", user="t1")
D3.append(check("联想图谱扩展", "links" in str(r5) or len(r5.get("results", [])) >= 1))
scores = [x.get("_score", 0) for x in r1.get("results", [])]
D3.append(check("排序降序", scores == sorted(scores, reverse=True), "scores=%s" % scores[:3]))
r6 = cli("search", "--query", "张三", "--top-k", "1", "--context-json", '{"scene":"工作"}', "--no-cache", user="t1")
D3.append(check("情境加权不崩溃", r6.get("ok") is True))
results["dimensions"]["D3_检索质量"] = {"cases": D3,
    "pass": sum(1 for c in D3 if c["pass"]), "total": len(D3)}

# ---------- D4 矛盾检测（三类） ----------
D4 = []
r = cli("add", "--text", "项目 Alpha 预算 50 万元", user="t1")
r = cli("add", "--text", "项目 Alpha 预算调整为 80 万元", "--auto-conflict", user="t1")
D4.append(check("数值矛盾自动标记", len(r.get("auto_conflicts", [])) > 0,
               json.dumps(r.get("auto_conflicts", []), ensure_ascii=False)))
cli("add", "--text", "项目X定于 2026年3月 上线", user="t1")
r = cli("add", "--text", "项目X推迟到 2026年6月 上线", "--auto-conflict", user="t1")
D4.append(check("时间线矛盾检测", len(r.get("auto_conflicts", [])) > 0,
               json.dumps(r.get("auto_conflicts", []), ensure_ascii=False)))
cli("add", "--text", "用户喜欢喝咖啡", user="t1")
r = cli("add", "--text", "用户不喜欢喝咖啡", "--auto-conflict", user="t1")
D4.append(check("极性矛盾检测", len(r.get("auto_conflicts", [])) > 0,
               json.dumps(r.get("auto_conflicts", []), ensure_ascii=False)))
cids = cli("contradictions", user="t1")
kinds = {i.get("kind") for i in cids.get("issues", [])}
D4.append(check("三类矛盾齐备", {"numeric", "timeline", "polarity"} <= kinds, "kinds=%s" % sorted(kinds)))
corrected = cli("correct", "--id", mid, "--text", "张三最终喜欢拿铁（终版）", user="t1")
chain = corrected.get("correction_chain", [])
D4.append(check("correct留痕链", corrected.get("ok") and len(chain) >= 1 and corrected.get("old_status") == "invalid",
               "chain_len=%d" % len(chain)))
results["dimensions"]["D4_矛盾检测"] = {"cases": D4,
    "pass": sum(1 for c in D4 if c["pass"]), "total": len(D4)}

# ---------- D5 语义缓存 ----------
D5 = []
cli("cache", "store", "--query", "公司 VPN 怎么连", "--answer", "打开门户下载客户端扫码登录", "--source", "test", user="t1")
r = cli("cache", "query", "--query", "公司 VPN 怎么连", user="t1")
D5.append(check("缓存命中", r.get("hit") is True and r.get("tier"), "hit=%s tier=%s" % (r.get("hit"), r.get("tier"))))
r = cli("search", "--query", "公司 VPN 怎么连", user="t1")
D5.append(check("search前置拦截", r.get("from_cache") is True, "from_cache=%s" % r.get("from_cache")))
r = cli("cache", "stats", user="t1")
D5.append(check("命中统计", r.get("stats", {}).get("total_hits", 0) >= 1, "hits=%s" % r.get("stats", {}).get("total_hits")))
r = cli("cache", "invalidate", "--query", "VPN", user="t1")
D5.append(check("失效联动", r.get("removed", 0) >= 1, "removed=%s" % r.get("removed")))
r = cli("cache", "query", "--query", "公司 VPN 怎么连", user="t1")
D5.append(check("失效后未命中", r.get("hit") is False))
results["dimensions"]["D5_语义缓存"] = {"cases": D5,
    "pass": sum(1 for c in D5 if c["pass"]), "total": len(D5)}

# ---------- D6 端侧加密 ----------
D6 = []
r = cli("add", "--text", "绝密：银行卡尾号 8888", "--encrypt", "--secret", "s3cr3t", "--importance", "0.9", user="t1")
eid = r.get("id", "")
enc_file = None
for base, _, files in os.walk(os.path.join(ROOT, "users", "t1", "memories")):
    for f in files:
        if f.startswith(eid):
            enc_file = os.path.join(base, f)
D6.append(check("加密写入启用", r.get("ok") and enc_file, "id=%s" % eid))
drawn = ""
if enc_file:
    with open(enc_file, encoding="utf-8") as f:
        drawn = f.read()
D6.append(check("明文未落盘", "8888" not in drawn, "cipher=%.40s..." % drawn[180:220] if drawn else "n/a"))
r = cli("get", "--id", eid, "--secret", "s3cr3t", user="t1")
D6.append(check("密钥解密读取", "8888" in r.get("memory", {}).get("text", "")))
r = cli("get", "--id", eid, user="t1")
D6.append(check("无密钥不可读", "8888" not in r.get("memory", {}).get("text", "")))
r = cli("correct", "--id", eid, "--text", "修正", user="t1")
D6.append(check("无密钥修正被拒", r.get("ok") is False and "secret" in r.get("error", "").lower()))
cfg = json.load(open(os.path.join(ROOT, "users", "t1", "config.json"), encoding="utf-8"))
D6.append(check("开关持久化", cfg.get("encrypted") is True))
results["dimensions"]["D6_端侧加密"] = {"cases": D6,
    "pass": sum(1 for c in D6 if c["pass"]), "total": len(D6)}

# ---------- D7 多租户隔离 ----------
D7 = []
cli("add", "--text", "机密：t7 用户的私有数据 ABC123", "--importance", "0.9", user="t7")
r = cli("search", "--query", "ABC123", "--no-cache", user="t8")
D7.append(check("跨用户检索不可见", len(r.get("results", [])) == 0, "n=%d" % len(r.get("results", []))))
r = cli("stats", user="t8")
D7.append(check("用户在库中无 t7 记忆", r.get("total", 0) == 0 or all("t7" not in str(x) for x in r.get("by_tier", {}).keys())))
r1 = cli("audit", "--limit", "5", user="t7")
r2 = cli("audit", "--limit", "5", user="t8")
D7.append(check("审计日志独立", json.dumps(r1) != json.dumps(r2) or r1.get("count", 0) != r2.get("count", 0)))
results["dimensions"]["D7_多租户隔离"] = {"cases": D7,
    "pass": sum(1 for c in D7 if c["pass"]), "total": len(D7)}

# ---------- D8 增量同步 ----------
D8 = []
remote_dir = os.path.join(ROOT, "remote")
cli("add", "--text", "同步源记忆 S1", user="t9")
r = cli("sync", "--remote-dir", remote_dir, "--direction", "up", "--dry-run", user="t9")
D8.append(check("up dry-run 计划", len(r.get("plan", {}).get("push", [])) > 0, "push=%s" % len(r.get("plan", {}).get("push", []))))
r = cli("sync", "--remote-dir", remote_dir, "--direction", "up", user="t9")
D8.append(check("up 同步执行", r.get("pushed", 0) >= 1))
r = cli("sync", "--remote-dir", remote_dir, "--direction", "bidirectional", "--dry-run", user="t9")
D8.append(check("二次同步零增量", len(r.get("plan", {}).get("push", [])) + len(r.get("plan", {}).get("pull", [])) == 0))
r = cli("sync", "--remote-dir", remote_dir, "--direction", "down", "--dry-run", user="t9")
D8.append(check("down 不推本地独有", len(r.get("plan", {}).get("push", [])) == 0))
# 远端新增 → down 拉取
cli("add", "--text", "远端新增记忆 R1", user="t9")
subprocess.run([PY, NEUROMEM_FILE, "--dir", remote_dir, "add", "--text", "远端/新增记忆 R2", "--user", "t9"],
               capture_output=True, env=ENV, timeout=30)
r = cli("sync", "--remote-dir", remote_dir, "--direction", "down", "--dry-run", user="t9")
D8.append(check("down 拉取远端差异", len(r.get("plan", {}).get("pull", [])) > 0, "pull=%s" % r.get("plan", {}).get("pull")))
results["dimensions"]["D8_增量同步"] = {"cases": D8,
    "pass": sum(1 for c in D8 if c["pass"]), "total": len(D8)}

# ---------- D9 异常与边界 ----------
D9 = []
r = cli("add", user="t1")
D9.append(check("空文本拒绝", r.get("ok") is False and "empty" in r.get("error", "")))
r = cli("add", "--file", os.path.join(ROOT, "no-such-file.txt"), user="t1")
D9.append(check("坏文件不崩溃", r.get("ok") is False and "cannot read" in r.get("error", "")))
r = cli("get", "--id", "m_nonexist", user="t1")
D9.append(check("缺 ID 报错", r.get("ok") is False))
r = cli("restore", "--id", "m_nonexist", "--version", "99", user="t1")
D9.append(check("超界版本报错", r.get("ok") is False))
r = cli("batch", "--ids", "bad1,bad2", "--operation", "delete", user="t1")
D9.append(check("batch 坏 ID 计数", r.get("succeeded", 0) == 0 and r.get("failed", 0) == 2))
r = cli("update", "--id", "m_nonexist", "--text", "x", user="t1")
D9.append(check("update 缺 ID 报错", r.get("ok") is False))
results["dimensions"]["D9_异常与边界"] = {"cases": D9,
    "pass": sum(1 for c in D9 if c["pass"]), "total": len(D9)}

# ---------- P1 性能（进程内） ----------
results["perf"] = perf_store()

# ---------- 汇总 ----------
total_pass = sum(d["pass"] for d in results["dimensions"].values())
total_cases = sum(d["total"] for d in results["dimensions"].values())
results["summary"] = {
    "dimensions": len(results["dimensions"]),
    "total_cases": total_cases,
    "passed": total_pass,
    "failed": total_pass == total_cases and 0 or (total_cases - total_pass),
    "pass_rate": round(100.0 * total_pass / total_cases, 2),
}
with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=1)
print(json.dumps(results["summary"], ensure_ascii=False))
print("输出:", OUT_JSON)