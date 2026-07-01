#!/usr/bin/env python3
"""既存の per-visa 観点メタを、原本の章立て（5単元）に再編する。

新単元:
  basics(ビザの基本)        ← basics
  commercial(商用に必要なビザ) ← b_visa
  work(就労に必要なビザ)      ← e_visa + l_visa + h1b_visa（多いので上限で間引き）
  study(就学に必要なビザ)     ← f_visa
  training(研修に必要なビザ)   ← j_visa + H-3/研修B（新規）

各級（beginner/intermediate/advanced）ごとに再編。ID は新略号+連番で振り直す。
level_description は代表元（統合の場合は先頭ソース）を引き継ぎ、単元名に合わせて微修正。
"""
import json
import os
from pathlib import Path

PDIR = Path("backend/perspectives")
LEVELS = ["beginner", "intermediate", "advanced"]

# 新単元 -> (単元名, ID略号, [元unit_id...], 観点上限 or None)
PLAN = {
    "basics":     ("ビザの基本",        "bs", ["basics"],                       None),
    "commercial": ("商用に必要なビザ",   "co", ["b_visa"],                       None),
    "work":       ("就労に必要なビザ",   "wk", ["e_visa", "l_visa", "h1b_visa"], 48),
    "study":      ("就学に必要なビザ",   "st", ["f_visa"],                       None),
    "training":   ("研修に必要なビザ",   "tr", ["j_visa"],                       None),
}

# level_description は元の文の「〜について」の主語を単元名に置換して流用する。
def adapt_desc(desc: str, old_names, new_name: str) -> str:
    if not desc:
        return f"{new_name}について、原本・観点に明記された内容のみから出題する。創作した事実・数値・条文を含めない。"
    out = desc
    for on in old_names:
        out = out.replace(on, new_name)
    return out


def load(level, unit_id):
    p = PDIR / f"{level}_{unit_id}.json"
    if not p.exists():
        return None
    return json.load(open(p, encoding="utf-8"))


def build():
    # training に足す新規観点
    extra = json.load(open("scripts/training_extra_perspectives.json", encoding="utf-8"))
    training_extra = extra["H3_and_B"]

    for level in LEVELS:
        for new_id, (new_name, abbr, sources, cap) in PLAN.items():
            merged = []
            descs = []
            src_names = []
            for src in sources:
                d = load(level, src)
                if d is None:
                    continue
                src_names.append(d.get("unit_name", src))
                if d.get("level_description"):
                    descs.append(d["level_description"])
                for p in d.get("perspectives", []):
                    merged.append({
                        "name": p.get("name", ""),
                        "summary": p.get("summary", ""),
                        "source_pages": p.get("source_pages", []),
                    })
            # training には H-3/研修B を追記
            if new_id == "training":
                for p in training_extra:
                    merged.append({
                        "name": p["name"],
                        "summary": p["summary"],
                        "source_pages": p.get("source_pages", []),
                    })
            # 上限で間引き（先頭から採用）
            if cap is not None and len(merged) > cap:
                merged = merged[:cap]
            # ID 振り直し
            for i, p in enumerate(merged, 1):
                p_id = f"{abbr}{i:02d}"
                # dict のキー順を id,name,summary,source_pages に
                p_ordered = {
                    "id": p_id,
                    "name": p["name"],
                    "summary": p["summary"],
                    "source_pages": p["source_pages"],
                }
                merged[i - 1] = p_ordered

            desc = adapt_desc(descs[0] if descs else "", src_names, new_name)
            obj = {
                "level": level,
                "unit_id": new_id,
                "unit_name": new_name,
                "level_description": desc,
                "perspectives": merged,
            }
            outp = PDIR / f"{level}_{new_id}.json"
            with open(outp, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
            print(f"wrote {outp}  ({len(merged)} 観点)")


if __name__ == "__main__":
    build()
