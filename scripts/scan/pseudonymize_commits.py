#!/usr/bin/env python3
"""Replace personal names and emails in per-commit CSVs with salted pseudonyms, keeping bot and agent identities."""
import argparse
import csv
import hashlib
import os
import re
import sys

csv.field_size_limit(sys.maxsize)

# identities that are marker evidence or tooling rather than a person: kept verbatim so the
# marker review of a commit reads the same as on GitHub. Matched exactly, never by name
# prefix, since "Claude", "Devin" and "Jules" are also people's first names.
TOOL_EMAILS = {"noreply@github.com", "noreply@anthropic.com", "noreply@openai.com",
               "cursoragent@cursor.com", "copilot@github.com", "actions@github.com",
               "action@github.com", "github-actions@github.com"}
TOOL_EMAIL = re.compile(r"\[bot\]|^\d+\+copilot@users\.noreply\.github\.com$", re.I)
# tools that sign a commit or trailer with a bare name and no email
TOOL_NAMES = {"sourcery ai", "claude"}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# "Co-authored-by: Name <email>" and the other trailers naming a person, email optional
TRAILER = re.compile(r"^((?:co-?auth\w*|authored|signed-off|reviewed|reported|acked|tested"
                     r"|suggested|helped)-by:[ \t]*)([^\n]*)$", re.I | re.M)
ADDRESS = re.compile(r"(.*?)[ \t]*<([^>]*)>?(.*)$")
PEOPLE = [("author_name", "author_email"), ("committer_name", "committer_email")]


def pseudonym(salt, email):
    return "u" + hashlib.sha256((salt + email.strip().lower()).encode()).hexdigest()[:16]


def keep(name, email):
    e = email.strip().lower()
    return (e in TOOL_EMAILS or bool(TOOL_EMAIL.search(e)) or "[bot]" in name.lower()
            or (not e and name.strip().lower() in TOOL_NAMES))


def identity(salt, name, email):
    """(name, email) of one person; an empty email stays empty, so it still counts as one."""
    if keep(name, email):
        return name, email
    if not email:
        return (pseudonym(salt, "name:" + name) if name else name), email
    p = pseudonym(salt, email)
    return p, p + "@users.invalid"


def text(salt, s):
    def trailer(m):
        a = ADDRESS.match(m.group(2))
        name, email, rest = (a.group(1), a.group(2), a.group(3)) if a else (m.group(2).strip(), "", "")
        if keep(name, email):
            return m.group(0)
        n, e = identity(salt, name, email)
        return m.group(1) + n + (f" <{e}>" if a else "") + rest

    s = TRAILER.sub(trailer, s)
    return EMAIL.sub(lambda m: m.group(0) if keep("", m.group(0)) or m.group(0).endswith("@users.invalid")
                     else pseudonym(salt, m.group(0)) + "@users.invalid", s)


def rewrite(salt, src, dst):
    with open(src, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        fields = list(rows[0].keys()) if rows else []
    for r in rows:
        for n, e in PEOPLE:
            r[n], r[e] = identity(salt, r[n], r[e])
        for c in ("subject", "message"):
            r[c] = text(salt, r[c])
    with open(dst, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", nargs="?", help="directory of per-repository commit CSVs")
    p.add_argument("output", nargs="?", help="directory to write the pseudonymised copies to")
    p.add_argument("-s", "--salt", default=os.environ.get("PSEUDONYM_SALT", ""),
                   help="secret salt, default $PSEUDONYM_SALT; never publish it")
    p.add_argument("-r", "--repos", default="", help="file of owner/name lines to restrict to")
    args = p.parse_args()
    if not args.input or not args.output:
        p.print_help()
        sys.exit(1)
    if not args.salt:
        sys.exit("no salt: pass -s or set PSEUDONYM_SALT")
    only = None
    if args.repos:
        only = {l.strip().replace("/", "__") + ".csv" for l in open(args.repos) if l.strip()}
    os.makedirs(args.output, exist_ok=True)
    names = sorted(n for n in os.listdir(args.input) if n.endswith(".csv") and (only is None or n in only))
    for n in names:
        rewrite(args.salt, os.path.join(args.input, n), os.path.join(args.output, n))
    print(f"{args.output}  {len(names)} files", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
