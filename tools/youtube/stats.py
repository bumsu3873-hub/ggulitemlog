"""세꿀사 쇼츠 조회수 정리.

사용법: python tools/youtube/stats.py
채널의 모든 영상을 받아 올린 시각(한국 시간), 조회수, 좋아요, 댓글을
stats/쇼츠통계_날짜.csv 로 저장하고 시간대·요일별 요약을 출력한다.
매일 같은 시각에 돌리면 '올린 지 며칠째 조회수'를 비교할 수 있다.
"""
import csv
import datetime as dt
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from upload import get_youtube, KST, ROOT  # noqa: E402

OUT = ROOT / "stats"
DAYS = "월화수목금토일"


def all_videos(yt):
    ch = yt.channels().list(part="contentDetails", mine=True).execute()
    pl = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, token = [], None
    while True:
        r = yt.playlistItems().list(part="contentDetails", playlistId=pl,
                                    maxResults=50, pageToken=token).execute()
        ids += [i["contentDetails"]["videoId"] for i in r["items"]]
        token = r.get("nextPageToken")
        if not token:
            break
    vids = []
    for i in range(0, len(ids), 50):
        vids += yt.videos().list(part="snippet,statistics,status",
                                 id=",".join(ids[i:i + 50])).execute()["items"]
    return vids


def main():
    yt = get_youtube()
    now = dt.datetime.now(KST)
    rows = []
    for v in all_videos(yt):
        s, stt = v["snippet"], v["statistics"]
        pub = dt.datetime.fromisoformat(s["publishedAt"].replace("Z", "+00:00")).astimezone(KST)
        status = v["status"]
        if status.get("publishAt"):  # 예약 영상은 공개 예정 시각
            pub = dt.datetime.fromisoformat(status["publishAt"].replace("Z", "+00:00")).astimezone(KST)
        views = int(stt.get("viewCount", 0))
        age = (now - pub).total_seconds() / 86400
        rows.append({
            "id": v["id"],
            "올린시각": pub.strftime("%Y-%m-%d %H:%M"),
            "요일": DAYS[pub.weekday()],
            "시": pub.hour,
            "경과일": round(age, 1),
            "조회수": views,
            "좋아요": int(stt.get("likeCount", 0)),
            "댓글": int(stt.get("commentCount", 0)),
            "하루평균": round(views / age, 1) if age >= 1 else "",
            "상태": status.get("privacyStatus"),
            "제목": s["title"],
        })
    rows.sort(key=lambda r: r["올린시각"])
    OUT.mkdir(exist_ok=True)
    path = OUT / f"쇼츠통계_{now:%Y-%m-%d}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # 매시간 기록을 한 파일에 쌓는다 → '올린 지 24시간 뒤 조회수'를 나중에 찾을 수 있다
    hist = OUT / "조회수기록.csv"
    new = not hist.exists()
    with open(hist, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["기록시각", "id", "올린시각", "올린지_시간", "조회수", "좋아요", "제목"])
        for r in rows:
            if r["상태"] == "public":
                w.writerow([now.strftime("%Y-%m-%d %H:%M"), r["id"], r["올린시각"],
                            round(r["경과일"] * 24, 1), r["조회수"], r["좋아요"], r["제목"]])

    pub_rows = [r for r in rows if r["상태"] == "public" and r["경과일"] >= 1]
    for label, key in (("시간대", "시"), ("요일", "요일")):
        print(f"\n[{label}별 조회수 중앙값]")
        groups = {}
        for r in pub_rows:
            groups.setdefault(r[key], []).append(r["조회수"])
        for k in sorted(groups, key=lambda x: DAYS.index(x) if key == "요일" else x):
            g = groups[k]
            print(f"  {k}{'시' if key == '시' else ''}: {len(g)}개, 중앙값 {st.median(g):.0f}, 평균 {st.mean(g):.0f}")
    print(f"\n저장: {path}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        OUT.mkdir(exist_ok=True)
        with open(OUT / "오류.log", "a", encoding="utf-8") as f:
            f.write(f"\n[{dt.datetime.now(KST):%Y-%m-%d %H:%M}]\n{traceback.format_exc()}")
        raise
