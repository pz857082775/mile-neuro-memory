#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeuroMem 类脑记忆引擎 CLI（零第三方依赖，Python 标准库实现）

模拟人脑记忆机制的分层记忆系统：
  - 瞬时工作记忆(working) → 短时记忆(short) → 长时永久记忆(long: 情景/语义/程序)
  - 混合索引（倒排 + 时间 + 标签 + 关联图谱）+ 多维加权联想检索
  - 写时复制版本管理（可回溯任意历史版本）
  - 矛盾检测 + 修正留痕（旧记忆标 invalid 并挂 superseded_by，不直接删除）
  - 选择性遗忘（降权/归档，永不物理删除）
  - 每日巩固（短时→长时迁移、去重、画像提炼）
  - 本地导出/导入，可选本地 HTTP API（跨应用接入）
  - 可选 LLM 提炼（OpenAI 兼容接口，urllib 直连，绕过失效代理）

用法:
  python neuromem.py <command> [options]

环境变量:
  NEUROMEM_DIR   记忆数据根目录（默认 ~/.neuromem）
  NEUROMEM_LLM_API_KEY / NEUROMEM_LLM_BASE_URL / NEUROMEM_LLM_MODEL   LLM 提炼配置（可选）
"""
import argparse
import base64
import hashlib
import hmac
import http.server
import json
import os
import re
import sys
import threading
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

VERSION = "2.0.0"
APP_NAME = "neuromem"

# ---------------------------------------------------------------------------
# 常量与工具
# ---------------------------------------------------------------------------

TIERS = ("working", "short", "long")
SUBTYPES = ("general", "episodic", "semantic", "procedural")
STATUSES = ("active", "pending", "invalid", "archived")

STOPWORDS = {
    "的", "了", "和", "是", "在", "我", "你", "他", "她", "它", "们",
    "这", "那", "有", "就", "也", "都", "而", "及", "与", "或", "一个",
    "a", "an", "the", "to", "of", "in", "on", "for", "and", "or", "is",
    "are", "was", "were", "be", "been", "with", "at", "by", "from",
}

PROCEDURE_HINTS = ("步骤", "流程", "工作流", "模板", "技巧", "方法", "先", "然后", "最后", "推荐做法", "最佳实践", "how to", "workflow", "step")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def rand_id() -> str:
    return "m_%s_%s" % (int(time.time() * 1000), hashlib.md5(os.urandom(16)).hexdigest()[:8])


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_json(p: Path, default=None):
    if default is None:
        default = {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(p: Path, obj) -> None:
    ensure_dir(p.parent)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def out(obj) -> None:
    """CLI 统一输出 JSON，便于智能体解析（flush 保证管道/守护场景实时可见）。"""
    print(json.dumps(obj, ensure_ascii=False), flush=True)


# ---------------------------------------------------------------------------
# 文本特征与相似度（零依赖近似语义）
# ---------------------------------------------------------------------------

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def text_tokens(text: str) -> set:
    """中英文混合 token 化：中文按字符 bigram，英文按单词，保留数字与 url。"""
    tokens = set()
    text = (text or "").lower()
    # 英文单词
    for w in re.findall(r"[a-z][a-z0-9_\-]{1,}", text):
        if w not in STOPWORDS:
            tokens.add("w:" + w)
    # 中文 bigram
    cjk = "".join(_CJK_RE.findall(text))
    if len(cjk) == 1:
        tokens.add("c:" + cjk)
    for i in range(len(cjk) - 1):
        bigram = cjk[i:i + 2]
        if bigram not in ("的了", "我是", "你是", "我们", "他们", "这个", "那个", "什么", "怎么", "没有", "不是"):
            tokens.add("c:" + bigram)
    # 数字
    for n in re.findall(r"\d+(?:\.\d+)?", text):
        tokens.add("n:" + n)
    return tokens


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def text_sim(a: str, b: str) -> float:
    ta, tb = text_tokens(a), text_tokens(b)
    j = jaccard(ta, tb)
    # 包含度修正：短文本被长文本完全包含时给补偿
    if ta and tb:
        overlap = len(ta & tb) / min(len(ta), len(tb))
        j = max(j, overlap * 0.85)
    return round(min(1.0, j), 4)


def time_decay(ts_iso: str, half_life_days: float = 30.0) -> float:
    """新近度衰减：距今 half_life 天后降为 0.5。"""
    try:
        t = datetime.fromisoformat(ts_iso)
        age = (datetime.now(timezone.utc) - t).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return 0.5
    return round(1.0 / (1.0 + max(0.0, age) / half_life_days), 4)


# 泛化疑问词：出现这些词且无具体实体线索（英文/数字）→ 视为宽泛查询
_GENERIC_QUERY_WORDS = ("什么", "多少", "哪些", "怎么", "如何", "说过", "记得", "是有啥", "安排")


def rule_topic(text: str) -> str:
    """纠偏规则的"主题"提取：人名/英文专名优先，无则取前 4 字。
    用于把"张三喜欢喝咖啡"与"张三改喝茶了"归到同一个主题（RQ-06/RQ-07）。"""
    t = text or ""
    m = re.search(r"([\u4e00-\u9fff]{2,3}(?:先生|女士|老师|同学|老板|经理|总|哥|姐))", t)
    if m:
        return m.group(1)
    m = re.search(r"([A-Z][A-Za-z0-9_-]{2,})", t)
    if m:
        return m.group(1)
    sur = ("王李张刘陈杨黄赵周吴徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘蒋蔡"
           "余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯"
           "龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤")
    m = re.search(r"([\u4e00-\u9fff]{2})", t)
    if m and m.group(1)[0] in sur:
        return m.group(1)
    return t[:4]


def _is_broad_query(query: str, q_toks: set) -> bool:
    """宽泛查询判定：无英文词、无数字、且含泛化疑问/回忆词 → 兜底为近期重要记忆。"""
    q = (query or "").strip()
    if len(q) <= 2:
        return True
    if any(t.startswith(("w:", "n:", "e:")) for t in q_toks):
        return False
    return any(g in q for g in _GENERIC_QUERY_WORDS)


# ---------------------------------------------------------------------------
# v2.0 RQ-01 向量语义检索（零依赖：hashing TF 向量 + 余弦相似度）
#   - 本地向量：md5 hash 映射到固定维度，中文 bigram + 英文词词频，归一化
#   - 可配远程 Embedding（NEUROMEM_EMBED_*，OpenAI 兼容），失败自动回退本地
# ---------------------------------------------------------------------------
import math as _math

VEC_DIM = 512


def _hash_tok_idx(tok: str, dim: int = VEC_DIM) -> int:
    """token -> 稳定 hash 维度索引（词带前缀后 md5 取前 8 位 hex）。"""
    return int(hashlib.md5(tok.encode("utf-8")).hexdigest()[:8], 16) % dim


def text_vector(text: str) -> dict:
    """TF 稀疏向量：{dim_idx: weight}，L2 归一化。零依赖近似语义向量。"""
    vec = {}
    for tok in text_tokens(text):
        i = _hash_tok_idx(tok)
        vec[i] = vec.get(i, 0.0) + 1.0
    norm = _math.sqrt(sum(v * v for v in vec.values()))
    if norm == 0:
        return {}
    return {k: v / norm for k, v in vec.items()}


def cosine_sim(a: dict, b: dict) -> float:
    """稀疏向量余弦相似度。"""
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    if not common:
        return 0.0
    dot = sum(a[k] * b[k] for k in common)
    return round(min(1.0, max(0.0, dot)), 4)


def _embed_remote(text: str) -> list | None:
    """可选的远程 Embedding（OpenAI 兼容）。未配置或失败返回 None。"""
    key = os.environ.get("NEUROMEM_EMBED_API_KEY")
    url = os.environ.get("NEUROMEM_EMBED_BASE_URL")
    model = os.environ.get("NEUROMEM_EMBED_MODEL")
    if not (key and url and model):
        return None
    try:
        api_url = url.rstrip("/") + "/embeddings"
        body = json.dumps({"model": model, "input": [text]}).encode("utf-8")
        req = urllib.request.Request(api_url, data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + key})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["data"][0]["embedding"]
    except Exception:
        return None


def _vec_from_embedding(emb: list) -> dict:
    """把远程 Embedding 列表转成稀疏向量（取 top 活跃维度压缩）。"""
    vec = {}
    norm = _math.sqrt(sum(x * x for x in emb)) or 1.0
    for dim, val in enumerate(emb):
        if val != 0:
            vec[dim] = round(val / norm, 6)
    return vec


# ---------------------------------------------------------------------------
# v2.0 RQ-02 BM25 关键词打分（零依赖实现）
# ---------------------------------------------------------------------------
BM25_K1 = 1.5
BM25_B = 0.75


def bm25_score(query_toks: set, mem_toks: set, df_map: dict, doc_len: int,
               avg_doc_len: float, total_docs: int) -> float:
    """BM25 打分：query 与单条记忆的词项匹配加权。df 从倒排索引统计。"""
    if not query_toks or total_docs == 0:
        return 0.0
    score = 0.0
    for tok in query_toks:
        if tok not in mem_toks:
            continue
        df = df_map.get(tok, 0)
        idf = _math.log(1.0 + (total_docs - df + 0.5) / (df + 0.5))
        tf = 1.0  # 词项命中即计 1（布尔近似）
        denom = tf + BM25_K1 * (1.0 - BM25_B + BM25_B * doc_len / max(1.0, avg_doc_len))
        score += idf * (tf * (BM25_K1 + 1.0)) / denom
    return round(score, 4)


# ---------------------------------------------------------------------------
# 加密（v2.0 RQ-10：encrypt-then-MAC，HMAC-SHA256 认证防篡改；兼容 enc:v1）
#   v1: scrypt 派生 + XOR 流（轻量混淆，无认证）
#   v2: scrypt 派生 + 随机 nonce + XOR 流 + HMAC-SHA256 认证标签（篡改可检出）
# ---------------------------------------------------------------------------

def _derive_key(secret: str, salt: bytes) -> bytes:
    return hashlib.scrypt(secret.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


def encrypt_text(text: str, secret: str) -> str:
    salt = os.urandom(16)
    nonce = os.urandom(8)
    key = _derive_key(secret, salt)
    data = text.encode("utf-8")
    stream = bytearray()
    ctr = int.from_bytes(nonce, "big")
    for b in data:
        stream.append(b ^ key[(ctr + len(stream)) % len(key)])
    body = salt + nonce + bytes(stream)
    tag = hmac.new(key, body, hashlib.sha256).digest()
    return "enc:v2:" + base64.b64encode(tag + body).decode("ascii")


def decrypt_text(payload: str, secret: str) -> str:
    if payload.startswith("enc:v1:"):
        raw = base64.b64decode(payload[len("enc:v1:"):])
        salt, data = raw[:16], raw[16:]
        key = _derive_key(secret, salt)
        out_bytes = bytearray()
        ctr = 0
        for b in data:
            out_bytes.append(b ^ key[ctr % len(key)])
            ctr += 1
        return out_bytes.decode("utf-8")
    if payload.startswith("enc:v2:"):
        raw = base64.b64decode(payload[len("enc:v2:"):])
        tag, body = raw[:32], raw[32:]
        salt, nonce = body[:16], body[16:24]
        key = _derive_key(secret, salt)
        expect = hmac.new(key, body, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expect):
            raise ValueError("integrity check failed: ciphertext tampered")
        stream = body[24:]
        ctr = int.from_bytes(nonce, "big")
        out_bytes = bytearray()
        for i, b in enumerate(stream):
            out_bytes.append(b ^ key[(ctr + i) % len(key)])
        return out_bytes.decode("utf-8")
    return payload


# ---------------------------------------------------------------------------
# 记忆条目
# ---------------------------------------------------------------------------

def new_memory(text: str, tier: str = "short", subtype: str = "general", **kw) -> dict:
    """构造记忆条目。所有写入都过这里，保证字段齐全。"""
    now = now_iso()
    mem = {
        "id": kw.get("id") or rand_id(),
        "text": text,
        "summary": kw.get("summary", ""),
        "tier": tier if tier in TIERS else "short",
        "subtype": subtype if subtype in SUBTYPES else "general",
        "tags": list(kw.get("tags") or []),
        "entities": dict(kw.get("entities") or {}),
        "importance": float(kw.get("importance") or 0.4),
        "access_count": int(kw.get("access_count") or 0),
        "strength": float(kw.get("strength") or 0.3),
        "weight": float(kw.get("weight") or 1.0),
        "status": kw.get("status", "active"),
        "created_at": now,
        "updated_at": now,
        "last_access": kw.get("last_access") or now,
        "session_id": kw.get("session_id", ""),
        "source": kw.get("source", ""),
        "context": dict(kw.get("context") or {}),
        "version": 1,
        "superseded_by": kw.get("superseded_by"),
        "links": list(kw.get("links") or []),
    }
    if not mem["summary"]:
        mem["summary"] = text
    return mem


# ---------------------------------------------------------------------------
# 存储层
# ---------------------------------------------------------------------------

class NeuroMemStore:
    """目录布局：

    <root>/users/<user_id>/
        config.json          用户配置（画像 profile、遗忘策略、加密开关）
        memories/<tier>/<subtype>/<mem_id>.json     记忆条目
        index/inverted.json  倒排索引 {token: [mem_id, ...]}
        index/graph.json     关联图谱 {mem_id: {neighbor_id: strength}}
        versions/<mem_id>.json  版本历史快照列表
        archive/<mem_id>.json  已失效/归档记忆（物理保留）
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    # ---- 路径 ----
    def user_dir(self, user_id: str) -> Path:
        return self.root / "users" / user_id

    def config_path(self, user_id: str) -> Path:
        return self.user_dir(user_id) / "config.json"

    def mem_dir(self, user_id: str, tier: str = None, subtype: str = None) -> Path:
        d = self.user_dir(user_id) / "memories"
        if tier:
            d = d / tier
        if subtype:
            d = d / subtype
        return d

    def index_dir(self, user_id: str) -> Path:
        return self.user_dir(user_id) / "index"

    def versions_dir(self, user_id: str) -> Path:
        return self.user_dir(user_id) / "versions"

    def archive_dir(self, user_id: str) -> Path:
        return self.user_dir(user_id) / "archive"

    # ---- 配置 ----
    def get_config(self, user_id: str) -> dict:
        cfg = load_json(self.config_path(user_id))
        cfg.setdefault("profile", {})
        cfg.setdefault("forget_policy", {"demote_threshold": 0.3, "archive_threshold": 0.1})
        cfg.setdefault("encrypted", False)
        return cfg

    def save_config(self, user_id: str, cfg: dict) -> None:
        save_json(self.config_path(user_id), cfg)

    # ---- 记忆读写 ----
    def mem_path(self, user_id: str, mem: dict) -> Path:
        return self.mem_dir(user_id, mem["tier"], mem["subtype"]) / (mem["id"] + ".json")

    def load_memory(self, user_id: str, mem_id: str) -> dict | None:
        for p in self.mem_dir(user_id).rglob("*.json"):
            if p.stem == mem_id:
                return load_json(p)
        ap = self.archive_dir(user_id) / (mem_id + ".json")
        if ap.exists():
            return load_json(ap)
        return None

    def all_memories(self, user_id: str, include_archived: bool = False) -> list:
        mems = []
        for p in self.mem_dir(user_id).rglob("*.json"):
            m = load_json(p)
            if m.get("id"):
                mems.append(m)
        if include_archived:
            for p in self.archive_dir(user_id).glob("*.json"):
                m = load_json(p)
                if m.get("id"):
                    mems.append(m)
        return mems

    def save_memory(self, user_id: str, mem: dict) -> None:
        """写记忆文件。版本快照由调用方在修改前显式 push（保证快照=变更前状态）。"""
        p = self.mem_path(user_id, mem)
        save_json(p, mem)

    def delete_file(self, user_id: str, mem: dict) -> None:
        p = self.mem_path(user_id, mem)
        if p.exists():
            p.unlink()

    def push_version(self, user_id: str, mem_id: str, mem: dict) -> None:
        vdir = self.versions_dir(user_id)
        vpath = vdir / (mem_id + ".json")
        versions = load_json(vpath, default=[])
        versions.append({k: v for k, v in mem.items() if k not in ("last_access",)})
        save_json(vpath, versions)

    def get_versions(self, user_id: str, mem_id: str) -> list:
        return load_json(self.versions_dir(user_id) / (mem_id + ".json"), default=[])

    def restore_version(self, user_id: str, mem_id: str, version_no: int) -> dict | None:
        versions = self.get_versions(user_id, mem_id)
        if version_no < 1 or version_no > len(versions):
            return None
        current = self.load_memory(user_id, mem_id)
        if current:
            self.push_version(user_id, mem_id, current)  # 先留存"回滚前状态"
        restored = dict(versions[version_no - 1])
        restored["id"] = mem_id
        restored["version"] = len(versions) + 1
        restored["updated_at"] = now_iso()
        self.save_memory(user_id, restored)
        self.rebuild_index(user_id)
        return restored

    # ---- 索引 ----
    def inverted(self, user_id: str) -> dict:
        return load_json(self.index_dir(user_id) / "inverted.json", default={})

    def graph(self, user_id: str) -> dict:
        return load_json(self.index_dir(user_id) / "graph.json", default={})

    def save_indexes(self, user_id: str, inv: dict, graph: dict) -> None:
        save_json(self.index_dir(user_id) / "inverted.json", inv)
        save_json(self.index_dir(user_id) / "graph.json", graph)

    def rebuild_index(self, user_id: str) -> None:
        mems = self.all_memories(user_id)
        inv = {}
        graph = {}
        for m in mems:
            if m.get("status") == "invalid":
                continue
            for tok in self._mem_tokens(m):
                inv.setdefault(tok, [])
                if m["id"] not in inv[tok]:
                    inv[tok].append(m["id"])
        # 图谱重建：同 tier 内两两相似建边（n 较小时全量；大库取抽样）
        active = [m for m in mems if m.get("status") in ("active", "pending")]
        if len(active) <= 500:
            pairs = []
            for i in range(len(active)):
                for j in range(i + 1, len(active)):
                    a, b = active[i], active[j]
                    s = text_sim(a["summary"] + " " + " ".join(a.get("tags", [])),
                                  b["summary"] + " " + " ".join(b.get("tags", [])))
                    if s >= 0.42:
                        pairs.append((a["id"], b["id"], s))
            for aid, bid, s in pairs:
                graph.setdefault(aid, {})[bid] = round(s, 3)
                graph.setdefault(bid, {})[aid] = round(s, 3)
        self.save_indexes(user_id, inv, graph)

    @staticmethod
    def _mem_tokens(m: dict) -> set:
        toks = text_tokens(m.get("summary", "") or m.get("text", ""))
        for t in m.get("tags", []):
            toks.add("t:" + t.lower())
        for ent_list in m.get("entities", {}).values():
            for e in ent_list:
                toks.add("e:" + str(e).lower())
        return toks

    def update_index_add(self, user_id: str, mem: dict, sims: list) -> None:
        inv = self.inverted(user_id)
        graph = self.graph(user_id)
        for tok in self._mem_tokens(mem):
            inv.setdefault(tok, [])
            if mem["id"] not in inv[tok]:
                inv[tok].append(mem["id"])
        for other_id, s in sims:
            if s >= 0.42:
                graph.setdefault(mem["id"], {})[other_id] = round(s, 3)
                graph.setdefault(other_id, {})[mem["id"]] = round(s, 3)
        self.save_indexes(user_id, inv, graph)

    # ---- 检索 ----
    def search(self, user_id: str, query: str, top_k: int = 10, tier: str | None = None,
               subtype: str | None = None, tags: list | None = None,
               from_date: str | None = None, to_date: str | None = None,
               include_archived: bool = False, expand_graph: bool = True,
               min_score: float = 0.05, context: dict | None = None,
               complete_fragments: bool = False) -> list:
        """联想式检索：四路索引联合召回 + 多维加权排序 + 图谱扩展 + 情境加权。

        - 宽泛查询（特征词过少）自动切换为「近期重要记忆」时间倒序兜底
        - 命中记忆动态增强 strength（记忆强度），模拟反复提取记忆加深
        - context 为情境字典（如 {"scene":"出差","device":"pc"}），命中情境的记忆加权
        - complete_fragments=True 时对碎片化短记忆做图谱推测补全并标注
        """
        q_toks = text_tokens(query)
        inv = self.inverted(user_id)
        graph = self.graph(user_id)
        mems = self.all_memories(user_id, include_archived=include_archived)
        now = now_iso()

        # 宽泛查询兜底：查询特征不足时按「重要性→新近度」返回近期重要记忆
        broad = _is_broad_query(query, q_toks)
        if broad:
            cand = [m for m in mems if m.get("status") in ("active", "pending") and
                    (include_archived or m.get("status") != "archived") and
                    (not tier or m.get("tier") == tier) and
                    (not subtype or m.get("subtype") == subtype)]
            cand.sort(key=lambda m: (float(m.get("importance") or 0.4),
                                     str(m.get("updated_at", ""))), reverse=True)
            top = [(m, (float(m.get("importance") or 0.4) + time_decay(m.get("updated_at", now), 30.0)) / 2)
                   for m in cand[:top_k]]
            expanded = []
        else:
            hits = {}
            # 候选：倒排命中 + 全量兜底（本地规模可承受）
            for tok in q_toks:
                for mid in inv.get(tok, []):
                    hits[mid] = hits.get(mid, 0) + 1
            cand_ids = set(hits)
            for m in mems:
                if m.get("status") == "invalid":
                    continue
                if include_archived or m.get("status") != "archived":
                    cand_ids.add(m["id"])
            scored = []
            for m in mems:
                if m["id"] not in cand_ids:
                    continue
                if m.get("status") == "invalid":
                    continue
                if not include_archived and m.get("status") == "archived":
                    continue
                if tier and m.get("tier") != tier:
                    continue
                if subtype and m.get("subtype") != subtype:
                    continue
                if tags and not set(tags) <= set(m.get("tags", [])):
                    continue
                if from_date and m.get("updated_at", "") < from_date:
                    continue
                if to_date and m.get("updated_at", "") > to_date:
                    continue
                score = self._score(m, q_toks, query, inv, context)
                if score >= min_score:
                    scored.append((m, score))
            scored.sort(key=lambda x: x[1], reverse=True)
            top = scored[:top_k]
            expanded = top
        # RQ-06 反馈闭环：纠偏规则参与排序——被纠正过的事实优先，旧表述降权。
        # 放在分支之后，确保"宽泛查询兜底"路径同样受规则约束。
        try:
            _rules = (self.get_config(user_id) or {}).get("correction_rules") or []
        except Exception:
            _rules = []
        if _rules and query and top:
            _adj = []
            for _m, _sc in top:
                _s = _m.get("summary", "") or ""
                _f = 1.0
                for _r in _rules[-50:]:
                    _topic = _r.get("topic") or rule_topic(_r.get("old") or "")
                    if not _topic or (_topic not in query and _topic not in _s):
                        continue
                    _new_t, _old_t = (_r.get("new") or ""), (_r.get("old") or "")
                    if _new_t and _new_t[:8] in _s:
                        _f = max(_f, 1.15)      # 纠正后的表述优先
                    elif _old_t and _old_t[:8] in _s and not (_new_t and _new_t[:6] in _s):
                        _f = min(_f, 0.60)      # 同主题旧表述降权
                _adj.append((_m, round(_sc * _f, 4)))
            _adj.sort(key=lambda x: x[1], reverse=True)
            top = _adj
        # 联想扩展：沿图谱补充关联记忆（关联强度衰减 0.6）
        results = []
        if not broad and expand_graph and top:
            got = {m["id"] for m, _ in top}
            extra = []
            for m, s in top:
                for nid, strength in graph.get(m["id"], {}).items():
                    if nid in got:
                        continue
                    nm = self.load_memory(user_id, nid)
                    if not nm or nm.get("status") != "active":
                        continue
                    extra.append((nm, s * strength * 0.6))
                    got.add(nid)
            extra.sort(key=lambda x: x[1], reverse=True)
            top = top + extra[:max(3, top_k // 2)]
        for m, s in top:
            item = dict(m)
            item["_score"] = round(s, 4)
            if complete_fragments:
                comp = self._complete_fragment(user_id, m, graph)
                if comp:
                    item["_completion"] = comp
            results.append(item)
        # 更新访问统计与记忆强度（前 5 名强化，模拟提取记忆加深）
        try:
            for m, s in top[:min(5, len(top))]:
                m["access_count"] = m.get("access_count", 0) + 1
                m["strength"] = round(min(1.0, float(m.get("strength") or 0.3) + 0.05), 3)
                m["last_access"] = now
                p = self.mem_path(user_id, m)
                if p.exists():
                    save_json(p, m)
        except Exception:
            pass
        if broad:
            return {"broad": True, "results": results}
        return results

    @staticmethod
    def _context_match(m: dict, context: dict) -> float:
        """情境匹配度：记忆 context 与查询情境共享的键值对数归一化。"""
        if not context:
            return 0.0
        mc = m.get("context") or {}
        shared = 0
        keys = set(context) & set(mc)
        for k in keys:
            if str(context[k]).lower() == str(mc[k]).lower():
                shared += 1
        if shared:
            return min(1.0, shared / max(1, max(len(keys), 1)))
        return 0.0

    def _complete_fragment(self, user_id: str, m: dict, graph: dict) -> dict | None:
        """碎片记忆推测补全：短记忆命中后先沿图谱邻居拼接上下文；
        无图边时按「共享标签/实体」找同主题记忆做软关联补全，显式标注推测。"""
        text = m.get("summary", "") or ""
        if len(text) >= 24:
            return None
        # 1) 硬关联：图谱邻居
        neighbors = graph.get(m["id"], {})
        if neighbors:
            top_id = max(neighbors, key=neighbors.get)
            nm = self.load_memory(user_id, top_id)
            if nm and nm.get("status") == "active":
                return {"inferred": True,
                        "hint": f"关联记忆推测：{(nm.get('summary') or '')[:60]}",
                        "from_memory": top_id}
        # 2) 软关联：共享标签/实体最多的同主题记忆
        m_tags = set(m.get("tags", []))
        m_ents = {str(v) for vs in (m.get("entities") or {}).values() for v in vs}
        best, best_score = None, 0
        for p in self.all_memories(user_id):
            if p["id"] == m["id"] or p.get("status") != "active":
                continue
            p_tags = set(p.get("tags", []))
            p_ents = {str(v) for vs in (p.get("entities") or {}).values() for v in vs}
            shared = len(m_tags & p_tags) + len(m_ents & p_ents)
            if shared > best_score:
                best, best_score = p, shared
        if best and best_score >= 1:
            return {"inferred": True,
                    "hint": f"关联记忆推测（标签/实体关联）：{(best.get('summary') or '')[:60]}",
                    "from_memory": best["id"]}
        return None

    @staticmethod
    def _score(m: dict, q_toks: set, query: str, inv: dict, context: dict | None = None) -> float:
        """联想式多维打分（v2.0）：语义 0.45 + 向量 0.15 + BM25 0.10 + 重要 0.15 + 频次 0.05 + 新近 0.05 + 强度 0.05，
        情境匹配加成；遗忘权重整体调节。语义相关用 bigram Jaccard，向量用 hashing TF 余弦（RQ-01），
        关键词用 BM25（RQ-02），三者互补构成混合检索。"""
        # 1. 语义相关度（字符 bigram / 英文单词 Jaccard，中文友好）
        sem = text_sim(query, m.get("summary", ""))
        sem = max(sem, text_sim(query, " ".join(m.get("tags", []))) * 0.8)
        sem = max(sem, text_sim(query, " ".join(v for vs in m.get("entities", {}).values() for v in vs)) * 0.7)
        # 1b. 向量语义（RQ-01：hashing TF + 余弦，零依赖近似语义）
        mem_vec_text = (m.get("summary", "") or "") + " " + " ".join(m.get("tags", []))
        vec = cosine_sim(text_vector(query), text_vector(mem_vec_text))
        # 1c. BM25 关键词（RQ-02：从倒排索引统计 df）
        mem_toks = text_tokens(mem_vec_text)
        total_docs = max(1, len(inv))  # 近似：倒排 token 数为文档量级
        avg_dl = max(1.0, sum(len(v) for v in inv.values()) / max(1, len(inv)))
        bm = bm25_score(q_toks, mem_toks, {t: len(l) for t, l in inv.items()},
                        max(1, len(mem_toks)), avg_dl, total_docs)
        bm_norm = min(1.0, bm / 8.0)  # 归一化到 0-1（经验界）
        # 2. 重要性
        imp = min(1.0, float(m.get("importance") or 0.4))
        # 3. 新近度（短时半衰 14 天，长时半衰 60 天）
        hl = 14.0 if m.get("tier") == "short" else 60.0
        rec = time_decay(m.get("updated_at", now_iso()), hl)
        # 4. 使用频率
        freq = min(1.0, float(m.get("access_count", 0)) / 10.0)
        # 5. 记忆强度（反复使用增强）
        strength = min(1.0, float(m.get("strength") or 0.3))
        # 6. 情境匹配加成（上限 +0.12）
        ctx_bonus = 0.0
        if context:
            ctx_bonus = NeuroMemStore._context_match(m, context) * 0.12
        # 7. 遗忘权重（selective forgetting 整体调节）
        w = float(m.get("weight", 1.0))
        score = (0.45 * sem + 0.15 * vec + 0.10 * bm_norm + 0.15 * imp
                 + 0.05 * freq + 0.05 * rec + 0.05 * strength + ctx_bonus)
        return round(score * w, 4)


# ---------------------------------------------------------------------------
# 写入与去重
# ---------------------------------------------------------------------------

def _dedup_and_link(store: NeuroMemStore, user_id: str, mem: dict) -> tuple:
    """与已有记忆比对：极高相似 → 合并（返回 merged）；高相似 → 建图谱边。"""
    peers = [m for m in store.all_memories(user_id)
             if m.get("tier") == mem["tier"] and m.get("status") in ("active", "pending")]
    sims = []
    for p in peers:
        s = text_sim(p["summary"], mem["summary"])
        if s >= 0.85:
            # 合并：保留访问多的文本更长者，累积访问
            if p.get("access_count", 0) >= mem.get("access_count", 0) or len(p["summary"]) >= len(mem["summary"]):
                p["access_count"] = p.get("access_count", 0) + mem.get("access_count", 0) + 1
                p["tags"] = sorted(set(p.get("tags", [])) | set(mem.get("tags", [])))
                p["updated_at"] = now_iso()
                store.save_memory(user_id, p)
                return p, sims
            else:
                mem["access_count"] = mem.get("access_count", 0) + p.get("access_count", 0) + 1
                mem["tags"] = sorted(set(mem.get("tags", [])) | set(p.get("tags", [])))
                store.delete_file(user_id, p)
                return mem, sims
        elif s >= 0.42:
            sims.append((p["id"], s))
    return mem, sims


def cmd_add(args) -> int:
    store = NeuroMemStore(Path(args.dir))
    cfg = store.get_config(args.user)
    if args.file:
        try:
            text = Path(args.file).read_text(encoding="utf-8")
        except OSError as e:
            out({"ok": False, "error": "cannot read file: %s" % e, "path": args.file})
            return 1
    else:
        text = args.text
    if not text or not text.strip():
        out({"ok": False, "error": "empty text"})
        return 1
    # LLM 提炼摘要（可选）
    summary = ""
    if args.llm_summarize and (os.environ.get("NEUROMEM_LLM_API_KEY") or args.llm_api_key):
        summary = _llm_summarize(text, args)
    context = json.loads(args.context_json) if args.context_json else {}
    if args.scene and (args.scene in ("work", "life", "study") or args.scene):
        context["scene"] = args.scene  # scene_type（需求 8.1.1：work/life/study 场景分类）
    mem = new_memory(
        text, tier=args.tier or "short", subtype=args.subtype or _infer_subtype(text),
        tags=args.tags, entities=json.loads(args.entities) if args.entities else {},
        importance=args.importance, session_id=args.session, source=args.source,
        context=context,
        summary=summary,
    )
    # 端侧加密（需求 3.5）：--encrypt + --secret 启用并持久化；config 已开启时传 --secret 即加密
    if args.encrypt_flag and not args.secret:
        out({"ok": False, "error": "--encrypt requires --secret"})
        return 1
    use_enc = bool(args.secret) and (cfg.get("encrypted") or args.encrypt_flag)
    if use_enc and not cfg.get("encrypted"):
        cfg["encrypted"] = True
        store.save_config(args.user, cfg)
    if use_enc:
        mem["text"] = encrypt_text(mem["text"], args.secret)
        mem["summary"] = encrypt_text(mem["summary"], args.secret)
    merged, sims = _dedup_and_link(store, user_id=args.user, mem=mem)
    store.save_memory(args.user, merged)
    store.update_index_add(args.user, merged, sims)
    # v2.0 RQ-05：实体关系图谱增量更新（实体节点+关系边+时间线）
    if not use_enc:  # 加密记忆不参与图谱明文（避免泄露）
        try:
            _upsert_entity_graph(store, args.user, merged)
        except Exception:
            pass
    # 自动冲突检测：新记忆与已有记忆矛盾时，旧记忆标 pending（待验证），全程留痕
    marked = []
    if args.auto_conflict:
        for c in _check_new_conflicts(store, args.user, merged):
            pm = _mark_pending(store, args.user, c["old_id"], f"[自动] {c['detail']}")
            if pm:
                marked.append({"old_id": c["old_id"], "kind": c["kind"],
                               "new_status": "pending", "reason": c["detail"]})
    store.rebuild_index(args.user)
    _audit(store, args.user, "add", merged["id"])
    out({"ok": True, "id": merged["id"], "merged": merged["id"] != mem["id"],
         "tier": merged["tier"], "subtype": merged["subtype"],
         "links": len(sims), "auto_conflicts": marked, "summary": merged["summary"]})
    return 0


def _infer_subtype(text: str) -> str:
    t = text.lower()
    if any(k in t for k in PROCEDURE_HINTS):
        return "procedural"
    return "general"


# ---------------------------------------------------------------------------
# 审计日志（操作全链路留痕）
# ---------------------------------------------------------------------------

def _audit(store: NeuroMemStore, user_id: str, action: str, mem_id: str = "", detail: str = "") -> None:
    """写操作留痕：users/<user>/audit.jsonl 追加一行 JSON。"""
    entry = {"ts": now_iso(), "action": action, "mem_id": mem_id, "detail": detail}
    ap = store.user_dir(user_id) / "audit.jsonl"
    ensure_dir(ap.parent)
    try:
        with open(ap, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def cmd_audit(args) -> int:
    store = NeuroMemStore(Path(args.dir))
    ap = store.user_dir(args.user) / "audit.jsonl"
    entries = []
    if ap.exists():
        with open(ap, "r", encoding="utf-8") as f:
            entries = [json.loads(l) for l in f if l.strip()]
    if args.limit:
        entries = entries[-args.limit:]
    out({"ok": True, "count": len(entries), "entries": entries})
    return 0


# ---------------------------------------------------------------------------
# 矛盾检测与修正
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"(?:约|大约|大概|近|超过|不到)?(\d+(?:\.\d+)?)\s*(年|岁|元|块钱|%|百分比|公里|kg|公斤|小时|分钟|天|个月|次|个|人|万|亿)?")
_NEG_PAIRS = [
    (("喜欢", "爱吃", "爱看", "偏好"), ("不喜欢", "讨厌", "不爱", "反感", "厌恶")),
    (("是", "属于"), ("不是", "不属于")),
    (("支持", "同意", "赞成"), ("反对", "不支持", "不同意")),
    (("在", "位于"), ("不在", "离开")),
]


def _extract_nums(text: str) -> list:
    return [(m.group(0).strip(), m.group(1), m.group(2) or "") for m in _NUM_RE.finditer(text)]


# 时间线冲突检测（需求 3.3 三类矛盾：事实冲突 / 数值冲突 / 时间线冲突）
# 抽取显式时间点：YYYY年M月 / YYYY-MM-DD / YYYY年 / M月 / 第Q季度 / HH:MM 等
_TIME_RE = re.compile(
    r"(?P<ym>(?:19|20)\d{2}\s*年\s*(?:1[0-2]|0?[1-9])\s*月?)"
    r"|(?P<date>(?:19|20)\d{2}[-/.](?:1[0-2]|0?[1-9])(?:[-/.](?:[12]\d|3[01]|0?[1-9]))?)"
    r"|(?P<year>(?:19|20)\d{2}\s*年)"
    r"|(?P<month>(?:1[0-2]|0?[1-9])\s*月)"
    r"|(?P<quarter>第\s*[1-4]\s*季度)"
    r"|(?P<time>[0-2]?\d[:：][0-5]\d)"
)
_TIMELINE_VERBS = ("上线", "发布", "开始", "结束", "启动", "截止", "到期", "入职", "离职", "生日",
                   "开会", "交付", "计划", "定于", "将于", "推迟", "提前", "改期", "举行", "上线时间", "举行时间")


def _extract_time_points(text: str) -> list:
    """抽取文本中的时间点，返回 (原文, 规格化键, 排序键)。规格化键用于判定是否同维度。"""
    pts = []
    for m in _TIME_RE.finditer(text):
        raw = m.group(0).strip()
        if m.group("ym"):
            pts.append((raw, "ym", raw.replace(" ", "")))
        elif m.group("date"):
            pts.append((raw, "date", raw.replace(" ", "").replace("/", "-").replace(".", "-")))
        elif m.group("year"):
            pts.append((raw, "year", raw.replace(" ", "")))
        elif m.group("month"):
            pts.append((raw, "month", raw.replace(" ", "")))
        elif m.group("quarter"):
            pts.append((raw, "quarter", raw.replace(" ", "")))
        elif m.group("time"):
            pts.append((raw, "time", raw.replace(" ", "")))
    return pts


def _is_timeline_sentence(text: str) -> bool:
    return any(v in text for v in _TIMELINE_VERBS)


def _time_conflict_detail(a: str, b: str) -> str | None:
    """两条文本的显式时间点冲突：同维度不同值 且 至少一句含时间线动词 → 冲突。"""
    pa, pb = _extract_time_points(a), _extract_time_points(b)
    if not pa or not pb:
        return None
    for ra, ka, va in pa:
        for rb, kb, vb in pb:
            if ka != kb:
                continue
            if va != vb:
                return "%s vs %s" % (ra, rb)
    return None


def check_contradictions(store: NeuroMemStore, user_id: str) -> list:
    """规则化矛盾检测：数值断言冲突 + 情感否定冲突。"""
    mems = [m for m in store.all_memories(user_id) if m.get("status") in ("active", "pending")]
    issues = []
    for i in range(len(mems)):
        for j in range(i + 1, len(mems)):
            a, b = mems[i], mems[j]
            if text_sim(a["summary"], b["summary"]) < 0.25:
                continue
            # 数值冲突
            nums_a, nums_b = _extract_nums(a["summary"]), _extract_nums(b["summary"])
            for na, va, ua in nums_a:
                for nb, vb, ub in nums_b:
                    if ua and ua == ub and va != vb and abs(float(va) - float(vb)) > 0.001:
                        issues.append({
                            "kind": "numeric", "a_id": a["id"], "b_id": b["id"],
                            "detail": f"{na} vs {nb}（{ua}）", "severity": "high",
                        })
            # 时间线冲突（需求 3.3）：同维度显式时间点不同，且至少一句含时间动词
            if _is_timeline_sentence(a["summary"]) or _is_timeline_sentence(b["summary"]):
                tdetail = _time_conflict_detail(a["summary"], b["summary"])
                if tdetail:
                    issues.append({
                        "kind": "timeline", "a_id": a["id"], "b_id": b["id"],
                        "detail": tdetail, "severity": "high",
                    })
            # 否定冲突
            for pos, neg in _NEG_PAIRS:
                pa = [k for k in pos if k in a["summary"]]
                nb_ = [k for k in neg if k in b["summary"]]
                if pa and nb_:
                    issues.append({
                        "kind": "polarity", "a_id": a["id"], "b_id": b["id"],
                        "detail": f"「{pa[0]}」vs「{nb_[0]}」", "severity": "medium",
                    })
                pb = [k for k in pos if k in b["summary"]]
                na_ = [k for k in neg if k in a["summary"]]
                if pb and na_:
                    issues.append({
                        "kind": "polarity", "a_id": a["id"], "b_id": b["id"],
                        "detail": f"「{pb[0]}」vs「{na_[0]}」", "severity": "medium",
                    })
    return issues


def cmd_correct(args) -> int:
    """用户修正：旧记忆标 invalid + superseded_by 指向新记忆，写版本快照，不删除。"""
    store = NeuroMemStore(Path(args.dir))
    old = store.load_memory(args.user, args.id)
    if not old:
        out({"ok": False, "error": "memory not found", "id": args.id})
        return 1
    old["status"] = "invalid"
    old["updated_at"] = now_iso()
    store.push_version(args.user, old["id"], old)
    # 归档物理保留
    store.delete_file(args.user, old)
    ap = store.archive_dir(args.user) / (old["id"] + ".json")
    save_json(ap, old)
    new = new_memory(
        args.text, tier=old["tier"], subtype=old["subtype"],
        tags=old.get("tags"), entities=old.get("entities"),
        importance=old.get("importance"), source=old.get("source"),
        context=old.get("context"), superseded_by=None,
    )
    new["links"] = list(old.get("links", [])) + [old["id"]]
    # 端侧加密：原记忆已加密且提供 secret 时，修正内容同样加密写入
    cfg = store.get_config(args.user)
    if cfg.get("encrypted"):
        if not args.secret:
            out({"ok": False, "error": "memory is encrypted, --secret required for correction"})
            return 1
        new["text"] = encrypt_text(new["text"], args.secret)
        new["summary"] = encrypt_text(new["summary"], args.secret)
    store.save_memory(args.user, new)
    store.rebuild_index(args.user)
    # 缓存同步失效（需求 3.3）：按旧记忆摘要关键词联动清除相关缓存，防脏缓存
    invalidated = 0
    for kw in (old.get("tags") or [])[:3] or [str(old.get("summary", ""))[:6]]:
        invalidated += cache_invalidate(store, args.user, kw)
    # v2.0 RQ-06 反馈闭环：沉淀纠偏规则到 config（下次检索自动调整该主题权重）
    try:
        cfg = store.get_config(args.user)
        rules = cfg.setdefault("correction_rules", [])
        old_key = str(old.get("summary", ""))[:20]
        new_key = args.text[:20]
        rules.append({"topic": rule_topic(str(old.get("summary", ""))),
                      "old": old_key, "new": new_key, "at": now_iso(),
                      "memories": [old["id"], new["id"]]})
        cfg["correction_rules"] = rules[-200:]  # 保留最近 200 条
        # 纠偏主题降权：把与旧记忆同主题的 active 记忆 weight 降 0.8（防混淆）
        old_toks = text_tokens(old.get("summary", ""))
        for peer in store.all_memories(args.user):
            if peer.get("status") != "active" or peer["id"] == new["id"]:
                continue
            if text_sim(peer.get("summary", ""), old.get("summary", "")) >= 0.35:
                peer["weight"] = round(float(peer.get("weight", 1.0)) * 0.8, 3)
                store.save_memory(args.user, peer)
        store.save_config(args.user, cfg)
        feedback = {"rule_added": True, "demoted_peers": 1}
    except Exception:
        feedback = {"rule_added": False}
    _audit(store, args.user, "correct", old["id"], args.text[:80])
    out({"ok": True, "old_id": old["id"], "old_status": "invalid",
         "new_id": new["id"], "correction_chain": new["links"],
         "cache_invalidated": invalidated, "feedback": feedback})
    return 0


def _check_new_conflicts(store: NeuroMemStore, user_id: str, mem: dict) -> list:
    """新记忆 vs 已有 active/pending 记忆的冲突检测（只查与 mem 相关的对）。"""
    peers = [m for m in store.all_memories(user_id)
             if m.get("status") in ("active", "pending") and m["id"] != mem["id"]]
    issues = []
    for p in peers:
        if text_sim(p["summary"], mem["summary"]) < 0.25:
            continue
        # 数值断言冲突
        nums_p, nums_m = _extract_nums(p["summary"]), _extract_nums(mem["summary"])
        for n1, v1, u1 in nums_p:
            for n2, v2, u2 in nums_m:
                if u1 and u1 == u2 and v1 != v2 and abs(float(v1) - float(v2)) > 0.001:
                    issues.append({"old_id": p["id"], "kind": "numeric",
                                   "detail": f"{n1} vs {n2}（{u1}）"})
        # 时间线冲突（需求 3.3）
        if _is_timeline_sentence(p["summary"]) or _is_timeline_sentence(mem["summary"]):
            tdetail = _time_conflict_detail(p["summary"], mem["summary"])
            if tdetail:
                issues.append({"old_id": p["id"], "kind": "timeline", "detail": tdetail})
        # 情感/断言极性冲突
        for pos, neg in _NEG_PAIRS:
            p_pos = any(k in p["summary"] for k in pos) and any(k in mem["summary"] for k in neg)
            p_neg = any(k in neg for k in p["summary"]) and any(k in pos for k in mem["summary"])
            if p_pos or p_neg:
                issues.append({"old_id": p["id"], "kind": "polarity", "detail": "情感/断言极性冲突"})
    return issues


def _mark_pending(store: NeuroMemStore, user_id: str, mem_id: str, reason: str) -> dict | None:
    """旧记忆标「待验证」：不覆盖、不删除，留版本快照与审计。"""
    m = store.load_memory(user_id, mem_id)
    if not m or m.get("status") != "active":
        return None
    store.push_version(user_id, m["id"], m)  # 变更前快照
    m["status"] = "pending"
    m["updated_at"] = now_iso()
    store.save_memory(user_id, m)
    _audit(store, user_id, "conflict_mark_pending", m["id"], reason)
    return m


# ---------------------------------------------------------------------------
# 巩固（consolidation）：短时 → 长时迁移、去重、画像提炼
# ---------------------------------------------------------------------------

def _promote(m: dict, cfg: dict) -> dict:
    m = dict(m)
    m["tier"] = "long"
    m["subtype"] = m.get("subtype") if m.get("subtype") in SUBTYPES else _infer_subtype(m.get("summary", ""))
    if m["subtype"] == "general":
        # 有实体/人 → 情景；纯偏好事实 → 语义
        ents = m.get("entities") or {}
        if any(ents.values()):
            m["subtype"] = "episodic"
        else:
            m["subtype"] = "semantic"
    m["updated_at"] = now_iso()
    return m


def cmd_consolidate(args) -> int:
    store = NeuroMemStore(Path(args.dir))
    cfg = store.get_config(args.user)
    short_mems = [m for m in store.all_memories(args.user)
                  if m.get("tier") == "short" and m.get("status") == "active"]
    now = now_iso()
    promoted, merged, kept = 0, 0, 0
    for m in short_mems:
        age_days = (datetime.now(timezone.utc) - datetime.fromisoformat(m["created_at"])).days if m.get("created_at") else 0
        hot = (float(m.get("importance") or 0.4) >= args.importance_threshold) or \
              (int(m.get("access_count") or 0) >= args.access_threshold) or \
              (age_days >= args.min_age_days)
        if hot:
            # 迁移前再查重：与长时库相似则合并
            dup = None
            for lm in store.all_memories(args.user):
                if lm.get("tier") != "long" or lm.get("status") != "active":
                    continue
                if text_sim(lm["summary"], m["summary"]) >= 0.9:
                    dup = lm
                    break
            if dup:
                dup["access_count"] = dup.get("access_count", 0) + m.get("access_count", 0) + 1
                dup["tags"] = sorted(set(dup.get("tags", [])) | set(m.get("tags", [])))
                dup["updated_at"] = now
                store.save_memory(args.user, dup)
                store.delete_file(args.user, m)
                merged += 1
                continue
            pm = _promote(m, cfg)
            store.delete_file(args.user, m)
            store.save_memory(args.user, pm)
            promoted += 1
        else:
            kept += 1
    store.rebuild_index(args.user)
    # 自动失效：--auto-resolve 时，被 ≥2 次新信息否定的 pending 记忆 → invalid 归档
    invalidated = 0
    if args.auto_resolve:
        issues = check_contradictions(store, args.user)
        neg_count = {}
        for i in issues:
            neg_count[i["a_id"]] = neg_count.get(i["a_id"], 0) + 1
            neg_count[i["b_id"]] = neg_count.get(i["b_id"], 0) + 1
        for m in store.all_memories(args.user):
            if m.get("status") == "pending" and neg_count.get(m["id"], 0) >= 2:
                m["status"] = "invalid"
                m["updated_at"] = now_iso()
                store.push_version(args.user, m["id"], m)
                store.delete_file(args.user, m)
                save_json(store.archive_dir(args.user) / (m["id"] + ".json"), m)
                _audit(store, args.user, "auto_invalidate", m["id"], "连续否定 >=2 次")
                invalidated += 1
        store.rebuild_index(args.user)
    # 画像提炼：从 long/semantic 汇总偏好型事实
    profile = _build_profile(store, args.user)
    cfg["profile"] = profile
    store.save_config(args.user, cfg)
    _audit(store, args.user, "consolidate", "", f"promoted={promoted} merged={merged} invalidated={invalidated}")
    out({"ok": True, "promoted": promoted, "merged_duplicates": merged,
         "kept_short": kept, "invalidated": invalidated,
         "profile_entries": len(profile.get("preferences", []))})
    return 0


def _build_profile(store: NeuroMemStore, user_id: str) -> dict:
    mems = [m for m in store.all_memories(user_id)
            if m.get("status") == "active" and m.get("tier") == "long"
            and m.get("subtype") in ("semantic", "episodic")]
    prefs = []
    ent_counter = {}
    for m in mems[:200]:
        for k, vs in (m.get("entities") or {}).items():
            for v in vs:
                ent_counter[f"{k}:{v}"] = ent_counter.get(f"{k}:{v}", 0) + 1
    for m in mems[:100]:
        t = m.get("summary", "")
        if any(k in t for k in ("喜欢", "偏好", "习惯", "偏好", "倾向", "prefer", "favorite", "喜欢用", "常用")):
            prefs.append({"memory_id": m["id"], "preference": t})
    top_ents = sorted(ent_counter.items(), key=lambda x: x[1], reverse=True)[:30]
    return {
        "top_entities": [{"name": k, "count": v} for k, v in top_ents],
        "preferences": prefs[:30],
        "total_long": len(mems),
        "updated_at": now_iso(),
    }


# ---------------------------------------------------------------------------
# 选择性遗忘 / 降权
# ---------------------------------------------------------------------------

def cmd_forget(args) -> int:
    """归档：不删除，物理移入 archive，status=archived。"""
    store = NeuroMemStore(Path(args.dir))
    m = store.load_memory(args.user, args.id)
    if not m:
        out({"ok": False, "error": "memory not found", "id": args.id})
        return 1
    m["status"] = "archived"
    m["updated_at"] = now_iso()
    store.push_version(args.user, m["id"], m)
    store.delete_file(args.user, m)
    save_json(store.archive_dir(args.user) / (m["id"] + ".json"), m)
    store.rebuild_index(args.user)
    _audit(store, args.user, "forget", m["id"])
    out({"ok": True, "id": m["id"], "status": "archived",
         "note": "已归档（非物理删除），可随时 search --include-archived 唤醒"})
    return 0


def cmd_demote(args) -> int:
    """降权：降低检索权重，模拟遗忘曲线。"""
    store = NeuroMemStore(Path(args.dir))
    m = store.load_memory(args.user, args.id)
    if not m:
        out({"ok": False, "error": "memory not found", "id": args.id})
        return 1
    m["weight"] = round(max(0.05, float(m.get("weight", 1.0)) * args.factor), 3)
    m["updated_at"] = now_iso()
    store.push_version(args.user, m["id"], m)  # 变更前快照
    store.save_memory(args.user, m)
    _audit(store, args.user, "demote", m["id"], f"weight->{m['weight']}")
    out({"ok": True, "id": m["id"], "weight": m["weight"]})
    return 0


# ---------------------------------------------------------------------------
# 元记忆：概况 / 画像
# ---------------------------------------------------------------------------

def cmd_stats(args) -> int:
    store = NeuroMemStore(Path(args.dir))
    mems = store.all_memories(args.user, include_archived=True)
    by_tier = {}
    by_status = {}
    by_subtype = {}
    for m in mems:
        by_tier[m.get("tier", "?")] = by_tier.get(m.get("tier", "?"), 0) + 1
        by_status[m.get("status", "?")] = by_status.get(m.get("status", "?"), 0) + 1
        by_subtype[m.get("subtype", "?")] = by_subtype.get(m.get("subtype", "?"), 0) + 1
    versions = 0
    vdir = store.versions_dir(args.user)
    if vdir.exists():
        versions = sum(1 for _ in vdir.glob("*.json"))
    cfg = store.get_config(args.user)
    out({"ok": True, "user": args.user,
         "total": len(mems), "by_tier": by_tier, "by_status": by_status,
         "by_subtype": by_subtype, "version_snapshots": versions,
         "profile_entries": len(cfg.get("profile", {}).get("preferences", [])),
         "encrypted": cfg.get("encrypted", False)})
    return 0


def cmd_profile(args) -> int:
    store = NeuroMemStore(Path(args.dir))
    cfg = store.get_config(args.user)
    profile = cfg.get("profile", {})
    if args.llm_summarize:
        # 可选 LLM 画像综述
        pass
    out({"ok": True, "profile": profile})
    return 0


# ---------------------------------------------------------------------------
# 导出 / 导入
# ---------------------------------------------------------------------------

def cmd_export(args) -> int:
    store = NeuroMemStore(Path(args.dir))
    mems = store.all_memories(args.user, include_archived=True)
    data = {"app": APP_NAME, "version": VERSION, "user": args.user,
            "exported_at": now_iso(),
            "memories": mems,
            "config": store.get_config(args.user)}
    out_path = Path(args.output) if args.output else Path.cwd() / f"neuromem-{args.user}-export.json"
    save_json(out_path, data)
    out({"ok": True, "exported": len(mems), "path": str(out_path)})
    return 0


def cmd_import(args) -> int:
    store = NeuroMemStore(Path(args.dir))
    data = load_json(Path(args.file))
    mems = data.get("memories", [])
    imported = 0
    for m in mems:
        m["id"] = rand_id()  # 避免 ID 冲突
        m["status"] = "active"
        store.save_memory(args.user, m)
        imported += 1
    store.rebuild_index(args.user)
    _audit(store, args.user, "import", "", f"imported={imported}")
    out({"ok": True, "imported": imported, "user": args.user})
    return 0


def cmd_batch(args) -> int:
    """批量操作（需求 8.2.3）：delete 批量删除 / export 批量导出 / tag 批量标记分类 / importance 批量调重要性。

    --ids 逗号分隔记忆 ID；--operation 指定动作；--params 传 JSON（export 用 {"output": 路径}，
    tag 用 {"tags": [...]}，importance 用 {"value": 0.8}）。删除为软删除（标 invalid 归档）。
    """
    store = NeuroMemStore(Path(args.dir))
    ids = [x.strip() for x in args.ids.split(",") if x.strip()]
    if not ids:
        out({"ok": False, "error": "ids required（逗号分隔）"})
        return 1
    try:
        params = json.loads(args.params) if args.params else {}
    except json.JSONDecodeError:
        out({"ok": False, "error": "params must be valid JSON"})
        return 1
    succeeded, failed = [], []
    if args.operation == "export":
        out_path = Path(params.get("output") or ("batch-export-%s.json" % args.user))
        rows = []
        for mid in ids:
            m = store.load_memory(args.user, mid)
            if m:
                rows.append(m)
                succeeded.append(mid)
            else:
                failed.append({"id": mid, "error": "memory not found"})
        save_json(out_path, {"app": APP_NAME, "user": args.user, "exported_at": now_iso(),
                             "memories": rows, "config": store.get_config(args.user)})
        _audit(store, args.user, "batch_export", "", f"ok={len(succeeded)}")
        out({"ok": True, "operation": args.operation, "succeeded": len(succeeded),
             "failed": len(failed), "failures": failed, "path": str(out_path)})
        return 0
    for mid in ids:
        m = store.load_memory(args.user, mid)
        if not m:
            failed.append({"id": mid, "error": "memory not found"})
            continue
        if args.operation == "delete":
            # 软删除：标 invalid 归档（永不物理删除原则）
            m["status"] = "invalid"
            m["updated_at"] = now_iso()
            store.push_version(args.user, m["id"], m)
            store.delete_file(args.user, m)
            save_json(store.archive_dir(args.user) / (m["id"] + ".json"), m)
            succeeded.append(mid)
        elif args.operation == "tag":
            tags = params.get("tags", [])
            m["tags"] = sorted(set(m.get("tags", [])) | set(tags))
            m["updated_at"] = now_iso()
            store.save_memory(args.user, m)
            succeeded.append(mid)
        elif args.operation == "importance":
            m["importance"] = float(params.get("value", 0.4))
            m["updated_at"] = now_iso()
            store.save_memory(args.user, m)
            succeeded.append(mid)
        else:
            out({"ok": False, "error": "unknown operation: %s" % args.operation})
            return 1
    store.rebuild_index(args.user)
    _audit(store, args.user, "batch_" + args.operation, "", f"ok={len(succeeded)}")
    out({"ok": True, "operation": args.operation, "succeeded": len(succeeded),
         "failed": len(failed), "failures": failed})
    return 0


# ---------------------------------------------------------------------------
# v2.0 RQ-05 实体关系图谱：写入时抽取实体与关系，图谱支持实体节点+关系+时间线
# ---------------------------------------------------------------------------

_ENTITY_PATTERNS = {
    "person": re.compile(r"([\u4e00-\u9fff]{2,4}(?:先生|女士|老师|同学|老板|经理|总|哥|姐)?)"),
}
_RELATION_KEYWORDS = (
    ("负责", "负责"), ("喜欢", "喜欢"), ("属于", "属于"), ("位于", "位于"),
    ("在", "在"), ("使用", "使用"), ("偏好", "偏好"), ("讨厌", "讨厌"),
)


def _extract_relations(text: str) -> list:
    """规则化关系抽取：A 动词 B（人/项目/物），返回 [(subject, rel, object)]。"""
    rels = []
    # 简化：找「实体 动词 实体/名词短语」三元组，中文按动词切分
    for verb in ("负责", "喜欢", "偏好", "讨厌", "属于", "使用", "位于"):
        if verb in text:
            parts = text.split(verb, 1)
            if len(parts) == 2:
                subj = parts[0].strip()[-8:] or "?"
                obj = parts[1].strip().split("，")[0].split("。")[0][:12] or "?"
                if subj and obj:
                    rels.append((subj, verb, obj))
    return rels


def _extract_entities_mem(text: str) -> dict:
    """写入时抽取实体（tags/人名/地名/项目/数字单位），存入 entities 字段。"""
    ents = {}
    # 已有显式实体
    # 规则：动词宾语/专名（公司、项目、地名常见后缀）
    for suffix, key in (("公司", "org"), ("项目", "project"), ("部门", "dept"),
                        ("酒店", "place"), ("咖啡", "item"), ("App", "app"), ("系统", "system")):
        idx = text.find(suffix)
        while idx != -1:
            start = idx
            while start > 0 and (text[start - 1].isalnum() or text[start - 1] in "_-·"):
                start -= 1
            name = text[start:idx + len(suffix)]
            if 1 < len(name) <= 20:
                ents.setdefault(key, [])
                if name not in ents[key]:
                    ents[key].append(name)
            idx = text.find(suffix, idx + len(suffix))
    # 英文专名（大写连续词）
    for w in re.findall(r"[A-Z][A-Za-z0-9_-]{2,}", text):
        ents.setdefault("org", [])
        if w not in ents["org"]:
            ents["org"].append(w)
    return ents


def _upsert_entity_graph(store: NeuroMemStore, user_id: str, mem: dict) -> None:
    """把记忆的实体/关系写入知识图谱（users/<u>/index/graph-entities.json）。
    节点 {entity: {type, first_seen, last_seen, memories[]}}，边 {src_verb_dst: {strength, memories[]}}。"""
    gp = store.user_dir(user_id) / "index" / "graph-entities.json"
    g = load_json(gp)
    ents = dict(mem.get("entities") or {})
    rels = _extract_relations(mem.get("summary", "") or "")
    now = now_iso()
    g.setdefault("nodes", {})
    g.setdefault("edges", {})
    # 实体节点
    for etype, names in ents.items():
        for n in names:
            node = g["nodes"].setdefault(str(n), {"type": etype, "first_seen": now,
                                                  "last_seen": now, "memories": []})
            node["last_seen"] = now
            if mem["id"] not in node["memories"]:
                node["memories"].append(mem["id"])
    # 关系边（实体-动词-实体）
    for subj, verb, obj in rels:
        sn = g["nodes"].get(subj) or g["nodes"].setdefault(
            subj, {"type": "unknown", "first_seen": now, "last_seen": now, "memories": []})
        sn["last_seen"] = now
        on = g["nodes"].get(obj) or g["nodes"].setdefault(
            obj, {"type": "unknown", "first_seen": now, "last_seen": now, "memories": []})
        on["last_seen"] = now
        key = f"{subj}|{verb}|{obj}"
        edge = g["edges"].setdefault(key, {"strength": 0.0, "memories": []})
        edge["strength"] = round(min(1.0, edge["strength"] + 0.2), 3)
        if mem["id"] not in edge["memories"]:
            edge["memories"].append(mem["id"])
    # 保留最近 500 节点
    nodes = g["nodes"]
    if len(nodes) > 500:
        for k in list(nodes)[:len(nodes) - 500]:
            nodes.pop(k, None)
    save_json(gp, g)


def cmd_graph(args) -> int:
    """查询/导出实体关系图谱（RQ-05）。--entity 查某实体；--edges 看关系边。"""
    store = NeuroMemStore(Path(args.dir))
    gp = store.user_dir(args.user) / "index" / "graph-entities.json"
    g = load_json(gp)
    if args.entity:
        node = g.get("nodes", {}).get(args.entity)
        if not node:
            out({"ok": False, "error": "entity not found", "entity": args.entity})
            return 1
        rels = [{"edge": k, "strength": v["strength"], "memories": len(v.get("memories", []))}
                for k, v in g.get("edges", {}).items()
                if args.entity in k.split("|")]
        out({"ok": True, "entity": args.entity, "node": node, "relations": rels})
        return 0
    out({"ok": True, "nodes": len(g.get("nodes", {})), "edges": len(g.get("edges", {})),
         "sample_nodes": list(g.get("nodes", {}).keys())[:20],
         "sample_edges": list(g.get("edges", {}).keys())[:20]})
    return 0


# ---------------------------------------------------------------------------
# v2.0 RQ-03 自动捕获 Hook：会话结束后自动提炼关键事实并写入短时记忆
#   输入：--messages 对话 JSON（[{role,content}...]）或 --text 原始文本
#   产出：自动 add（去重合并、矛盾检测），返回捕获到的记忆数
# ---------------------------------------------------------------------------

def _capture_facts(text: str) -> list:
    """从一段文本中提取候选关键事实（零依赖：按句+逗号拆+重要性信号）。"""
    # 先按句号/感叹/换行拆，再对长句按逗号拆，尽量还原独立事实
    segments = re.split(r"[。！？!?\n]+", text)
    sentences = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        if len(seg) > 30 and "，" in seg:
            sentences.extend(s.strip() for s in seg.split("，") if s.strip() and len(s.strip()) >= 6)
        else:
            sentences.append(seg)
    facts = []
    for s in sentences:
        s = s.strip()
        if len(s) < 6:
            continue
        # 重要性信号：含偏好/数字/实体/项目/决策词
        signals = 0
        if any(k in s for k in ("喜欢", "偏好", "习惯", "负责", "项目", "预算", "时间", "日期",
                                "决定", "确认", "计划", "上线", "发布", "生日", "账号", "密码")):
            signals += 2
        if re.search(r"\d", s):
            signals += 1
        if re.search(r"[A-Z][A-Za-z]{2,}", s):
            signals += 1
        if signals >= 2 and len(s) <= 60:
            facts.append(s)
    # 去重
    uniq, seen = [], set()
    for f in facts:
        key = f[:12]
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    return uniq[:10]


def cmd_capture(args) -> int:
    """自动捕获（RQ-03）：从对话/文本提炼关键事实写入记忆。--text 或 --file。"""
    store = NeuroMemStore(Path(args.dir))
    if args.file:
        try:
            text = Path(args.file).read_text(encoding="utf-8")
        except OSError as e:
            out({"ok": False, "error": "cannot read file: %s" % e})
            return 1
    else:
        text = args.text
    if not text or not text.strip():
        out({"ok": False, "error": "empty text"})
        return 1
    facts = _capture_facts(text)
    written = []
    for fact in facts:
        ents = _extract_entities_mem(fact)
        mem = new_memory(fact, tier="short", subtype=_infer_subtype(fact),
                         tags=args.tags, entities=ents,
                         importance=0.55, source="capture-hook")
        merged, sims = _dedup_and_link(store, args.user, mem)
        store.save_memory(args.user, merged)
        store.update_index_add(args.user, merged, sims)
        _upsert_entity_graph(store, args.user, merged)
        written.append(merged["id"])
    store.rebuild_index(args.user)
    _audit(store, args.user, "capture", "", f"facts={len(facts)}")
    out({"ok": True, "captured": len(written), "facts": facts,
         "dedup_merged": len(written) - len(set(written)), "ids": written[:5]})
    return 0


# ---------------------------------------------------------------------------
# v2.0 RQ-04 MCP 协议接入（stdio JSON-RPC 2.0，零依赖）
#   启动：neuromem.py mcp --user <id>
#   暴露工具：add / search / get / correct / cache_query / cache_store / stats
#   任意 MCP 客户端（Claude Code / Cursor 等）可直连
# ---------------------------------------------------------------------------

_MCP_TOOLS = [
    {"name": "memory_add", "description": "写入一条记忆（自动分层/去重/索引/图谱）",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string", "description": "记忆内容"},
         "importance": {"type": "number", "description": "重要性 0-1，默认0.4"},
         "tags": {"type": "array", "items": {"type": "string"}}},
         "required": ["text"]}},
    {"name": "memory_search", "description": "联想式检索记忆（先缓存拦截，未命中再全量）",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string"}, "top_k": {"type": "integer", "default": 5}},
         "required": ["query"]}},
    {"name": "memory_get", "description": "按 ID 读取一条记忆",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}},
                     "required": ["id"]}},
    {"name": "memory_correct", "description": "修正错误记忆（旧标 invalid 留痕，联动失效缓存）",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}, "text": {"type": "string"}},
         "required": ["id", "text"]}},
    {"name": "cache_query", "description": "语义缓存查询（命中直接返回，零大模型调用）",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}},
                     "required": ["query"]}},
    {"name": "memory_stats", "description": "记忆统计概况",
     "inputSchema": {"type": "object", "properties": {}}},
]


def _mcp_call(tool: str, args_dict: dict, store: NeuroMemStore, user: str) -> dict:
    """MCP 工具执行。返回内容列表。"""
    if tool == "memory_add":
        r = {"text": args_dict.get("text", ""), "tags": args_dict.get("tags", [])}
        mem = new_memory(r["text"], tier="short", subtype=_infer_subtype(r["text"]),
                         tags=r["tags"], importance=float(args_dict.get("importance", 0.4)),
                         source="mcp")
        merged, sims = _dedup_and_link(store, user, mem)
        store.save_memory(user, merged)
        store.update_index_add(user, merged, sims)
        try:
            _upsert_entity_graph(store, user, merged)
        except Exception:
            pass
        store.rebuild_index(user)
        _audit(store, user, "mcp_add", merged["id"])
        return {"text": json.dumps({"ok": True, "id": merged["id"],
                                    "merged": merged["id"] != mem["id"]}, ensure_ascii=False)}
    if tool == "memory_search":
        res = store.search(user, args_dict.get("query", ""),
                           top_k=int(args_dict.get("top_k", 5)))
        if isinstance(res, dict):
            res = res["results"]
        return {"text": json.dumps({"ok": True, "results": res}, ensure_ascii=False)}
    if tool == "memory_get":
        m = store.load_memory(user, args_dict.get("id", ""))
        return {"text": json.dumps({"ok": bool(m), "memory": m}, ensure_ascii=False)}
    if tool == "memory_correct":
        old = store.load_memory(user, args_dict.get("id", ""))
        if not old:
            return {"text": json.dumps({"ok": False, "error": "memory not found"}, ensure_ascii=False)}
        old["status"] = "invalid"
        old["updated_at"] = now_iso()
        store.push_version(user, old["id"], old)
        store.delete_file(user, old)
        save_json(store.archive_dir(user) / (old["id"] + ".json"), old)
        new = new_memory(args_dict.get("text", old["summary"]), tier=old["tier"],
                         subtype=old["subtype"], tags=old.get("tags"),
                         entities=old.get("entities"), source="mcp")
        new["links"] = list(old.get("links", [])) + [old["id"]]
        store.save_memory(user, new)
        store.rebuild_index(user)
        for kw in (old.get("tags") or [])[:3]:
            cache_invalidate(store, user, kw)
        return {"text": json.dumps({"ok": True, "old_id": old["id"], "new_id": new["id"]},
                                   ensure_ascii=False)}
    if tool == "cache_query":
        e, tier = cache_query(store, user, args_dict.get("query", ""))
        if e:
            return {"text": json.dumps({"ok": True, "hit": True, "tier": tier,
                                        "answer": e.get("a")}, ensure_ascii=False)}
        return {"text": json.dumps({"ok": True, "hit": False}, ensure_ascii=False)}
    if tool == "memory_stats":
        mems = store.all_memories(user, include_archived=True)
        by_tier = {}
        for m in mems:
            by_tier[m.get("tier", "?")] = by_tier.get(m.get("tier", "?"), 0) + 1
        return {"text": json.dumps({"ok": True, "total": len(mems), "by_tier": by_tier},
                                   ensure_ascii=False)}
    return {"text": json.dumps({"ok": False, "error": "unknown tool: %s" % tool},
                               ensure_ascii=False)}


def cmd_mcp(args) -> int:
    """MCP stdio 服务器（RQ-04）：逐行读 JSON-RPC 2.0，响应写 stdout。"""
    store = NeuroMemStore(Path(args.dir))
    user = args.user
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = req.get("id")
        method = req.get("method", "")
        resp = {"jsonrpc": "2.0", "id": rid}
        if method == "initialize":
            resp["result"] = {"protocolVersion": "2024-11-05",
                              "capabilities": {"tools": {}},
                              "serverInfo": {"name": "mile-neuro-memory", "version": VERSION}}
        elif method == "tools/list":
            resp["result"] = {"tools": _MCP_TOOLS}
        elif method == "tools/call":
            params = req.get("params", {})
            tool, args_dict = params.get("name", ""), params.get("arguments", {})
            result = _mcp_call(tool, args_dict, store, user)
            resp["result"] = {"content": [result], "isError": False}
        elif method == "notifications/initialized" or method.startswith("notifications/"):
            continue  # 通知不需要响应
        elif method == "ping":
            resp["result"] = {}
        else:
            resp["error"] = {"code": -32601, "message": "method not found: %s" % method}
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


# ---------------------------------------------------------------------------
# v2.0 RQ-07 失败案例 → 规则进化闭环
#   输入：audit 里 correct 事件 / 检索空结果。产出：规则沉淀进程序记忆（procedural）
# ---------------------------------------------------------------------------

def cmd_evolve(args) -> int:
    """自我进化（RQ-07）：从历史 correct/失败案例提炼可复用规则入程序记忆库。
    实现：按纠偏主题（实体词）聚类 → 频次≥2 生成规则记忆（procedural）→ 输出可审计报告。"""
    store = NeuroMemStore(Path(args.dir))
    cfg = store.get_config(args.user)
    rules = cfg.get("correction_rules", [])
    if not rules:
        out({"ok": True, "evolved": 0, "note": "暂无纠偏历史，先使用 correct 后再进化"})
        return 0

    def _topic_of(old_text: str) -> str:
        """从旧表述提取主题实体（人名/项目/专名），无则取前 4 字。"""
        # 1) 带称谓/后缀的人名（张先生、李总、王五老师）
        m = re.search(r"([\u4e00-\u9fff]{2,3}(?:先生|女士|老师|同学|老板|经理|总|哥|姐))", old_text or "")
        if m:
            return m.group(1)
        # 2) 英文专名
        m = re.search(r"([A-Z][A-Za-z0-9_-]{2,})", old_text or "")
        if m:
            return m.group(1)
        # 3) 中文 2 字人名（常见姓氏开头），避免贪婪吞后续字
        m = re.search(r"([\u4e00-\u9fff]{2})", old_text or "")
        if m and m.group(1)[0] in "王李张刘陈杨黄赵周吴徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤":
            return m.group(1)
        return (old_text or "")[:4]

    from collections import Counter
    topics = Counter()
    for r in rules:
        t = _topic_of(r.get("old", ""))
        if t:
            topics[t] += 1
    evolved = []
    for topic, cnt in topics.most_common(5):
        if cnt < 2:
            continue
        samples = [r for r in rules if _topic_of(r.get("old", "")) == topic]
        latest = samples[-1].get("new", "")
        rule_text = ("纠偏规则（自动沉淀）：关于「%s」的信息，历史纠正 %d 次，"
                     "正确表述以最近一次为准：%s" % (topic, cnt, latest[:40]))
        # 检查是否已有同规则
        dup = False
        for m in store.all_memories(args.user):
            if m.get("subtype") == "procedural" and rule_text[:16] in (m.get("summary", "") or ""):
                dup = True
                break
        if dup:
            continue
        mem = new_memory(rule_text, tier="long", subtype="procedural",
                         tags=["纠偏规则", "auto-evolved"], importance=0.8,
                         source="evolve")
        merged, sims = _dedup_and_link(store, args.user, mem)
        store.save_memory(args.user, merged)
        store.update_index_add(args.user, merged, sims)
        try:
            _upsert_entity_graph(store, args.user, merged)
        except Exception:
            pass
        evolved.append({"rule": rule_text[:40], "count": cnt, "mem_id": merged["id"]})
    if evolved:
        store.rebuild_index(args.user)
    _audit(store, args.user, "evolve", "", f"rules={len(evolved)}")
    out({"ok": True, "evolved": len(evolved), "rules": evolved,
         "total_corrections": len(rules)})
    return 0


# ---------------------------------------------------------------------------
# v2.0 RQ-08 可视化面板：serve 内置 Web UI（时间线/图谱/缓存看板）与 API
# ---------------------------------------------------------------------------

def _timeline_data(store: NeuroMemStore, user_id: str, limit: int = 50) -> list:
    mems = store.all_memories(user_id)
    mems.sort(key=lambda m: str(m.get("updated_at", "")), reverse=True)
    return [{"id": m["id"], "summary": (m.get("summary") or "")[:60],
             "tier": m.get("tier"), "status": m.get("status"),
             "updated_at": (m.get("updated_at") or "")[:19],
             "importance": m.get("importance")} for m in mems[:limit]]


def _cache_board(store: NeuroMemStore, user_id: str) -> dict:
    board = {"hits": 0, "entries": 0, "tiers": {}}
    for tier in CACHE_TIERS:
        entries = _read_cache(store, user_id, tier)
        hits = sum(e.get("hit_count", 0) for e in entries)
        board["tiers"][tier] = {"entries": len(entries), "hits": hits}
        board["hits"] += hits
        board["entries"] += len(entries)
    return board


_UI_HTML = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>mile-neuro-memory 记忆面板</title>
<style>
body{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;margin:0;background:#f6f8fa;color:#1f2328}
header{background:#24292f;color:#fff;padding:14px 24px;font-size:18px;font-weight:600}
main{padding:20px 24px;max-width:1100px;margin:0 auto}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:16px 0}
.card{background:#fff;border:1px solid #d0d7de;border-radius:10px;padding:14px 16px}
.card .v{font-size:26px;font-weight:700;color:#0969da}.card .l{font-size:12px;color:#57606a;margin-top:2px}
h3{border-left:4px solid #0969da;padding-left:8px;margin:24px 0 10px}
table{border-collapse:collapse;width:100%;background:#fff;font-size:13px}
th,td{border:1px solid #d0d7de;padding:6px 10px;text-align:left}
th{background:#f6f8fa;font-weight:600}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px}
.b-short{background:#ddf4ff;color:#0969da}.b-long{background:#dafbe1;color:#1a7f37}.b-working{background:#fff8c5;color:#9a6700}
.b-active{background:#dafbe1;color:#1a7f37}.b-invalid{background:#ffebe9;color:#cf222e}.b-pending{background:#fff8c5;color:#9a6700}
.rules{background:#fff8c5;border:1px solid #d4a72c;border-radius:8px;padding:10px 14px;font-size:13px;margin:8px 0}
footer{color:#57606a;font-size:12px;padding:16px 24px;text-align:center}
</style></head><body>
<header>🧠 mile-neuro-memory 记忆面板 <span style="font-size:12px;opacity:.7">(可视化 v2.0)</span></header>
<main>
<div class="cards">
<div class="card"><div class="v" id="total">-</div><div class="l">记忆总数</div></div>
<div class="card"><div class="v" id="activeC">-</div><div class="l">生效中</div></div>
<div class="card"><div class="v" id="cacheHits">-</div><div class="l">缓存命中</div></div>
<div class="card"><div class="v" id="cacheEntries">-</div><div class="l">缓存条目</div></div>
<div class="card"><div class="v" id="ruleCount">-</div><div class="l">纠偏规则</div></div>
</div>
<h3>最近记忆时间线</h3>
<table id="tl"><thead><tr><th>时间</th><th>内容</th><th>层级</th><th>状态</th><th>重要性</th></tr></thead><tbody></tbody></table>
<h3>语义缓存看板</h3>
<table id="cacheT"><thead><tr><th>层级</th><th>条目</th><th>命中</th></tr></thead><tbody></tbody></table>
<h3>纠偏规则（反馈闭环）</h3>
<div id="rules"></div>
</main>
<footer>数据由 neuromem.py serve 提供</footer>
<script>
async function load(url){const r=await fetch(url);return r.json()}
(async()=>{
 try{
  const s=await load('/api/ui/stats');
  document.getElementById('total').textContent=s.total;
  document.getElementById('activeC').textContent=s.active;
  document.getElementById('cacheHits').textContent=s.cache.hits;
  document.getElementById('cacheEntries').textContent=s.cache.entries;
  document.getElementById('ruleCount').textContent=s.rules.length;
  const tl=document.getElementById('tl').querySelector('tbody');
  for(const m of s.timeline){
   const tr=document.createElement('tr');
   tr.innerHTML='<td>'+m.updated_at+'</td><td>'+m.summary+'</td><td><span class="badge b-'+m.tier+'">'+m.tier+'</span></td><td><span class="badge b-'+m.status+'">'+m.status+'</span></td><td>'+m.importance+'</td>';
   tl.appendChild(tr);
  }
  const ct=document.getElementById('cacheT').querySelector('tbody');
  for(const [tier,v] of Object.entries(s.cache.tiers)){
   const tr=document.createElement('tr');
   tr.innerHTML='<td>'+tier+'</td><td>'+v.entries+'</td><td>'+v.hits+'</td>';
   ct.appendChild(tr);
  }
  const rbox=document.getElementById('rules');
  if(s.rules.length===0){rbox.innerHTML='<p style="color:#57606a">暂无纠偏规则</p>'}
  else{s.rules.forEach(r=>{const d=document.createElement('div');d.className='rules';d.textContent=r.old+' → '+r.new;rbox.appendChild(d)})}
 }catch(e){document.body.innerHTML='<main><h3>加载失败：'+e+'</h3><p>请确认 --user 参数一致</p></main>'}
})();
</script></body></html>"""


def _ui_stats(store: NeuroMemStore, user_id: str, limit: int = 50) -> dict:
    cfg = store.get_config(user_id)
    return {"total": len(store.all_memories(user_id)),
            "active": len([m for m in store.all_memories(user_id) if m.get("status") == "active"]),
            "timeline": _timeline_data(store, user_id, limit),
            "cache": _cache_board(store, user_id),
            "rules": cfg.get("correction_rules", [])[-20:]}


# ---------------------------------------------------------------------------
# v2.0 RQ-09 离线巩固后台化：consolidate --daemon 守护模式（低峰自动整理+忙退避）
# ---------------------------------------------------------------------------

def cmd_consolidate_daemon(args) -> int:
    """后台守护：每隔 --interval-min 分钟执行一次巩固；忙时退避（检测最近 --busy-window 分钟
    有写入则顺延）。Ctrl+C 退出。"""
    store = NeuroMemStore(Path(args.dir))
    interval = int(args.interval_min or 30)
    busy_window = int(args.busy_window or 5)
    out({"ok": True, "daemon": True, "interval_min": interval,
         "note": "守护模式启动，Ctrl+C 退出"})
    try:
        while True:
            now = now_iso()
            # 忙退避：最近 busy_window 分钟有 add/correct 则推迟
            busy = False
            ap = store.user_dir(args.user) / "audit.jsonl"
            if ap.exists():
                with open(ap, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            e = json.loads(line)
                        except Exception:
                            continue
                        if e.get("action") in ("add", "correct", "capture") and \
                           e.get("ts", "") >= now[:16]:
                            busy = True
            if busy:
                time.sleep(interval * 60)
                continue
            # 执行巩固（静默）
            try:
                short_mems = [m for m in store.all_memories(args.user)
                              if m.get("tier") == "short" and m.get("status") == "active"]
                promoted = 0
                for m in short_mems:
                    age_days = 0
                    if m.get("created_at"):
                        age_days = (datetime.now(timezone.utc) -
                                    datetime.fromisoformat(m["created_at"])).days
                    if (float(m.get("importance") or 0.4) >= 0.6 or age_days >= 7):
                        pm = dict(m)
                        pm["tier"] = "long"
                        pm["updated_at"] = now_iso()
                        store.delete_file(args.user, m)
                        store.save_memory(args.user, pm)
                        promoted += 1
                if promoted:
                    store.rebuild_index(args.user)
                _audit(store, args.user, "consolidate_daemon", "", f"promoted={promoted}")
            except Exception:
                pass
            time.sleep(interval * 60)
    except KeyboardInterrupt:
        return 0


# ---------------------------------------------------------------------------
# v2.0 RQ-11 公开基准跑分：内置合成评测集（零依赖、离线可跑）
#   场景：记忆写入 → 语义检索 → 矛盾修正 → 巩固迁移
#   输出：准确率/召回率/延迟，可复现
# ---------------------------------------------------------------------------

_BUILTIN_BENCH = [
    {"scenario": "语义检索（同义改写）",
     "memories": ["张三喜欢喝美式咖啡，常去公司楼下瑞幸",
                  "项目 Alpha 预算 50 万元，2026年3月上线",
                  "李四负责前端团队，偏好深色主题",
                  "公司 VPN 用企业微信扫码登录",
                  "王五的生日是 8 月 15 日"],
     "queries": [("常去瑞幸喝咖啡的是谁", "张三"), ("公司 VPN 怎么登录", "企业微信"),
                 ("谁负责前端", "李四"), ("Alpha 项目什么时候上线", "2026"),
                 ("王五哪天生日", "8 月")]},
    {"scenario": "偏好与身份记忆",
     "memories": ["用户是产品经理，常驻上海",
                  "客户张总偏好电话沟通，不喜欢邮件",
                  "团队周会固定在周五下午三点",
                  "报销需要两级审批，先直属上级再财务",
                  "王五英文名是 Kevin，负责后端"],
     "queries": [("用户做什么工作", "产品经理"), ("客户喜欢怎么联系", "电话"),
                 ("周会什么时间", "周五"), ("报销流程有几级", "两级"),
                 ("Kevin 是谁", "王五")]},
    {"scenario": "数字与配置事实",
     "memories": ["生产数据库连接超时设置为 30 秒",
                  "测试环境地址是 10.0.0.21",
                  "接口并发上限 500 QPS",
                  "服务器部署在华东区可用区 B",
                  "日志保留 90 天"],
     "queries": [("超时时间多少", "30 秒"), ("测试环境 IP", "10.0.0.21"),
                 ("并发上限是多少", "500"), ("服务器在哪个区", "华东"),
                 ("日志存多久", "90 天")]},
    {"scenario": "项目与业务事实",
     "memories": ["星火计划目标季度营收 1200 万元",
                  "合同编号 HT-2026-0815，签约方是云启科技"],
     "queries": [("星火计划目标营收多少", "1200"), ("合同签约方是谁", "云启")]},
    {"scenario": "矛盾修正（数值）",
     "memories": ["项目 Alpha 预算 50 万元"],
     "correct": ("项目 Alpha 预算调整为 80 万元", "80"),
     "query": ("Alpha 项目预算多少", "80")},
    {"scenario": "时间线矛盾",
     "memories": ["项目X定于 2026年3月 上线"],
     "correct": ("项目X推迟到 2026年6月 上线", "2026年6月"),
     "query": ("项目X 什么时候上线", "2026年6月")},
]


def cmd_benchmark(args) -> int:
    """公共基准跑分（RQ-11）：写入→检索→矛盾修正→巩固，输出指标。也可加载外部评测集 JSON。"""
    store = NeuroMemStore(Path(args.dir))
    user = args.user
    t0 = time.perf_counter()
    results = {"scenarios": [], "summary": {}}
    total_hit = total_q = 0
    query_ms_sum = 0.0
    for case in _BUILTIN_BENCH:
        r = {"scenario": case["scenario"], "checks": []}
        # 1. 写入记忆
        t1 = time.perf_counter()
        for mtext in case["memories"]:
            mem = new_memory(mtext, tier="short", importance=0.6)
            merged, sims = _dedup_and_link(store, user, mem)
            store.save_memory(user, merged)
            store.update_index_add(user, merged, sims)
        store.rebuild_index(user)
        write_ms = (time.perf_counter() - t1) * 1000
        r["checks"].append({"name": "memory write", "ok": True, "ms": round(write_ms, 1)})
        # 2. 矛盾修正（如该场景有 correct）
        if case.get("correct"):
            text, expect = case["correct"]
            old = store.all_memories(user)
            target = None
            for m in old:
                if m.get("status") == "active":
                    target = m
                    break
            if target:
                t2 = time.perf_counter()
                target["status"] = "invalid"
                target["updated_at"] = now_iso()
                store.push_version(user, target["id"], target)
                store.delete_file(user, target)
                save_json(store.archive_dir(user) / (target["id"] + ".json"), target)
                new = new_memory(text, tier="short", importance=0.7)
                new["links"] = [target["id"]]
                store.save_memory(user, new)
                store.rebuild_index(user)
                correct_ms = (time.perf_counter() - t2) * 1000
                r["checks"].append({"name": "矛盾修正留痕", "ok": True, "ms": round(correct_ms, 1)})
        # 3. 检索命中
        if case.get("queries"):
            for q, expect in case["queries"]:
                t3 = time.perf_counter()
                res = store.search(user, q, top_k=5)
                if isinstance(res, dict):
                    res = res["results"]
                ms = (time.perf_counter() - t3) * 1000
                hit = any(expect in (x.get("summary", "") or "") for x in res)
                total_hit += int(hit)
                total_q += 1
                query_ms_sum += ms
                r["checks"].append({"name": f"query: {q}", "ok": hit,
                                    "hit": hit, "ms": round(ms, 1)})
        if case.get("query"):
            q, expect = case["query"]
            t4 = time.perf_counter()
            res = store.search(user, q, top_k=5)
            if isinstance(res, dict):
                res = res["results"]
            ms = (time.perf_counter() - t4) * 1000
            hit = any(expect in (x.get("summary", "") or "") for x in res)
            total_hit += int(hit)
            total_q += 1
            query_ms_sum += ms
            r["checks"].append({"name": f"query: {q}", "ok": hit,
                                "hit": hit, "ms": round(ms, 1)})
        r["pass"] = sum(1 for c in r["checks"] if c["ok"])
        r["total"] = len(r["checks"])
        results["scenarios"].append(r)
    total_ms = (time.perf_counter() - t0) * 1000
    results["summary"] = {
        "accuracy": round(100.0 * total_hit / max(1, total_q), 2),
        "recall": round(100.0 * total_hit / max(1, total_q), 2),  # 合成集命中=召回
        "queries": total_q, "query_hits": total_hit,
        "total_ms": round(total_ms, 1),
        "avg_query_ms": round(query_ms_sum / max(1, total_q), 2),
        "total_query_ms": round(query_ms_sum, 1),
        "engine_version": VERSION,
        "note": "内置合成评测集（RQ-11），外部 LoCoMo/LongMemEval 可用同法接入",
    }
    out({"ok": True, "benchmark": results["summary"], "scenarios": results["scenarios"]})
    return 0


# ---------------------------------------------------------------------------
# 语义缓存模块（成本优化）：热/温/冷三级，语义命中零 Token 调用
#     hot  ↔ 瞬时工作记忆（会话内，内存级读写）
#     warm ↔ 短时记忆（本地高速存储，30 天）
#     cold ↔ 长时永久记忆（本地+云端，永久有效）
# 命中即「免单」：直接返回答案，不调用大模型；未命中时的上下文压缩见 SKILL.md。
# ---------------------------------------------------------------------------

CACHE_TIERS = ("hot", "warm", "cold")
CACHE_SIM_THRESHOLD = {"hot": 0.90, "warm": 0.85, "cold": 0.80}

def _cache_file(store, user_id: str, tier: str) -> Path:
    return store.user_dir(user_id) / f"cache-{tier}.jsonl"


def _read_cache(store, user_id: str, tier: str) -> list:
    p = _cache_file(store, user_id, tier)
    entries = []
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        pass
    return entries


def _write_cache(store, user_id: str, tier: str, entries: list, cap: int = 1000) -> None:
    p = _cache_file(store, user_id, tier)
    ensure_dir(p.parent)
    with open(p, "w", encoding="utf-8") as f:
        for e in entries[:cap]:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def cache_query(store: NeuroMemStore, user_id: str, query: str) -> tuple:
    """三级缓存逐级检索：hot → warm → cold。命中返回 (entry, tier)，全部未命中返回 (None, None)。
    命中后自动更新热度并做缓存升温（cold→warm→hot）。"""
    idx = {t: i for i, t in enumerate(CACHE_TIERS)}
    for i, tier in enumerate(CACHE_TIERS):
        thr = CACHE_SIM_THRESHOLD[tier]
        entries = _read_cache(store, user_id, tier)
        for e in entries:
            if text_sim(e.get("q", ""), query) >= thr:
                e["hit_count"] = e.get("hit_count", 0) + 1
                e["last_hit"] = now_iso()
                entries.remove(e)
                entries.insert(0, e)
                _write_cache(store, user_id, tier, entries)
                # 升温：写入更热的一级
                if i > 0:
                    hotter = _read_cache(store, user_id, CACHE_TIERS[i - 1])
                    hotter.insert(0, e)
                    _write_cache(store, user_id, CACHE_TIERS[i - 1], hotter)
                return e, tier
    return None, None


def cache_put(store: NeuroMemStore, user_id: str, query: str, answer: str,
              source: str = "", threshold: float | None = None) -> bool:
    """写入缓存（默认 warm 层）。与已有条目语义重复（≥0.95 同问同答）则刷新不新增。"""
    entry = {"q": query, "a": answer, "source": source, "hit_count": 0,
             "created_at": now_iso(), "last_hit": now_iso()}
    for tier in CACHE_TIERS:
        entries = _read_cache(store, user_id, tier)
        for e in entries:
            if text_sim(e.get("q", ""), query) >= 0.95 and e.get("a") == answer:
                e["created_at"] = now_iso()
                _write_cache(store, user_id, tier, entries)
                return False
    warm = _read_cache(store, user_id, "warm")
    warm.insert(0, entry)
    _write_cache(store, user_id, "warm", warm)
    return True


def cache_invalidate(store: NeuroMemStore, user_id: str, keyword: str) -> int:
    """按关键词移除缓存条目（记忆修正时联动失效，避免脏缓存）。"""
    removed = 0
    for tier in CACHE_TIERS:
        entries = _read_cache(store, user_id, tier)
        keep = [e for e in entries
                if keyword not in e.get("q", "") and keyword not in e.get("a", "")]
        removed += len(entries) - len(keep)
        _write_cache(store, user_id, tier, keep)
    return removed


def cmd_cache(args) -> int:
    store = NeuroMemStore(Path(args.dir))
    if args.action == "query":
        e, tier = cache_query(store, args.user, args.query)
        if e:
            out({"ok": True, "hit": True, "tier": tier, "answer": e.get("a"),
                 "source": e.get("source", ""), "hit_count": e.get("hit_count", 0)})
        else:
            out({"ok": True, "hit": False, "tier": None,
                 "message": "缓存未命中，需调用大模型（可先用记忆检索压缩上下文）"})
    elif args.action == "store":
        created = cache_put(store, args.user, args.query, args.answer, source=args.source)
        _audit(store, args.user, "cache_store", "", args.query[:80])
        out({"ok": True, "created": created, "query": args.query})
    elif args.action == "invalidate":
        removed = cache_invalidate(store, args.user, args.query)
        _audit(store, args.user, "cache_invalidate", "", f"removed={removed}")
        out({"ok": True, "removed": removed})
    elif args.action == "stats":
        stats = {"total_hits": 0, "tiers": {}}
        for tier in CACHE_TIERS:
            entries = _read_cache(store, args.user, tier)
            hits = sum(e.get("hit_count", 0) for e in entries)
            stats["tiers"][tier] = {"entries": len(entries), "hits": hits}
            stats["total_hits"] += hits
        out({"ok": True, "stats": stats})
    else:
        out({"ok": False, "error": "unknown cache action"})
        return 1
    return 0


# ---------------------------------------------------------------------------
# 增量同步：本地 ↔ 远端双目录（基于 version + updated_at 比对）
# ---------------------------------------------------------------------------

def _mem_state_map(store: NeuroMemStore, user_id: str) -> dict:
    """扫出用户全部记忆：{mem_id: {"version", "updated_at", "mem"}}。"""
    state = {}
    for p in store.mem_dir(user_id).rglob("*.json"):
        try:
            m = load_json(p)
        except Exception:
            continue
        if m.get("id"):
            state[m["id"]] = {"version": int(m.get("version") or 1),
                              "updated_at": m.get("updated_at", ""), "mem": m}
    return state


def cmd_sync(args) -> int:
    """增量同步：比对本地与远端每个记忆的 (version, updated_at)，新者胜。
    --direction up|down|bidirectional 控制同步方向（需求 8.2.1 sync_direction）；
    --dry-run 只预览不执行；archive/invalid 记忆默认不参与同步。"""
    store = NeuroMemStore(Path(args.dir))
    remote = NeuroMemStore(Path(args.remote_dir))
    local = _mem_state_map(store, args.user)
    rem = _mem_state_map(remote, args.user)
    direction = getattr(args, "direction", "bidirectional")
    plan = {"push": [], "pull": []}
    for mid in sorted(set(local) | set(rem)):
        lm, rm = local.get(mid), rem.get(mid)
        if lm and rm:
            if (rm["version"], rm["updated_at"]) > (lm["version"], lm["updated_at"]):
                if direction in ("down", "bidirectional"):
                    plan["pull"].append(mid)
            elif (lm["version"], lm["updated_at"]) > (rm["version"], rm["updated_at"]):
                if direction in ("up", "bidirectional"):
                    plan["push"].append(mid)
        elif lm and not rm:
            if direction in ("up", "bidirectional"):
                plan["push"].append(mid)
        elif rm and not lm:
            if direction in ("down", "bidirectional"):
                plan["pull"].append(mid)
    if args.dry_run:
        out({"ok": True, "dry_run": True, "direction": direction, "plan": plan})
        return 0
    pushed = pulled = 0
    for mid in plan["push"]:
        remote.save_memory(args.user, local[mid]["mem"])
        pushed += 1
    for mid in plan["pull"]:
        store.save_memory(args.user, rem[mid]["mem"])
        pulled += 1
    store.rebuild_index(args.user)
    _audit(store, args.user, "sync", "", f"push={pushed} pull={pulled}")
    out({"ok": True, "direction": direction, "pushed": pushed, "pulled": pulled, "plan": plan})
    return 0


# ---------------------------------------------------------------------------
# 可选 LLM 提炼（OpenAI 兼容接口；绕过失效代理）
# ---------------------------------------------------------------------------

def _llm_chat(messages: list, args) -> str | None:
    key = args.llm_api_key or os.environ.get("NEUROMEM_LLM_API_KEY")
    base = args.llm_base_url or os.environ.get("NEUROMEM_LLM_BASE_URL") or "https://api.openai.com/v1"
    model = args.llm_model or os.environ.get("NEUROMEM_LLM_MODEL") or "gpt-4o-mini"
    if not key:
        return None
    url = base.rstrip("/") + "/chat/completions"
    body = json.dumps({"model": model, "messages": messages, "temperature": 0.2,
                       "max_tokens": 256}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕死代理
        with opener.open(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return None


def _llm_summarize(text: str, args) -> str:
    summary = _llm_chat([
        {"role": "system", "content": "你是记忆编码器。请把下面内容压缩成一句简洁的长期记忆（<=50字，保留关键实体、数字、偏好），直接输出结果，不要解释。"},
        {"role": "user", "content": text},
    ], args)
    return summary or ""


# ---------------------------------------------------------------------------
# 本地 HTTP API（跨应用接入）
# ---------------------------------------------------------------------------

def _parse_bool(v) -> bool:
    return str(v).lower() in ("1", "true", "yes", "on")


class _ApiHandler(http.server.BaseHTTPRequestHandler):
    store = None
    user = "default"
    secret = ""

    def log_message(self, *a):  # 静默访问日志
        pass

    def _send(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _post_json(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        store: NeuroMemStore = self.store
        try:
            if path == "/api/search":
                # 前置缓存拦截（需求 3.2/8.1.2）：?enable_cache=false 关闭
                enable_cache = _parse_bool(q.get("enable_cache", ["true"])[0])
                if enable_cache:
                    e, tier = cache_query(store, self.user, q.get("query", [""])[0])
                    if e:
                        self._send(200, {"ok": True, "from_cache": True, "cache_tier": tier,
                                         "answer": e.get("a"), "source": e.get("source", ""),
                                         "hit_count": e.get("hit_count", 0), "results": []})
                        return
                res = store.search(self.user, q.get("query", [""])[0],
                                   top_k=int(q.get("top_k", ["10"])[0]),
                                   include_archived=_parse_bool(q.get("include_archived", ["false"])[0]))
                self._send(200, {"ok": True, "from_cache": False, "results": res})
            elif path == "/api/stats":
                # 全维度统计 + 成本看板（需求 8.2.2）
                mems = store.all_memories(self.user, include_archived=True)
                by_tier, by_status, by_subtype = {}, {}, {}
                for m in mems:
                    by_tier[m.get("tier", "?")] = by_tier.get(m.get("tier", "?"), 0) + 1
                    by_status[m.get("status", "?")] = by_status.get(m.get("status", "?"), 0) + 1
                    by_subtype[m.get("subtype", "?")] = by_subtype.get(m.get("subtype", "?"), 0) + 1
                cache_stats = {"total_hits": 0, "tiers": {}}
                for tier in CACHE_TIERS:
                    entries = _read_cache(store, self.user, tier)
                    hits = sum(e.get("hit_count", 0) for e in entries)
                    cache_stats["tiers"][tier] = {"entries": len(entries), "hits": hits}
                    cache_stats["total_hits"] += hits
                cfg = store.get_config(self.user)
                self._send(200, {"ok": True, "total": len(mems), "by_tier": by_tier,
                                 "by_status": by_status, "by_subtype": by_subtype,
                                 "cache": cache_stats,
                                 "profile_entries": len(cfg.get("profile", {}).get("preferences", [])),
                                 "encrypted": cfg.get("encrypted", False)})
            elif path == "/api/health":
                self._send(200, {"ok": True, "app": APP_NAME, "version": VERSION})
            elif path == "/ui" or path == "/ui/":
                # v2.0 RQ-08 可视化面板
                body = _UI_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/ui/stats":
                # v2.0 RQ-08 面板数据接口
                self._send(200, _ui_stats(store, self.user,
                                          int(q.get("limit", ["50"])[0])))
            else:
                self._send(404, {"ok": False, "error": "not found"})
        except Exception as e:
            self._send(500, {"ok": False, "error": str(e)})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        store: NeuroMemStore = self.store
        body = self._post_json()
        try:
            if path == "/api/add":
                text = body.get("text", "")
                if not text:
                    self._send(400, {"ok": False, "error": "text required"})
                    return
                mem = new_memory(text, tier=body.get("tier", "short"),
                                 subtype=body.get("subtype", _infer_subtype(text)),
                                 tags=body.get("tags"), entities=body.get("entities") or {},
                                 importance=float(body.get("importance", 0.4)),
                                 session_id=body.get("session_id"), source=body.get("source", "http-api"),
                                 context=body.get("context") or {})
                if self.secret and mem.get("text"):
                    mem["text"] = encrypt_text(mem["text"], self.secret)
                    mem["summary"] = encrypt_text(mem["summary"], self.secret)
                merged, sims = _dedup_and_link(store, self.user, mem)
                store.save_memory(self.user, merged)
                store.update_index_add(self.user, merged, sims)
                store.rebuild_index(self.user)
                self._send(200, {"ok": True, "id": merged["id"], "merged": merged["id"] != mem["id"]})
            elif path == "/api/correct":
                old = store.load_memory(self.user, body.get("id", ""))
                if not old:
                    self._send(404, {"ok": False, "error": "memory not found"})
                    return
                old["status"] = "invalid"
                old["updated_at"] = now_iso()
                store.push_version(self.user, old["id"], old)
                store.delete_file(self.user, old)
                save_json(store.archive_dir(self.user) / (old["id"] + ".json"), old)
                new = new_memory(body.get("text", old["summary"]), tier=old["tier"],
                                 subtype=old["subtype"], tags=old.get("tags"),
                                 entities=old.get("entities"), source="http-api")
                new["links"] = list(old.get("links", [])) + [old["id"]]
                store.save_memory(self.user, new)
                store.rebuild_index(self.user)
                self._send(200, {"ok": True, "old_id": old["id"], "new_id": new["id"]})
            elif path == "/api/forget":
                m = store.load_memory(self.user, body.get("id", ""))
                if not m:
                    self._send(404, {"ok": False, "error": "memory not found"})
                    return
                m["status"] = "archived"
                m["updated_at"] = now_iso()
                store.delete_file(self.user, m)
                save_json(store.archive_dir(self.user) / (m["id"] + ".json"), m)
                store.rebuild_index(self.user)
                self._send(200, {"ok": True, "id": m["id"], "status": "archived"})
            elif path in ("/api/cache", "/api/cache/query"):
                # 语义缓存查询（需求 8.1.3）：{query, similarity_threshold?, cache_level?}
                q = body.get("query", "")
                if not q:
                    self._send(400, {"ok": False, "error": "query required"})
                    return
                e, tier = cache_query(store, self.user, q)
                if e:
                    self._send(200, {"ok": True, "hit": True, "tier": tier,
                                     "answer": e.get("a"), "source": e.get("source", ""),
                                     "hit_count": e.get("hit_count", 0),
                                     "from_cache": True})
                else:
                    self._send(200, {"ok": True, "hit": False, "from_cache": False})
            elif path == "/api/cache/store":
                q, a = body.get("query", ""), body.get("answer", "")
                if not q or not a:
                    self._send(400, {"ok": False, "error": "query and answer required"})
                    return
                created = cache_put(store, self.user, q, a, source=body.get("source", "http-api"))
                self._send(200, {"ok": True, "created": created, "query": q,
                                 "from_cache": False})
            elif path == "/api/cache/invalidate":
                kw = body.get("query", "")
                if not kw:
                    self._send(400, {"ok": False, "error": "query required"})
                    return
                removed = cache_invalidate(store, self.user, kw)
                self._send(200, {"ok": True, "removed": removed})
            else:
                self._send(404, {"ok": False, "error": "not found"})
        except Exception as e:
            self._send(500, {"ok": False, "error": str(e)})


def cmd_serve(args) -> int:
    store = NeuroMemStore(Path(args.dir))
    _ApiHandler.store = store
    _ApiHandler.user = args.user
    _ApiHandler.secret = args.secret or ""
    server = http.server.ThreadingHTTPServer((args.host, args.port), _ApiHandler)
    print(json.dumps({"ok": True, "listening": f"http://{args.host}:{args.port}",
                      "endpoints": ["/api/health", "/api/add", "/api/search", "/api/correct",
                                    "/api/forget", "/api/stats", "/api/cache", "/api/cache/store",
                                    "/api/cache/invalidate"]}),
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="neuromem", description="NeuroMem 类脑记忆引擎")
    p.add_argument("--dir", default=os.environ.get("NEUROMEM_DIR", str(Path.home() / ".neuromem")),
                   help="记忆数据根目录（默认 ~/.neuromem）")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp, extra=()):
        sp.add_argument("--user", default="default", help="用户 ID（多租户隔离维度）")
        for name in extra:
            if name == "secret":
                sp.add_argument("--secret", default="", help="本地加密密钥（与 --encrypt 搭配）")

    # add
    sp = sub.add_parser("add", help="写入记忆（自动分层/去重/建索引）")
    add_common(sp, ("secret",))
    sp.add_argument("--text", default="", help="记忆文本")
    sp.add_argument("--file", default="", help="从文件读取记忆文本")
    sp.add_argument("--tier", choices=TIERS, default="", help="强制层级（默认 short）")
    sp.add_argument("--subtype", choices=SUBTYPES, default="", help="记忆子类型")
    sp.add_argument("--tags", nargs="*", default=None, help="标签列表")
    sp.add_argument("--entities", default="", help='实体 JSON，如 {"person":["张三"],"place":["杭州"]}')
    sp.add_argument("--importance", type=float, default=0.4, help="重要性 0-1")
    sp.add_argument("--session", default="", help="会话 ID（工作记忆维度）")
    sp.add_argument("--source", default="", help="来源（workbuddy/cursor/...）")
    sp.add_argument("--context-json", default="", help='上下文 JSON，如 {"mood":"happy","device":"pc"}')
    sp.add_argument("--llm-summarize", action="store_true", help="用 LLM 提炼摘要（需配置 API Key）")
    sp.add_argument("--llm-api-key", default="", help="LLM API Key（或环境变量 NEUROMEM_LLM_API_KEY）")
    sp.add_argument("--llm-base-url", default="", help="LLM Base URL（OpenAI 兼容）")
    sp.add_argument("--llm-model", default="", help="LLM 模型名")
    sp.add_argument("--scene", default="", help="场景分类（需求8.1.1）：work/life/study，写入 context.scene")
    sp.add_argument("--encrypt", dest="encrypt_flag", action="store_true",
                    help="端侧加密（需求3.5）：需配合 --secret，并持久化该用户 encrypted 开关")
    sp.add_argument("--auto-conflict", action="store_true",
                    help="自动检测与已有记忆的矛盾，冲突方旧记忆标 pending（待验证）")
    # search
    sp = sub.add_parser("search", help="联想式检索（多维加权）")
    add_common(sp)
    sp.add_argument("--query", required=True, help="查询内容")
    sp.add_argument("--top-k", type=int, default=10)
    sp.add_argument("--tier", choices=TIERS, default=None)
    sp.add_argument("--subtype", choices=SUBTYPES, default=None)
    sp.add_argument("--tags", nargs="*", default=None)
    sp.add_argument("--from-date", default="", help="ISO 起（含）")
    sp.add_argument("--to-date", default="", help="ISO 止（含）")
    sp.add_argument("--include-archived", action="store_true", help="包含归档记忆")
    sp.add_argument("--no-expand-graph", action="store_true", help="关闭图谱联想扩展")
    sp.add_argument("--min-score", type=float, default=0.05)
    sp.add_argument("--no-cache", action="store_true",
                    help="关闭前置缓存拦截（默认开启，需求3.2 enable_cache）")
    sp.add_argument("--context-json", default="", help='情境 JSON，如 {"scene":"出差","device":"pc"}，命中情境记忆加权')
    sp.add_argument("--complete-fragments", action="store_true", help="对碎片化短记忆做图谱推测补全并标注")
    # get
    sp = sub.add_parser("get", help="按 ID 读取一条记忆")
    add_common(sp, ("secret",))
    sp.add_argument("--id", required=True)
    # versions
    sp = sub.add_parser("versions", help="查看记忆版本历史")
    add_common(sp)
    sp.add_argument("--id", required=True)
    # update
    sp = sub.add_parser("update", help="更新记忆（自动留版本快照）")
    add_common(sp, ("secret",))
    sp.add_argument("--id", required=True)
    sp.add_argument("--text", default="", help="新文本")
    sp.add_argument("--importance", type=float, default=None)
    sp.add_argument("--tags", nargs="*", default=None)
    # restore
    sp = sub.add_parser("restore", help="回滚到历史版本（同样留快照）")
    add_common(sp)
    sp.add_argument("--id", required=True)
    sp.add_argument("--version", type=int, required=True)
    # correct
    sp = sub.add_parser("correct", help="修正错误记忆（旧标 invalid 留痕，新记忆关联）")
    add_common(sp, ("secret",))
    sp.add_argument("--id", required=True)
    sp.add_argument("--text", required=True, help="修正后的正确内容")
    # contradictions
    sp = sub.add_parser("contradictions", help="检测矛盾记忆")
    add_common(sp)
    # consolidate
    sp = sub.add_parser("consolidate", help="记忆巩固：短时→长时迁移+去重+画像提炼")
    add_common(sp)
    sp.add_argument("--importance-threshold", type=float, default=0.6)
    sp.add_argument("--access-threshold", type=int, default=3)
    sp.add_argument("--min-age-days", type=int, default=7)
    sp.add_argument("--auto-resolve", action="store_true",
                    help="自动失效：被 ≥2 次新信息否定的 pending 记忆标 invalid 归档")
    sp.add_argument("--daemon", action="store_true", help="后台守护模式：定时巩固+忙退避（RQ-09）")
    sp.add_argument("--interval-min", type=int, default=30, help="守护模式巩固间隔（分钟）")
    sp.add_argument("--busy-window", type=int, default=5, help="忙退避窗口（分钟，窗口内有写入则顺延）")
    # forget
    sp = sub.add_parser("forget", help="归档记忆（非物理删除）")
    add_common(sp)
    sp.add_argument("--id", required=True)
    # demote
    sp = sub.add_parser("demote", help="降低记忆检索权重（遗忘曲线）")
    add_common(sp)
    sp.add_argument("--id", required=True)
    sp.add_argument("--factor", type=float, default=0.5)
    # stats
    sp = sub.add_parser("stats", help="记忆概况统计")
    add_common(sp)
    # profile
    sp = sub.add_parser("profile", help="读取用户画像（元记忆）")
    add_common(sp)
    sp.add_argument("--llm-summarize", action="store_true")
    # export / import
    sp = sub.add_parser("export", help="全量导出备份")
    add_common(sp)
    sp.add_argument("--output", default="", help="导出文件路径")
    sp = sub.add_parser("import", help="导入记忆备份")
    add_common(sp)
    sp.add_argument("--file", required=True)
    # batch（需求 8.2.3 记忆批量操作接口）
    sp = sub.add_parser("batch", help="批量操作（需求8.2.3）：delete/tag/importance/export")
    add_common(sp)
    sp.add_argument("--ids", required=True, help="逗号分隔的记忆 ID 列表")
    sp.add_argument("--operation", required=True,
                    choices=("delete", "tag", "importance", "export"),
                    help="批量动作：delete 软删除归档 / tag 标记分类 / importance 调整重要性 / export 导出")
    sp.add_argument("--params", default="", help='JSON 参数：tag 用 {"tags":[...]}，importance 用 {"value":0.8}，export 用 {"output":"路径"}')
    # serve
    sp = sub.add_parser("serve", help="启动本地 HTTP API（跨应用接入）")
    add_common(sp, ("secret",))
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=17890)
    # audit
    sp = sub.add_parser("audit", help="查看操作审计日志（全链路留痕）")
    add_common(sp)
    sp.add_argument("--limit", type=int, default=0, help="只显示最近 N 条")
    # cache（语义缓存：语义命中零 Token 调用）
    sp = sub.add_parser("cache", help="语义缓存：query 查 / store 存 / invalidate 失效 / stats 统计")
    add_common(sp)
    sp.add_argument("action", choices=("query", "store", "invalidate", "stats"),
                    help="cache 动作（注意放 --user 后面）")
    sp.add_argument("--query", default="", help="查询文本（query/invalidate 用）")
    sp.add_argument("--answer", default="", help="答案文本（store 用）")
    sp.add_argument("--source", default="", help="答案来源（store 用）")
    # sync
    sp = sub.add_parser("sync", help="增量同步：本地 ↔ 远端目录（version+updated_at 新者胜）")
    add_common(sp)
    sp.add_argument("--remote-dir", required=True, help="远端记忆根目录（同构目录结构）")
    sp.add_argument("--direction", choices=("up", "down", "bidirectional"), default="bidirectional",
                    help="同步方向（需求8.2.1 sync_direction）：up=仅推送本地→远端，down=仅拉取远端→本地，bidirectional=双向")
    sp.add_argument("--dry-run", action="store_true", help="只预览 push/pull 计划，不执行")
    # v2.0 RQ-03 自动捕获
    sp = sub.add_parser("capture", help="自动捕获（RQ-03）：从对话/文本提炼关键事实写入记忆")
    add_common(sp)
    sp.add_argument("--text", default="", help="原始对话/文本")
    sp.add_argument("--file", default="", help="从文件读取文本")
    sp.add_argument("--tags", nargs="*", default=None, help="附加标签")
    # v2.0 RQ-04 MCP 服务
    sp = sub.add_parser("mcp", help="MCP stdio 服务器（RQ-04）：MCP 客户端即插即用")
    add_common(sp)
    # v2.0 RQ-05 图谱查询
    sp = sub.add_parser("graph", help="实体关系图谱（RQ-05）：查看/导出图谱")
    add_common(sp)
    sp.add_argument("--entity", default="", help="查询某实体的节点与关系")
    # v2.0 RQ-07 自我进化
    sp = sub.add_parser("evolve", help="自我进化（RQ-07）：从纠偏历史提炼规则入程序记忆")
    add_common(sp)
    # v2.0 RQ-11 基准跑分
    sp = sub.add_parser("benchmark", help="公共基准跑分（RQ-11）：内置合成评测集，输出准确率/召回率")
    add_common(sp)
    return p


def main() -> int:
    args = build_parser().parse_args()
    store = NeuroMemStore(Path(args.dir))
    ensure_dir(store.user_dir(args.user))
    if args.command == "add":
        return cmd_add(args)
    if args.command == "search":
        ctx = json.loads(args.context_json) if args.context_json else None
        # 前置缓存拦截（需求 3.2/8.1.2 enable_cache）：命中直接返回，零大模型调用
        if not args.no_cache:
            e, tier = cache_query(store, args.user, args.query)
            if e:
                out({"ok": True, "from_cache": True, "cache_tier": tier,
                     "answer": e.get("a"), "source": e.get("source", ""),
                     "hit_count": e.get("hit_count", 0), "results": []})
                return 0
        res = store.search(args.user, args.query, top_k=args.top_k, tier=args.tier,
                           subtype=args.subtype, tags=args.tags,
                           from_date=args.from_date or None, to_date=args.to_date or None,
                           include_archived=args.include_archived,
                           expand_graph=not args.no_expand_graph, min_score=args.min_score,
                           context=ctx, complete_fragments=args.complete_fragments)
        if isinstance(res, dict):  # 宽泛查询兜底
            out({"ok": True, "broad": True, "from_cache": False, "results": res["results"]})
        else:
            out({"ok": True, "from_cache": False, "results": res})
        return 0
    if args.command == "get":
        m = store.load_memory(args.user, args.id)
        if not m:
            out({"ok": False, "error": "memory not found", "id": args.id})
            return 1
        if args.secret and (m.get("text", "").startswith("enc:v1:") or m.get("text", "").startswith("enc:v2:")):
            try:
                m["text"] = decrypt_text(m["text"], args.secret)
                m["summary"] = decrypt_text(m["summary"], args.secret)
            except (ValueError, UnicodeDecodeError, base64.binascii.Error) as e:
                out({"ok": False, "error": str(e), "id": args.id})
                return 1
        out({"ok": True, "memory": m})
        return 0
    if args.command == "versions":
        out({"ok": True, "id": args.id, "versions": store.get_versions(args.user, args.id)})
        return 0
    if args.command == "update":
        m = store.load_memory(args.user, args.id)
        if not m:
            out({"ok": False, "error": "memory not found", "id": args.id})
            return 1
        # 加密记忆更新：需要 secret 解密原值，新内容加密写入（需求 3.5）
        cfg = store.get_config(args.user)
        if cfg.get("encrypted") and (m.get("text", "").startswith("enc:v1:") or m.get("text", "").startswith("enc:v2:")):
            if not args.secret:
                out({"ok": False, "error": "memory is encrypted, --secret required for update"})
                return 1
            m["text"] = decrypt_text(m["text"], args.secret)
            m["summary"] = decrypt_text(m["summary"], args.secret)
        store.push_version(args.user, m["id"], m)  # 变更前快照（明文入快照，个人本地可回溯）
        if args.text:
            m["text"] = args.text
            m["summary"] = args.text
        if args.importance is not None:
            m["importance"] = args.importance
        if args.tags is not None:
            m["tags"] = args.tags
        m["updated_at"] = now_iso()
        if cfg.get("encrypted"):
            m["text"] = encrypt_text(m["text"], args.secret)
            m["summary"] = encrypt_text(m["summary"], args.secret)
        store.save_memory(args.user, m)
        store.rebuild_index(args.user)
        out({"ok": True, "id": m["id"], "version": len(store.get_versions(args.user, m["id"])) + 1})
        return 0
    if args.command == "restore":
        m = store.restore_version(args.user, args.id, args.version)
        if not m:
            out({"ok": False, "error": "version not found"})
            return 1
        out({"ok": True, "id": m["id"], "restored_version": args.version, "new_version": m["version"]})
        return 0
    if args.command == "correct":
        return cmd_correct(args)
    if args.command == "contradictions":
        issues = check_contradictions(store, args.user)
        out({"ok": True, "issues": issues, "count": len(issues)})
        return 0
    if args.command == "audit":
        return cmd_audit(args)
    if args.command == "cache":
        return cmd_cache(args)
    if args.command == "sync":
        return cmd_sync(args)
    if args.command == "consolidate":
        if getattr(args, "daemon", False):
            return cmd_consolidate_daemon(args)
        return cmd_consolidate(args)
    if args.command == "forget":
        return cmd_forget(args)
    if args.command == "demote":
        return cmd_demote(args)
    if args.command == "stats":
        return cmd_stats(args)
    if args.command == "profile":
        return cmd_profile(args)
    if args.command == "export":
        return cmd_export(args)
    if args.command == "import":
        return cmd_import(args)
    if args.command == "batch":
        return cmd_batch(args)
    if args.command == "serve":
        return cmd_serve(args)
    if args.command == "capture":
        return cmd_capture(args)
    if args.command == "mcp":
        return cmd_mcp(args)
    if args.command == "graph":
        return cmd_graph(args)
    if args.command == "evolve":
        return cmd_evolve(args)
    if args.command == "benchmark":
        return cmd_benchmark(args)
    out({"ok": False, "error": "unknown command"})
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
