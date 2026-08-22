import json, re, unicodedata
from pathlib import Path
from urllib.request import Request, urlopen
from datetime import datetime, timezone

BASE="https://fantasy.premierleague.com/api/"
ROOT=Path(__file__).resolve().parents[1]

def get(path):
    req=Request(BASE+path, headers={"User-Agent":"AI-Fantasy-League/1.0"})
    with urlopen(req, timeout=30) as r:
        return json.load(r)

def norm(s):
    s=unicodedata.normalize("NFKD", s).encode("ascii","ignore").decode().lower()
    s=re.sub(r"[^a-z0-9]","",s)
    aliases={
      "antoninkinsky":"antoninkinsky","cristhianmosquera":"cristhianmosquera",
      "harrymaguire":"harrymaguire","tyrickmitchell":"tyrickmitchell",
      "brunofernandes":"brunofernandes","bryanmbeumo":"bryanmbeumo",
      "pascalgross":"pascalgross","mamadousangare":"mamadousangare",
      "florentino":"florentino","erlinghaaland":"erlinghaaland",
      "joaopedro":"joaopedro","bartverbruggen":"bartverbruggen",
      "bobbythomas":"bobbythomas","jonahkusiasare":"jonahkusiasare",
      "gabriel":"gabriel","christostzolis":"christostzolis",
      "martindubravka":"martindubravka","jacobgreaves":"jacobgreaves",
      "carlosbaleba":"carlosbaleba","elliottanderson":"elliottanderson",
      "sidikicherif":"sidikicherif","markflekken":"markflekken",
      "nobelmendy":"nobelmendy","jeremysarmiento":"jeremysarmiento",
      "riccardocalafiori":"riccardocalafiori","lukeshaw":"lukeshaw",
      "dominikszoboszlai":"dominikszoboszlai","dominickcalvertlewin":"dominickcalvertlewin",
      "kristofferajer":"kristofferajer","daraoshea":"daraoshea",
    }
    return aliases.get(s,s)

def load_squads():
    return json.loads((ROOT/"data/squads.json").read_text(encoding="utf-8"))

def build_index(players):
    idx={}
    for p in players:
        idx.setdefault(norm(p["web_name"]),[]).append(p)
        idx.setdefault(norm(p["first_name"]+" "+p["second_name"]),[]).append(p)
    return idx

def find_player(idx,name):
    hits=idx.get(norm(name),[])
    if len(hits)==1: return hits[0]
    # Prefer exact normalized web_name/second_name; otherwise first unique.
    if hits:
        return hits[0]
    raise KeyError(f"FPL player not found: {name}")

def valid_formation(players):
    c={k:0 for k in ["GK","DEF","MID","FWD"]}
    for p in players:c[p["pos"]]+=1
    return c["GK"]==1 and c["DEF"]>=3 and c["MID"]>=2 and c["FWD"]>=1

def score_team(team, idx, live_by_id):
    # Starting XI and bench are stored with positions.
    all_names=[x for x,_ in team["starting"]+team["bench"]]
    refs=[find_player(idx,n) for n in all_names]
    starts=[]
    for (name,pos),p in zip(team["starting"],refs[:11]):
        starts.append({"name":name,"pos":pos,"p":p,"live":live_by_id[p["id"]]})
    bench=[]
    for (name,pos),p in zip(team["bench"],refs[11:]):
        bench.append({"name":name,"pos":pos,"p":p,"live":live_by_id[p["id"]]})

    # Auto substitutions: first ensure a GK replacement if starting GK did not play;
    # then outfield bench players in order, preserving a valid formation.
    current=list(starts)
    used=set()
    if current[0]["live"]["minutes"]==0 and bench and bench[0]["live"]["minutes"]>0:
        current[0]=bench[0]; used.add(0)
    for bidx,b in enumerate(bench):
        if bidx in used or b["live"]["minutes"]==0: continue
        # Identify a starting outfield player who did not play.
        for sidx,s in enumerate(current[1:],start=1):
            if s["live"]["minutes"]==0:
                trial=[x["pos"] for x in current]
                trial[sidx]=b["pos"]
                if valid_formation([{"pos":x} for x in trial]):
                    current[sidx]=b; used.add(bidx); break

    def raw(x): return int(x["live"]["total_points"])
    total=sum(raw(x) for x in current)

    # Captain/vice-captain based on actual FPL player points; vice takes over only if captain did not play.
    byname={x["name"]:x for x in current}
    cap=byname.get(team["captain"])
    vice=byname.get(team["vice"])
    if cap and cap["live"]["minutes"]>0:
        total += raw(cap)
    elif vice and vice["live"]["minutes"]>0:
        total += raw(vice)
    return total

def main():
    boot=get("bootstrap-static/")
    events=boot["events"]
    # Update every completed/checked Gameweek. Current/unfinished GW is not published as final.
    squads=load_squads()
    idx=build_index(boot["elements"])
    results={}
    history={}
    for ev in events:
        gw=ev["id"]
        if not ev.get("finished") or not ev.get("data_checked"):
            continue
        live=get(f"event/{gw}/live/")
        live_by_id={x["id"]:x for x in live["elements"]}
        scores=[]
        for sid,team in squads.items():
            try:
                pts=score_team(team,idx,live_by_id)
            except Exception as e:
                print(f"Skipping {sid} GW{gw}: {e}")
                pts=0
            scores.append({"id":sid,"name":team["name"],"icon":team["icon"],"points":pts,"captain":team["captain"]})
        history[str(gw)]={"status":"Official FPL data marked finished and checked.","scores":scores}

    totals={sid:0 for sid in squads}
    for g in history.values():
        for x in g["scores"]: totals[x["id"]]+=x["points"]
    leaderboard=[]
    for sid,t in squads.items():
        latest=history.get("1",{}).get("scores",[])
        gw1=next((x["points"] for x in latest if x["id"]==sid),0)
        leaderboard.append({"id":sid,"name":t["name"],"icon":t["icon"],"formation":t["formation"],
                            "captain":t["captain"],"gw1":gw1,"total":totals[sid]})
    out={"status":"Updated from official FPL data.","updated_at":datetime.now(timezone.utc).isoformat(),
         "leaderboard":leaderboard,"gameweeks":history}
    (ROOT/"data/results.json").write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding="utf-8")

if __name__=="__main__": main()
