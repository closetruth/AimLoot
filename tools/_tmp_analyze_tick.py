import math
import sys

sys.path.insert(0, "tools")
import gen_slot_sounds as g

x = g._decode_source("tools/ref/tick_tock_source.mp3")
sr = g.SR
n = len(x)
rms = math.sqrt(sum(v * v for v in x) / n)
print("dur %.3f  rms %.4f (%.1f dBFS)  peak %.3f" % (n / sr, rms, 20 * math.log10(rms + 1e-12), max(abs(v) for v in x)))
tail = x[-int(0.2 * sr):]
print("tail rms %.4f  tail peak %.4f" % (math.sqrt(sum(v * v for v in tail) / len(tail)), max(abs(v) for v in tail)))
ev = g._find_events(x)
on = [a / sr for a, b in ev]
print("events: %d" % len(ev))
print("onsets:", [round(t, 3) for t in on])
print("d1:", [round(on[i + 1] - on[i], 3) for i in range(len(on) - 1)])
print("d2:", [round(on[i + 2] - on[i], 3) for i in range(len(on) - 2)])
for i, (a, b) in enumerate(ev):
    seg = x[a:b]
    print("%2d  t=%.3f len=%.3f pk=%.4f split=%.3f" % (i, on[i], (b - a) / sr, max(abs(v) for v in seg), g._spectral_split(x, a)))
print("first %.3f last %.3f gap_to_end %.3f" % (on[0], on[-1], n / sr - on[-1]))