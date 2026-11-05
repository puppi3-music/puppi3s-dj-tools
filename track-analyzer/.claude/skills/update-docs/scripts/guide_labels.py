"""Bold names in docs/GUIDE.html (<b>…</b>, split on → and ›) that don't appear in the page (web/index.html, web/js,
web.py). ${...} in the page counts as a wildcard, so "Load into Traktor…" matches `Load into ${DJ()}…`.
Prints candidates; prose labels ("On a Mac:", "Tip:") are expected noise. Run from track-analyzer/:
python3 .claude/skills/update-docs/scripts/guide_labels.py"""
import glob, html, re

W = "src/dj_track_analyzer/web"
page = html.unescape("".join(open(f, encoding="utf-8").read()
                             for f in [f"{W}/index.html", "src/dj_track_analyzer/web.py", *glob.glob(f"{W}/js/*.js")]))
page = page.replace("\\u2026", "…")
templ = [re.compile(re.escape(t).replace(re.escape("§"), ".{1,40}"))
         for t in set(re.sub(r"\$\{[^}]*\}", "§", m) for m in re.findall(r"[^`\"<>]*\$\{[^`\"<>]*", page))
         if len(t.replace("§", "").strip()) >= 6]
guide = open("docs/GUIDE.html", encoding="utf-8").read()
seen = set()
for b in re.findall(r"<b>(.*?)</b>", guide, re.S):
    for part in re.split(r"→|›", html.unescape(re.sub("<[^>]+>", "", b))):
        t = part.strip().rstrip(":")
        if len(t) < 3 or t in seen or t in page:
            continue
        seen.add(t)
        if not any(r.fullmatch(t) for r in templ):
            print(t)
