#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.0 vs v2.0 性能对比：写入吞吐 + 检索延迟（含向量/BM25 融合后的开销）。
用法: python perf_compare.py <PY> <v1脚本目录> <v2脚本目录> <输出json>
"""
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

PY = sys.argv[1]
V1_DIR = sys.argv[2]
V2_DIR = sys.argv[3]
OUT = sys.argv[4]

TOPICS = ["张三喜欢喝美式咖啡", "项目 Alpha 预算 50 万元", "李四负责前端团队",
          "用户偏好深色主题界面", "王五的生日是 3 月 12 日", "服务器部署在华东区",
          "每周五下午开周会", "客户合同金额 120 万元", "产品代号 星火计划",
          "回复邮件要用正式语气", "数据库选型倾向 PostgreSQL", "团队共有 8 人",
          "报销流程需要两级审批", "测试环境地址 10.0.0.21", "接口超时设置 30 秒",
          "喜欢用 VS Code 写代码", "文档统一放腾讯文档", "代码评审至少 1 人",
          "发版窗口固定在周四晚", "客户偏好电话沟通"]
QUERIES = ["谁喜欢喝咖啡", "项目预算多少", "前端谁负责", "界面主题偏好",
           "服务器在哪", "周会什么时候开", "合同多少钱", "报销怎么走",
           "接口超时多少", "发版时间"]


def run(script_dir, root, args, timeout=90):
    env = dict(os.environ)
    env["NEUROMEM_DIR"] = root
    t0 = time.time()
    r = subprocess.run([PY, "neuromem.py"] + args, cwd=script_dir,
                       capture_output=True, text=True, encoding="utf-8",
                       env=env, timeout=timeout)
    return r, (time.time() - t0)


def seed(script_dir, root, n):
    """写入 n 条记忆，返回总耗时。"""
    env = dict(os.environ)
    env["NEUROMEM_DIR"] = root
    t0 = time.time()
    # 批量导入走 batch add（v1.1 也支持 batch）
    for i in range(n):
        base = TOPICS[i % len(TOPICS)]
        text = "%s（编号%d）" % (base, i)
        subprocess.run([PY, "neuromem.py", "add", "--user", "p", "--text", text,
                        "--importance", "0.6"],
                       cwd=script_dir, capture_output=True, text=True,
                       encoding="utf-8", env=env, timeout=60)
    return time.time() - t0


def bench(script_dir, root):
    """检索延迟：10 个查询 x 3 轮。"""
    times = []
    for _ in range(3):
        for q in QUERIES:
            r, dt = run(script_dir, root, ["search", "--user", "p", "--query", q,
                                           "--top-k", "5", "--no-cache"])
            times.append(dt)
    times.sort()
    return {
        "avg_ms": round(sum(times) / len(times) * 1000, 1),
        "p50_ms": round(times[len(times) // 2] * 1000, 1),
        "p95_ms": round(times[int(len(times) * 0.95)] * 1000, 1),
        "samples": len(times),
    }


def main():
    n = 200
    result = {"memory_count": n, "queries": len(QUERIES), "rounds": 3}
    for label, script_dir in (("v1.1.0", V1_DIR), ("v2.0.0", V2_DIR)):
        root = tempfile.mkdtemp(prefix="nmem-perf-")
        try:
            seed_dt = seed(script_dir, root, n)
            latency = bench(script_dir, root)
            result[label] = {
                "seed_seconds": round(seed_dt, 1),
                "add_ms_per_mem": round(seed_dt / n * 1000, 1),
                "search": latency,
            }
            print(label, "| seed %.1fs (%.1f ms/条) | 检索 avg %sms p50 %sms p95 %sms" % (
                seed_dt, seed_dt / n * 1000,
                latency["avg_ms"], latency["p50_ms"], latency["p95_ms"]))
        finally:
            shutil.rmtree(root, ignore_errors=True)
    v1, v2 = result["v1.1.0"]["search"], result["v2.0.0"]["search"]
    result["delta"] = {
        "avg_ms": round(v2["avg_ms"] - v1["avg_ms"], 1),
        "avg_pct": round((v2["avg_ms"] / v1["avg_ms"] - 1) * 100, 1) if v1["avg_ms"] else 0,
        "add_ms_per_mem_pct": round((result["v2.0.0"]["add_ms_per_mem"] /
                                     result["v1.1.0"]["add_ms_per_mem"] - 1) * 100, 1),
    }
    print("检索延迟变化: %+.1f ms (%+.1f%%)" % (result["delta"]["avg_ms"], result["delta"]["avg_pct"]))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("输出:", OUT)


if __name__ == "__main__":
    random.seed(42)
    main()
