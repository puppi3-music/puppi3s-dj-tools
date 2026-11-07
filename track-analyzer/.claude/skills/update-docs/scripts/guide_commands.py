"""Feed every command in docs/GUIDE.html (<code>/<pre>) through the real CLI parser; print the ones it rejects.
Placeholders (X, a|b|c, shell pipes) show up as false rejections: read the reason. Run from track-analyzer/:
uv run python .claude/skills/update-docs/scripts/guide_commands.py"""
import argparse,re,html,shlex,sys,io,contextlib
cap={}
class Stop(Exception): pass
def grab(self,*a,**k): cap['ap']=self; raise Stop
orig=argparse.ArgumentParser.parse_args; argparse.ArgumentParser.parse_args=grab
from dj_track_analyzer import cli
try: cli.main()
except Stop: pass
ap=cap['ap']; argparse.ArgumentParser.parse_args=orig
g=open('docs/GUIDE.html').read()
snips=re.findall(r'<(?:code|pre)>(.*?)</(?:code|pre)>',g,re.S)
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
