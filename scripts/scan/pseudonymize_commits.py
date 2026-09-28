#!/usr/bin/env python3
import argparse
import csv
import hashlib
import os
import re
import sys

csv.field_size_limit(sys.maxsize)

# Tool and bot identities are marker evidence and stay verbatim. Exact matches only:
# Claude, Devin and Jules are also first names.
TOOL_EMAILS = {"noreply@github.com", "noreply@anthropic.com", "noreply@openai.com",
               "cursoragent@cursor.com", "copilot@github.com", "actions@github.com",
               "action@github.com", "github-actions@github.com", "support@github.com",
               "git@github.com"}
TOOL_EMAIL = re.compile(r"\[bot\]|^\d+\+(copilot|cursoragent)@users\.noreply\.github\.com$", re.I)
# Tools that sign with a bare name and no email.
TOOL_NAMES = {"sourcery ai", "claude"}
# A tool name is kept only with an address on the tool's own domain, or with none.
TOOL_NAME = re.compile(r"^(claude|codex|copilot|cursor|devin|gemini|gemini-code-assist|aider|openhands"
                       r"|windsurf|jules)( (opus|sonnet|haiku|code|agent|ai)\b.*| \(.*\))?$", re.I)
TOOL_DOMAINS = {"anthropic.com", "openai.com", "cursor.com", "aider.chat", "all-hands.dev",
                "google.com", "github.com"}
# Tool and bot accounts, kept as @-mentions and handles.
TOOL_HANDLES = {"dependabot", "renovate", "copilot", "claude", "codex", "cursor", "devin",
                "devin-ai-integration", "jules", "google-labs-jules", "gemini-code-assist",
                "coderabbitai", "sourcery-ai", "greptile-apps", "cubic-dev-ai",
                "chatgpt-codex-connector", "openhands", "openhands-agent", "sweep-ai",
                "codegen-sh", "ellipsis-dev", "deepsource-autofix", "pre-commit-ci",
                "mergifyio", "github-actions", "microsoft-github-policy-service"}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# "Name <email>" anywhere: the name runs back to a colon, a previous address or line start.
NAMED = re.compile(r"(?P<pre>(?:^|:|\(|(?<=>)[ \t]*[,;]?)[ \t]*(?:[-*>][ \t]*)*)"
                   r"(?P<name>(?:[^\n<>:()]|\([^()\n]*\)|<[^<>@\n]*>)*?)[ \t]*"
                   r"<(?P<email>[^<>\s@]+@[^<>\s]+)>", re.M)
# Trailers naming a person, also indented or bulleted.
TRAILER = re.compile(r"^([ \t]*(?:[-*>][ \t]*)*(?:(?:co-?auth\w*|authored|signed-off|reviewed"
                     r"|reported|acked|tested|suggested|helped)[- ]by|author):[ \t]*)([^\n]*)$",
                     re.I | re.M)
ADDRESS = re.compile(r"(.*?)[ \t]*<([^>]*)>?(.*)$")
# Handles in @-mentions, merge sources and profile links; owner/repo URLs are kept.
MENTION = re.compile(r"(?<![\w.@/+-])@([A-Za-z0-9][A-Za-z0-9-]{0,38})(?![\w@/-]|\.\w)")
MERGED = re.compile(r"(merge pull request #\d+ from |merge (?:remote-tracking )?branch '[^'\n]*' "
                    r"of \S*?github\.com[/:])([A-Za-z0-9-]+)(?=/)", re.I)
PROFILE = re.compile(r"(github\.com/)([A-Za-z0-9-]+)(?![\w/.-])", re.I)
PSEUDONYM = re.compile(r"[uh][0-9a-f]{16}")
PEOPLE = [("author_name", "author_email"), ("committer_name", "committer_email")]


def digest(s):
    return hashlib.sha256(s.strip().strip("\"'“”").lower().encode()).hexdigest()[:16]


def pseudonym(email):
    return "u" + digest(email)


def handle(h):
    return "h" + digest(h)


def keep(name, email):
    e = email.strip().lower()
    return (e in TOOL_EMAILS or bool(TOOL_EMAIL.search(e)) or "[bot]" in name.lower()
            or (not e and name.strip().lower() in TOOL_NAMES)
            or (bool(TOOL_NAME.match(name.strip())) and (not e or e.rsplit("@", 1)[-1] in TOOL_DOMAINS)))


def keep_handle(h, owner):
    h = h.lower()
    return h == owner or h in TOOL_HANDLES or h.endswith("-bot")


def identity(name, email):
    if keep(name, email):
        return name, email
    if not email:
        return (pseudonym(name) if name else name), email
    p = pseudonym(email)
    return p, p + "@users.invalid"


def text(s, owner):
    kept = set()  # addresses of tool identities kept whole, which the email pass must not hash

    def named(m):
        name, email = m.group("name"), m.group("email")
        if email.endswith("@users.invalid") or keep(name, email):
            kept.add(email.lower())
            return m.group(0)
        p = pseudonym(email)
        return m.group("pre") + (p + " " if name else "") + f"<{p}@users.invalid>"

    def trailer(m):
        value = m.group(2).strip()
        a = ADDRESS.match(value)
        name, email = (a.group(1).strip(), a.group(2).strip()) if a else (value, "")
        if not value or PSEUDONYM.fullmatch(name) or keep(name, email if "@" in email else ""):
            return m.group(0)
        e = re.sub(r"[\s\"'“”]", "", email)
        if "@" in e:
            p = pseudonym(e)
            return m.group(1) + f"{p} <{p}@users.invalid>"
        return m.group(1) + identity(value, "")[0]

    s = NAMED.sub(named, s)
    s = TRAILER.sub(trailer, s)
    s = EMAIL.sub(lambda m: m.group(0) if keep("", m.group(0)) or m.group(0).lower() in kept
                  or m.group(0).endswith("@users.invalid") else pseudonym(m.group(0)) + "@users.invalid", s)
    s = MENTION.sub(lambda m: m.group(0) if keep_handle(m.group(1), owner)
                    else "@" + handle(m.group(1)), s)
    for pattern in (MERGED, PROFILE):
        s = pattern.sub(lambda m: m.group(0) if keep_handle(m.group(2), owner)
                        else m.group(1) + handle(m.group(2)), s)
    return s


def rewrite(src, dst):
    with open(src, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        fields = list(rows[0].keys()) if rows else []
    for r in rows:
        owner = r["repo"].split("/")[0].lower()
        for n, e in PEOPLE:
            r[n], r[e] = identity(r[n], r[e])
        for c in ("subject", "message"):
            r[c] = text(r[c], owner)
    with open(dst, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main():
    p = argparse.ArgumentParser(
        description="Hash personal names, emails and GitHub handles in per-commit CSVs.")
    p.add_argument("input", nargs="?", help="directory of per-repository commit CSVs")
    p.add_argument("output", nargs="?", help="directory to write the pseudonymised copies to")
    p.add_argument("-r", "--repos", default="", help="file of owner/name lines to restrict to")
    args = p.parse_args()
    if not args.input or not args.output:
        p.print_help()
        sys.exit(1)
    only = None
    if args.repos:
        only = {l.strip().replace("/", "__") + ".csv" for l in open(args.repos) if l.strip()}
    os.makedirs(args.output, exist_ok=True)
    names = sorted(n for n in os.listdir(args.input) if n.endswith(".csv") and (only is None or n in only))
    for n in names:
        rewrite(os.path.join(args.input, n), os.path.join(args.output, n))
    print(f"{args.output}  {len(names)} files", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
