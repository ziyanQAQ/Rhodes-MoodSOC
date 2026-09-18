"""对照：段的副本位置 vs 轨迹速率（找 42~48 段为什么对不上）。"""
from decimal import Decimal
from data.paths import MAA_SAMPLE
from store.session import Session

D = Decimal
N = "龙舌兰"
s = Session()
s.load_paths([MAA_SAMPLE])
s.cycles = 2
s.idle_to_dorm = True
s.recompute()
tr = s.traj
print("段：")
for a, b, w in tr.segments:
    f = w.facility_of(N)
    print(f"  [{a} ~ {b})  {N} 在 {f.display_name if f else '不在基建'}  id={id(w)}  "
          f"宿舍#4={[o.name for x in w.facilities if x.display_name == '宿舍#4' for o in x.operators]}")
print()
print("rate_at:")
for t in ("41", "42", "43", "45", "47"):
    print(f"  t={t:>3}  rate={tr.rate_at(N, D(t))}  mood={tr.mood_at(N, D(t))}")
print()
print("idle 标记（t>=36）：")
for m in tr.marks:
    if m.kind == "idle" and m.t >= D(36):
        print("   ", m.t, m.label[:120])
