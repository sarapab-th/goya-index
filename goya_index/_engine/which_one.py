"""'Which one?': ask instead of guessing when look-alike files compete for a vague question.

Some questions cannot pick one file: "the loop JSON file" when there are 1,050 loop.json files, "the scoring module"
when there are four scoring.py. Plain search returns one of them anyway. This rule asks instead:
    "1,050 files look like */loop.json. Which one? Closest: ..."

Look-alike files: the same file name in different folders (scoring.py), or the same name with only a number or id
changed (tick-00010/loop.json, training/c1990cce.json).

Names in a question: code-like words (`load_config`, ParseError, json_depth, R2-1) found literally in a
file's text or path, and plain words found in at most RARE files.

The rule, in order:
  1. The question names something the best match has and at most RARE files have: answer with the best match.
  2. Candidate groups of look-alikes:
     (a) the best match's group, when another member is also in the top K results;
     (b) a group the question names by kind and type ("loop" + "JSON", "scoring" + "module"), in plain words, unless
         the best match's own name is named at least as fully and the question names no other file type.
     No group: answer with the best match.
  3. The question's names and path words decide between the candidates and the best match:
     - the best match has more of the question's names than any other candidate: answer with the best match;
     - otherwise keep the candidates with the most names, then the most path words that not all of them share;
       if that leaves one file: answer with it (which-one/1 answered only when it was the best match).
  4. Ask: name the group(s) and show at most SHOW candidates, best ranked first.
No model. Uses only the harness's word search and its own index of file texts.

Rule "which-one/2". which-one/1 was tested once, blind, on fresh questions about one code base; /2 changes step 3
after that test and has not been tested on fresh questions. See docs/BENCHMARKS.md, section 6.
"""
from __future__ import annotations

import collections
import os
import posixpath
import re
import sqlite3
import threading
from pathlib import Path

RULE = "which-one/2"
RANKED_LIMIT = 50  # how many search results the rule looks at, as tested

HEX = re.compile(r"(?<![0-9a-z])(?=[0-9a-f]*\d)[0-9a-f]{6,}(?![0-9a-z])")
TOK = re.compile(r"[a-z]+\d*|\d+")
FTS_TOK = re.compile(r"[a-z0-9]+")
WORD = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*[A-Za-z0-9_]|[A-Za-z0-9_]")
TICKS = re.compile(r"`([^`]+)`")
K, SHOW, RARE, MAX_GROUPS = 5, 5, 3, 3
# Words that describe any file, not a kind of file.
GENERIC = set("""a an and are as at be by can code contain contained contains data define defined defines describe
does file files for from function functions how in information into is it its json key keys kind md module modules of
on or provided purpose py script that the their this to type used uses what when where which with within""".split())
# A kind counts as named only together with a word for its file type.
TYPE_WORDS = {"json": {"json"}, "jsonl": {"json", "jsonl"}, "py": {"module", "modules", "script", "python"},
              "md": {"document", "doc", "docs", "markdown", "readme", "proposal", "spec"}, "csv": {"csv"},
              "ini": {"ini", "config"}, "txt": {"text", "txt"}, "yaml": {"yaml", "config"},
              "yml": {"yaml", "config"}, "toml": {"toml", "config"}, "sh": {"shell", "script"},
              "js": {"javascript", "script"}, "html": {"html", "page"}, "rs": {"rust"}}
ALL_TYPE_WORDS = set().union(*TYPE_WORDS.values())


def _norm(s):
    return re.sub(r"\d+", "#", HEX.sub("*", s.lower()))


def group_key(path):
    base = posixpath.basename(path)
    nb = _norm(base)
    if nb != base.lower():                      # the name itself carries a number or id: the folder shape counts too
        return _norm(posixpath.dirname(path)) + "/" + nb
    return "*/" + nb


def tokens(s):
    return set(TOK.findall(s.lower().replace("_", " ")))


def kind_words(key):
    """The words that name a group: its file name without numbers, ids and extension; for names that are only an
    id (c1990cce.json), the nearest named folder (training)."""
    base = key.rsplit("/", 1)[1]
    stem = base.rsplit(".", 1)[0] if "." in base else base
    t = {x for x in tokens(stem.replace("#", " ").replace("*", " ")) if not x.isdigit() and len(x) > 1}
    if not t:
        for seg in reversed(key.split("/")[:-1]):
            t = {x for x in tokens(seg.replace("#", " ").replace("*", " ")) if not x.isdigit() and len(x) > 1}
            if t:
                break
    return t - GENERIC


def ext_of(key):
    base = key.rsplit("/", 1)[1]
    return base.rsplit(".", 1)[1] if "." in base else ""


def is_code_like(w):
    return ("_" in w or re.search(r"[a-z][A-Z]", w) is not None or "." in w
            or (re.search(r"[A-Za-z]", w) is not None and re.search(r"\d", w) is not None))


def split_question(query):
    """Code-like names (literal) and the remaining plain words."""
    code = [m.strip("()").strip() for m in TICKS.findall(query)]
    rest = TICKS.sub(" ", query)
    plain = []
    for w in WORD.findall(rest):
        (code if is_code_like(w) else plain).append(w)
    code = sorted({c for c in code if len(c) >= 3})
    return code, plain


class Index:
    """The look-alike groups and literal-name lookups over the harness's word index (lexical.sqlite3).

    With a catalog, files that sync changed, added or deleted are read from the catalog's sync overlay, as search
    does: a changed file's new text replaces its old text, and a deleted file drops out."""

    def __init__(self, lexical_database, catalog=None):
        self.con = _connect_ro(lexical_database)
        self._lock = threading.Lock()
        self.cat, self.replaced, self.overlay = None, set(), []
        if catalog is not None:
            cat = _connect_ro(catalog)
            try:
                self.replaced = {r[0] for r in cat.execute("select source_path from sync_overrides")}
                self.replaced |= {r[0] for r in cat.execute("select source_path from sync_tombstones")}
                self.overlay = [r[0] for r in cat.execute(
                    "select source_path from sync_overrides where content is not null order by source_path")]
                self.cat = cat
            except sqlite3.OperationalError:  # a catalog without sync tables
                cat.close()
                self.replaced, self.overlay = set(), []
        self.members = collections.defaultdict(list)
        self.rowid, self.path_of = {}, {}
        for rid, p in self.con.execute("select rowid, path from documents"):
            self.rowid[p], self.path_of[rid] = rid, p
            if p not in self.replaced:
                self.members[group_key(p)].append(p)
        for p in self.overlay:
            self.members[group_key(p)].append(p)
        self.paths = [p for p in self.rowid if p not in self.replaced] + self.overlay
        self.kinds = [(key, kind_words(key)) for key, m in self.members.items() if len(m) >= 2]
        self.kinds = [(k, w) for k, w in self.kinds if w]
        self._lit, self._fts = {}, {}

    def _fts_rows(self, words, limit=None):
        phrase = '"%s"' % " ".join(words)
        sql = "select rowid from documents_fts where documents_fts match ?" + (" limit %d" % limit if limit else "")
        try:
            return [r[0] for r in self.con.execute(sql, (phrase,))]
        except sqlite3.OperationalError:
            return []

    def _overlay_rows(self, words, content_only=False):
        if self.cat is None or not self.overlay:
            return []
        phrase = '"%s"' % " ".join(words)
        try:
            return [r[0] for r in self.cat.execute("select source_path from sync_fts where sync_fts match ?",
                                                   ("content : " + phrase if content_only else phrase,))]
        except sqlite3.OperationalError:
            return []

    def files_with_name(self, name):
        """Files whose text or path holds this code-like name literally (case ignored)."""
        n = name.lower()
        if n not in self._lit:
            parts = FTS_TOK.findall(n)
            rows = self._fts_rows(parts) if parts else []
            hit = set()
            for i in range(0, len(rows), 900):
                chunk = rows[i:i + 900]
                q = "select rowid from documents where rowid in (%s) and instr(lower(content), ?) > 0" % (
                    ",".join("?" * len(chunk)))
                hit.update(self.path_of[r[0]] for r in self.con.execute(q, (*chunk, n)))
            hit -= self.replaced
            over = self._overlay_rows(parts) if parts else []
            for i in range(0, len(over), 900):
                chunk = over[i:i + 900]
                q = ("select source_path from sync_overrides where source_path in (%s) and content is not null"
                     " and instr(lower(content), ?) > 0" % ",".join("?" * len(chunk)))
                hit.update(r[0] for r in self.cat.execute(q, (*chunk, n)))
            hit.update(p for p in self.paths if n in p.lower())
            self._lit[n] = hit
        return self._lit[n]

    def files_with_word(self, word, limit=RARE + 1):
        """Files with this plain word in their name or text (by the index's own words), up to about limit."""
        w = word.lower()
        if w not in self._fts:
            parts = FTS_TOK.findall(w)
            if not parts:
                self._fts[w] = set()
            else:
                base = {self.path_of[r] for r in self._fts_rows(parts, limit + len(self.replaced))} - self.replaced
                over = set(self._overlay_rows(parts, content_only=True))
                over.update(p for p in self.overlay if _phrase_in(parts, posixpath.basename(p)))
                self._fts[w] = base | over
        return self._fts[w]


def _connect_ro(path):
    return sqlite3.connect("file:%s?mode=ro" % Path(path).resolve(strict=True), uri=True, check_same_thread=False)


def _phrase_in(parts, text):
    toks = FTS_TOK.findall(text.lower())
    n = len(parts)
    return any(toks[i:i + n] == parts for i in range(len(toks) - n + 1))


def names_in(query, ix):
    """{name: files holding it} for the question's code-like names and its rare plain words."""
    code, plain = split_question(query)
    out = {c: ix.files_with_name(c) for c in code}
    for w in set(plain):
        if w.lower() in GENERIC or len(w) < 3:
            continue
        f = ix.files_with_word(w)
        if 0 < len(f) <= RARE:
            out[w] = f
    return {k: v for k, v in out.items() if v}, plain


def decide(query, ranked, ix):
    """ranked: the harness's files, best first. Returns ("answer", {...}) or ("ask", {...})."""
    if not ranked:
        return ("answer", {"path": None, "why": "no match"})
    top = ranked[0]
    names, plain = names_in(query, ix)
    rare = sorted(n for n, f in names.items() if top in f and len(f) <= RARE)
    if rare:
        return ("answer", {"path": top, "why": "names something rare: %s" % ", ".join(rare)})
    q = {t for w in plain for t in tokens(w)}
    groups = []
    key = group_key(top)
    if len(ix.members.get(key, ())) >= 2 and any(group_key(p) == key for p in ranked[1:K]):
        groups.append(key)
    top_kind = kind_words(key) or tokens(posixpath.basename(top).rsplit(".", 1)[0]) - GENERIC
    named = [(k, w) for k, w in ix.kinds
             if k not in groups and w <= q and TYPE_WORDS.get(ext_of(k), set()) & q]
    if named:
        best = max(len(w) for _, w in named)
        named = [(k, w) for k, w in named if len(w) == best]
        q_types = q & ALL_TYPE_WORDS
        top_type_ok = not q_types or bool(q_types & TYPE_WORDS.get(ext_of(key), set()))
        if not (top_kind and top_kind <= q and len(top_kind) >= best and top_type_ok):
            pos = {group_key(p): i for i, p in reversed(list(enumerate(ranked)))}
            named.sort(key=lambda kw: (pos.get(kw[0], 10 ** 6), -len(ix.members[kw[0]])))
            groups += [k for k, _ in named]
    groups = groups[:MAX_GROUPS]
    if not groups:
        return ("answer", {"path": top, "why": "no look-alikes"})
    cands = list(dict.fromkeys([p for g in groups for p in ix.members[g]]))
    # The question's names: does the best match have more of them than any candidate?
    if names:
        hits = lambda p: sum(p in f for f in names.values())  # noqa: E731
        h_top = hits(top)
        h_other = max((hits(p) for p in cands if p != top), default=0)
        if h_top > h_other:
            return ("answer", {"path": top, "why": "its names point to the best match"})
        if h_other > 0:
            cands = [p for p in cands if hits(p) == h_other]
    # Path words that only some candidates have.
    narrowed = False
    if len(cands) > 1:
        common = set.intersection(*(tokens(p) for p in cands))
        useful = {t for t in q - common if not t.isdigit() or len(t) >= 2}
        ph = {p: len(useful & tokens(p)) for p in cands}
        m = max(ph.values())
        if m > 0:
            cands, narrowed = [p for p in cands if ph[p] == m], True
    if len(cands) == 1:  # which-one/2: the one file left, even when search ranked another file first
        return ("answer", {"path": cands[0], "why": "the question says which one"})
    rank = {p: i for i, p in enumerate(ranked)}
    shown = sorted(cands, key=lambda p: (rank.get(p, 10 ** 6), p))[:SHOW]
    return ("ask", {"groups": [{"pattern": g, "count": len(ix.members[g])} for g in groups],
                    "candidates": len(cands), "shown": shown, "narrowed": narrowed})


def message(info):
    """What the harness says instead of guessing."""
    head = "; ".join("%s files look like %s" % (format(g["count"], ","), g["pattern"]) for g in info["groups"])
    return "%s. Which one? Closest: %s" % (head, ", ".join(info["shown"]))


def covers(info, target):
    """Could the person's reply reach the target? In the shown list, or in a group the question was asked about."""
    if target in info["shown"]:
        return "shown"
    if any(group_key(target) == g["pattern"] for g in info["groups"]):
        return "in group"
    return "no"


_CACHE = {}
_CACHE_LOCK = threading.Lock()


def _index_for(lexical_database, catalog=None):
    """One Index per word index and catalog, rebuilt when either file changes (sync writes the catalog)."""
    files = [Path(lexical_database).resolve(strict=True)]
    if catalog is not None:
        files.append(Path(catalog).resolve(strict=True))
    key = tuple(files)
    sig = tuple((st.st_mtime_ns, st.st_size) for st in map(os.stat, files))
    with _CACHE_LOCK:
        old = _CACHE.get(key)
        if old and old[0] == sig:
            return old[1]
        ix = Index(files[0], files[1] if catalog is not None else None)
        _CACHE[key] = (sig, ix)  # a replaced Index closes when no caller holds it any more
        return ix


def ranked_files(rows):
    """The search's results as a list of files, best first (a code name counts as its file)."""
    out = []
    for r in rows:
        p = r.get("source_path")
        if r.get("object_type") == "folder" or not p or p in out:
            continue
        out.append(p)
    return out


def which_one(query, rows, lexical_database, catalog=None):
    """Answer with the best match, or ask which one when look-alike files compete. Never calls a model."""
    ranked = ranked_files(rows)
    ix = _index_for(lexical_database, catalog)
    with ix._lock:
        action, info = decide(query, ranked, ix)
    out = {"action": action, "rule": RULE, "model_calls": 0, "results": ranked[:K]}
    if action == "ask":
        out.update(info, message=message(info))
    else:
        out.update(info)
    return out
