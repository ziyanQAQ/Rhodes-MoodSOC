import sys
from decimal import Decimal
sys.path.insert(0, ".")
from store.session import Session

s = Session()
s.load_layout({"facilities": [
    {"type": "制造站", "level": 3, "operators": ["泡泡", "普通甲", "普通乙"]},
    {"type": "宿舍", "level": 5, "operators": []},
]}, hours=24)
s.set_initial_moods({"泡泡": 10})
s.set_detached(["泡泡"], recompute=True)      # 摘位置 + 进名单
s.idle_to_dorm = True
s.recompute()
print("她在第1班:", s.schedule.shifts[0].world.facility_of("泡泡"), "| bench:", s.bench_names())
print("逐次表:", [(t, [(r[0], str(r[1]), r[2]) for r in rows]) for t, _sc, rows in s.idle_groups()])
print("心情轨迹：")
for t in (Decimal(0), Decimal(1), Decimal(2), Decimal(6), Decimal(24)):
    print("  @%sh = %s  rate=%s" % (t, s.mood_at("泡泡", t), s.rate_at("泡泡", t)))
vals = s.traj.moods["泡泡"]
print("唯一值个数:", len(set(vals)), "| 平线:", len(set(vals)) == 1)
print("入宿标记:", [(str(m.t), m.label) for m in s.traj.marks if m.kind == "idle"])
