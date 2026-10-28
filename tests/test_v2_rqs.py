#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mile-neuro-memory v2.0 专项能力验收测试（RQ-01~11）。
覆盖提升计划中每项 RQ 的可验证验收标准。CLI 端到端。
用法: python test_v2_rqs.py <python_exe> <scripts_dir> <out_json>
"""
import json
import os
import subprocess
import sys
import tempfile
import time

PY = sys.argv[1] if len(sys.argv) > 1 else sys.executable
SCRIPTS = sys.argv[2] if len(sys.argv) > 2 else "."
NEUROMEM = os.path.join(SCRIPTS, "neuromem.py")
OUT = sys.argv[3] if len(sys.argv) > 3 else "results-v2.json"
ROOT = tempfile.mkdtemp(prefix="nmem-v2-accept-")
ENV = dict(os.environ, NEUROMEM_DIR=ROOT)


def cli(*args, user="t1"):
    cmd = [PY, NEUROMEM, "--dir", ROOT]
    cmd += list(args)
    if user:
        cmd += ["--user", user]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=ENV, timeout=90)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "{}")
    except Exception:
        return {"ok": False, "error": "non-json", "raw": r.stdout[-200:], "stderr": r.stderr[-300:]}


def check(name, cond, detail=""):
    return {"name": name, "pass": bool(cond), "detail": detail}


results = {"meta": {"engine": "v2.0", "root": ROOT}, "rqs": {}, "summary": {}}

# ---------- RQ-01 向量语义检索 ----------
R = []
cli("add", "--text", "张三喜欢喝美式咖啡，常去公司楼下瑞幸", "--importance", "0.7")
cli("add", "--text", "李四负责前端团队，偏好深色主题", "--importance", "0.7")
r = cli("search", "--query", "常去瑞幸买咖啡的人是谁", "--top-k", "3", "--no-cache")
top1 = r.get("results", [{}])[0].get("summary", "")
R.append(check("同义改写语义命中", "瑞幸" in top1 and "张三" in top1, f"top1={top1}"))
r = cli("search", "--query", "谁负责前端开发", "--top-k", "3", "--no-cache")
top1 = r.get("results", [{}])[0].get("summary", "")
R.append(check("语义近似命中", "李四" in top1 and "前端" in top1, f"top1={top1}"))
results["rqs"]["RQ-01_向量语义检索"] = {"cases": R,
    "pass": sum(1 for c in R if c["pass"]), "total": len(R)}

# ---------- RQ-02 BM25 关键词 ----------
R = []
cli("add", "--text", "项目 Alpha 预算 50 万元，2026年3月上线")
r = cli("search", "--query", "Alpha 预算 万元", "--top-k", "3", "--no-cache")
R.append(check("关键词精确命中", any("预算" in x.get("summary", "") for x in r.get("results", []))))
results["rqs"]["RQ-02_BM25关键词"] = {"cases": R,
    "pass": sum(1 for c in R if c["pass"]), "total": len(R)}

# ---------- RQ-03 自动捕获 ----------
R = []
r = cli("capture", "--text", "项目 Alpha 预算调整为 80 万元，王五负责前端团队，用户偏好深色主题界面")
R.append(check("3事实对话捕获≥2", r.get("captured", 0) >= 2, f"captured={r.get('captured')} facts={r.get('facts')}"))
r = cli("search", "--query", "王五负责什么", "--top-k", "5", "--no-cache")
R.append(check("捕获内容可检索", any("王五" in x.get("summary", "") for x in r.get("results", []))))
results["rqs"]["RQ-03_自动捕获"] = {"cases": R,
    "pass": sum(1 for c in R if c["pass"]), "total": len(R)}

# ---------- RQ-04 MCP ----------
R = []
inp = "\n".join([
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}',
    '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"memory_add","arguments":{"text":"MCP测试：赵六喜欢骑自行车"}}}',
    '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"memory_search","arguments":{"query":"赵六爱好","top_k":3}}}',
]) + "\n"
r = subprocess.run([PY, NEUROMEM, "--dir", ROOT, "mcp", "--user", "t1"],
                   input=inp, capture_output=True, text=True, encoding="utf-8", env=ENV, timeout=60)
resp = {}
for line in r.stdout.strip().splitlines():
    try:
        d = json.loads(line)
        resp[d.get("id")] = d
    except Exception:
        pass
R.append(check("initialize 握手", resp.get(1, {}).get("result", {}).get("serverInfo", {}).get("name") == "mile-neuro-memory",
               str(resp.get(1, {}).get("result", {}).get("serverInfo", {}))))
R.append(check("MCP add 成功", resp.get(2, {}).get("result", {}).get("content", [{}])[0].get("text", "").find("ok") >= 0))
srch = resp.get(3, {}).get("result", {}).get("content", [{}])[0].get("text", "")
R.append(check("MCP search 命中", "赵六" in srch and "骑自行车" in srch, srch[:80]))
results["rqs"]["RQ-04_MCP协议"] = {"cases": R,
    "pass": sum(1 for c in R if c["pass"]), "total": len(R)}

# ---------- RQ-05 实体关系图谱 ----------
R = []
r = cli("graph")
R.append(check("图谱有节点", r.get("nodes", 0) >= 3, f"nodes={r.get('nodes')} edges={r.get('edges')}"))
r = cli("graph", "--entity", "张三")
R.append(check("实体查询返回", r.get("ok") is True and r.get("entity") == "张三", str(r.get("relations", []))[:60])
         if r.get("ok") else check("实体查询返回", False, "entity not found"))
results["rqs"]["RQ-05_实体关系图谱"] = {"cases": R,
    "pass": sum(1 for c in R if c["pass"]), "total": len(R)}

# ---------- RQ-06 反馈闭环 ----------
R = []
mid = cli("add", "--text", "钱七喜欢喝拿铁", "--importance", "0.7").get("id", "")
r = cli("correct", "--id", mid, "--text", "钱七改喝美式了")
R.append(check("correct 沉淀纠偏规则", r.get("feedback", {}).get("rule_added") is True,
               json.dumps(r.get("feedback", {}), ensure_ascii=False)))
r = cli("search", "--query", "钱七喝什么咖啡", "--top-k", "3", "--no-cache")
R.append(check("纠偏后检索到新表述", any("美式" in x.get("summary", "") for x in r.get("results", []))))
# 同类不再复现：纠偏后新入库的"旧说法"必须排在纠偏结果之后
cli("add", "--text", "钱七爱喝拿铁的习惯很多年了", "--importance", "0.7")
r = cli("search", "--query", "钱七喝什么咖啡", "--top-k", "5", "--no-cache")
_res = r.get("results", [])
_new_pos = next((i for i, x in enumerate(_res) if "美式" in x.get("summary", "")), 999)
_old_pos = next((i for i, x in enumerate(_res) if "拿铁" in x.get("summary", "")), 999)
R.append(check("纠正后旧说法不再排前", _new_pos < _old_pos,
               f"新表述位次={_new_pos}, 旧说法位次={_old_pos}"))
results["rqs"]["RQ-06_反馈闭环"] = {"cases": R,
    "pass": sum(1 for c in R if c["pass"]), "total": len(R)}

# ---------- RQ-07 自主进化 ----------
R = []
m1 = cli("add", "--text", "孙八喜欢喝绿茶").get("id", "")
cli("correct", "--id", m1, "--text", "孙八改喝红茶了")
m2 = cli("add", "--text", "孙八偏好浅色主题").get("id", "")
cli("correct", "--id", m2, "--text", "孙八偏好深色主题")
r = cli("evolve")
R.append(check("进化沉淀规则", r.get("evolved", 0) >= 1, json.dumps(r.get("rules", []), ensure_ascii=False)[:120]))
results["rqs"]["RQ-07_自主进化"] = {"cases": R,
    "pass": sum(1 for c in R if c["pass"]), "total": len(R)}

# ---------- RQ-08 可视化面板 ----------
R = []
import http.server, threading, urllib.request
from pathlib import Path
# 起 serve 线程
store_dir = ROOT
server_proc = subprocess.Popen([PY, NEUROMEM, "--dir", ROOT, "serve", "--port", "17993", "--user", "t1"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=ENV)
time.sleep(2)
try:
    r = json.loads(urllib.request.urlopen("http://127.0.0.1:17993/api/ui/stats", timeout=5).read())
    R.append(check("ui/stats 数据完整", "timeline" in r and "cache" in r and "rules" in r,
                   f"total={r.get('total')}"))
    page = urllib.request.urlopen("http://127.0.0.1:17993/ui", timeout=5).read().decode("utf-8", "replace")
    R.append(check("UI 页面可访问", "记忆面板" in page, f"{len(page)} bytes"))
except Exception as e:
    R.append(check("ui/stats 数据完整", False, str(e)))
    R.append(check("UI 页面可访问", False, str(e)))
server_proc.terminate()
results["rqs"]["RQ-08_可视化面板"] = {"cases": R,
    "pass": sum(1 for c in R if c["pass"]), "total": len(R)}

# ---------- RQ-09 离线巩固后台化（守护参数可解析+启动） ----------
R = []
try:
    r = subprocess.run([PY, NEUROMEM, "--dir", ROOT, "consolidate", "--daemon", "--interval-min", "5", "--user", "t1"],
                       capture_output=True, text=True, encoding="utf-8", env=ENV, timeout=4)
    R.append(check("daemon 模式启动", "daemon" in r.stdout, r.stdout[:80]))
except subprocess.TimeoutExpired as e:
    # 超时=守护循环在运行（预期行为），检查启动输出
    out_before = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else str(e.stdout or "")
    R.append(check("daemon 模式启动", "daemon" in out_before, out_before[:80]))
results["rqs"]["RQ-09_后台巩固"] = {"cases": R,
    "pass": sum(1 for c in R if c["pass"]), "total": len(R)}

# ---------- RQ-10 AEAD 加密 ----------
R = []
r = cli("add", "--text", "绝密数据 V2-8888", "--encrypt", "--secret", "s3cr3t", "--importance", "0.9", user="sec")
eid = r.get("id", "")
import glob
fpath = None
for p in glob.glob(os.path.join(ROOT, "users", "sec", "memories", "**", "*.json"), recursive=True):
    if eid in p:
        fpath = p
        break
drawn = ""
if fpath:
    with open(fpath, encoding="utf-8") as f:
        drawn = f.read()
R.append(check("v2 密文落盘", "enc:v2:" in drawn and "8888" not in drawn, "enc:v2 present"))
r = cli("get", "--id", eid, "--secret", "s3cr3t", user="sec")
R.append(check("解密读取", "8888" in r.get("memory", {}).get("text", "")))
# 篡改检测
if fpath:
    d = json.load(open(fpath, encoding="utf-8"))
    c = d["text"]
    d["text"] = c[:-2] + ("A" if c[-2] != "A" else "B")
    json.dump(d, open(fpath, "w", encoding="utf-8"), ensure_ascii=False)
    r = cli("get", "--id", eid, "--secret", "s3cr3t", user="sec")
    R.append(check("篡改可检出", r.get("ok") is False or "integrity" in r.get("error", "").lower(),
                   str(r.get("error", ""))[:60]))
results["rqs"]["RQ-10_AEAD加密"] = {"cases": R,
    "pass": sum(1 for c in R if c["pass"]), "total": len(R)}

# ---------- RQ-11 基准跑分 ----------
R = []
r = cli("benchmark", user="bench")
b = r.get("benchmark", {})
R.append(check("跑分产出指标", "accuracy" in b and "recall" in b,
               f"accuracy={b.get('accuracy')}% recall={b.get('recall')}%"))
R.append(check("准确率可接受", b.get("accuracy", 0) >= 80, f"{b.get('accuracy')}%"))
results["rqs"]["RQ-11_基准跑分"] = {"cases": R,
    "pass": sum(1 for c in R if c["pass"]), "total": len(R)}

# 汇总
total_cases = sum(v["total"] for v in results["rqs"].values())
passed = sum(v["pass"] for v in results["rqs"].values())
results["summary"] = {"rq_count": len(results["rqs"]), "total_cases": total_cases,
                      "passed": passed, "pass_rate": round(100.0 * passed / total_cases, 2)}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=1)
print(json.dumps(results["summary"], ensure_ascii=False))
print("输出:", OUT)