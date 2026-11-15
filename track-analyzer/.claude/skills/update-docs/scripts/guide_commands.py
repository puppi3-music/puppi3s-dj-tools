"""Feed every command in docs/GUIDE.html (<code>/<pre>), or in a Markdown file given as argument (`code`, ``` blocks),
through the real CLI parser; print the ones it rejects.
Placeholders (X, a|b|c, shell pipes) show up as false rejections: read the reason. Run from track-analyzer/:
uv run python .claude/skills/update-docs/scripts/guide_commands.py [README.md]"""
import argparse,re,html,shlex,sys,io,contextlib
cap={}
class Stop(Exception): pass
def grab(self,*a,**k): cap['ap']=self; raise Stop
orig=argparse.ArgumentParser.parse_args; argparse.ArgumentParser.parse_args=grab
from dj_track_analyzer import cli
try: cli.main()
except Stop: pass
ap=cap['ap']; argparse.ArgumentParser.parse_args=orig
path=sys.argv[1] if len(sys.argv)>1 else 'docs/GUIDE.html'
g=open(path).read()
if path.endswith('.md'):  # fenced blocks first, then inline code (table pipes escaped as \|)
  snips=re.findall(r'```[a-z]*\n(.*?)```',g,re.S); g=re.sub(r'```.*?```','',g,flags=re.S)
  snips+=[c.replace('\\|','|') for c in re.findall(r'`([^`\n]+)`',g)]
  snips=[html.escape(x) for x in snips]
else: snips=re.findall(r'<(?:code|pre)>(.*?)</(?:code|pre)>',g,re.S)
cmds=set(ap._subparsers._group_actions[0].choices)
bad=0
for s in snips:
  for line in html.unescape(re.sub('<[^>]+>','',s)).splitlines():
    line=re.sub(r'^.*?dj-track-analyzer\s+','',line.strip())
    if not line or line.split()[0] not in cmds or '--' not in line and len(line.split())<2: continue
    line=re.sub(r'<[^>]*>','X',line).replace('…','X').split(' #')[0]
    try: toks=shlex.split(line)
    except ValueError: continue
    if toks[:2]==['clips','describe']: toks=['clips-describe',*toks[2:]]  # as cli.main() routes it
    e=io.StringIO()
    try:
      with contextlib.redirect_stderr(e): ap.parse_args(toks)
    except SystemExit as x:
      if x.code: bad+=1; print('BAD:',line,'|',e.getvalue().strip().splitlines()[-1][:120])
print('rejected',bad)
