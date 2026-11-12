"""세꿀사 쇼츠 예약 업로드.

사용법:
  1. 판다랭크에서 받은 영상을 upload-queue 폴더에 '번호.mp4'로 넣는다. 예: 56.mp4
  2. kits 폴더의 해당 번호 키트에 '## 유튜브 업로드' 블록이 있어야 한다.
  3. python tools/youtube/upload.py        (실제 업로드)
     python tools/youtube/upload.py --dry  (업로드 없이 무엇을 할지만 출력)

공개 시각은 유튜브 서버가 맞춰 공개하므로, 그 시간에 PC가 켜져 있을 필요는 없다.
업로드가 끝난 영상은 upload-queue/done 으로 옮긴다.
"""
import argparse
import datetime as dt
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
QUEUE = ROOT / "upload-queue"
KITS = ROOT / "kits"
SECRET = HERE / "client_secret.json"
TOKEN = HERE / "token.json"
CONFIG = HERE / "config.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]
KST = dt.timezone(dt.timedelta(hours=9))


def load_config():
    cfg = {"slots": ["19:00"], "category_id": "26", "synthetic_media": True}
    if CONFIG.exists():
        cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
    return cfg


def parse_kit(num):
    """kits/*-번호.md 에서 '## 유튜브 업로드' 블록을 읽는다."""
    files = sorted(KITS.glob(f"*-{num}.md"))
    if not files:
        raise SystemExit(f"{num}번 키트가 없어요: kits/*-{num}.md")
    text = files[-1].read_text(encoding="utf-8")
    m = re.search(r"^## 유튜브 업로드\s*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    if not m:
        raise SystemExit(f"{files[-1].name}에 '## 유튜브 업로드' 블록이 없어요")
    block, info, key = m.group(1), {}, None
    for line in block.splitlines():
        kv = re.match(r"^(제목|태그|공개|설명):\s*(.*)$", line)
        if kv:
            key = kv.group(1)
            info[key] = kv.group(2).strip()
        elif key == "설명":
            info[key] += "\n" + line
    info["설명"] = info.get("설명", "").strip()
    if not info.get("제목"):
        raise SystemExit(f"{files[-1].name}: 제목이 비어 있어요")
    return files[-1].name, info


def next_slots(cfg, taken, count):
    """이미 잡힌 시각(taken)을 피해서 설정된 시간대 중 가장 가까운 빈 시각을 고른다."""
    now = dt.datetime.now(KST) + dt.timedelta(minutes=30)
    out, day = [], now.date()
    while len(out) < count:
        for s in cfg["slots"]:
            h, mi = map(int, s.split(":"))
            t = dt.datetime(day.year, day.month, day.day, h, mi, tzinfo=KST)
            if t > now and t not in taken:
                out.append(t)
                taken.add(t)
                if len(out) == count:
                    break
        day += dt.timedelta(days=1)
    return out


def get_youtube():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not SECRET.exists():
                raise SystemExit(f"client_secret.json이 없어요: {SECRET}")
            flow = InstalledAppFlow.from_client_secrets_file(str(SECRET), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds)


def scheduled_times(yt):
    """채널에 이미 예약된 영상들의 공개 시각."""
    ch = yt.channels().list(part="contentDetails", mine=True).execute()
    uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    items = yt.playlistItems().list(part="contentDetails", playlistId=uploads,
                                    maxResults=50).execute().get("items", [])
    ids = [i["contentDetails"]["videoId"] for i in items]
    taken = set()
    if ids:
        vids = yt.videos().list(part="status", id=",".join(ids)).execute()["items"]
        for v in vids:
            p = v["status"].get("publishAt")
            if p:
                taken.add(dt.datetime.fromisoformat(p.replace("Z", "+00:00")).astimezone(KST))
    return taken


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="업로드하지 않고 계획만 출력")
    args = ap.parse_args()
    cfg = load_config()

    videos = sorted(QUEUE.glob("*.mp4"), key=lambda p: int(p.stem) if p.stem.isdigit() else 0)
    videos = [v for v in videos if v.stem.isdigit()]
    if not videos:
        print("upload-queue에 '번호.mp4' 영상이 없어요.")
        return

    plans = []
    for v in videos:
        kit, info = parse_kit(v.stem)
        plans.append((v, kit, info))

    yt = None if args.dry else get_youtube()
    taken = set() if args.dry else scheduled_times(yt)
    fixed = []
    for _, _, info in plans:
        if info.get("공개"):
            t = dt.datetime.strptime(info["공개"], "%Y-%m-%d %H:%M").replace(tzinfo=KST)
            fixed.append(t)
            taken.add(t)
    auto = iter(next_slots(cfg, taken, sum(1 for p in plans if not p[2].get("공개"))))

    for v, kit, info in plans:
        when = (dt.datetime.strptime(info["공개"], "%Y-%m-%d %H:%M").replace(tzinfo=KST)
                if info.get("공개") else next(auto))
        title = info["제목"][:100]
        desc = info["설명"]
        if "#Shorts" not in title and "#Shorts" not in desc:
            desc += "\n\n#Shorts"
        tags = [t.strip().lstrip("#") for t in info.get("태그", "").split(",") if t.strip()]
        print(f"[{v.name}] {kit}\n  제목: {title}\n  공개: {when:%Y-%m-%d %H:%M} (KST)\n  태그: {tags}")
        if args.dry:
            continue

        from googleapiclient.http import MediaFileUpload
        body = {
            "snippet": {"title": title, "description": desc, "tags": tags,
                        "categoryId": cfg["category_id"], "defaultLanguage": "ko"},
            "status": {"privacyStatus": "private",
                       "publishAt": when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "selfDeclaredMadeForKids": False,
                       "containsSyntheticMedia": cfg["synthetic_media"]},
        }
        req = yt.videos().insert(part="snippet,status", body=body,
                                 media_body=MediaFileUpload(str(v), chunksize=-1, resumable=True))
        resp = None
        while resp is None:
            _, resp = req.next_chunk()
        print(f"  완료: https://youtube.com/shorts/{resp['id']}")
        done = QUEUE / "done"
        done.mkdir(exist_ok=True)
        shutil.move(str(v), str(done / v.name))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
