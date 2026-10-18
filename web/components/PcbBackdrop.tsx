"use client";

/**
 * PCB 电路背景：纯 SVG + CSS 动画，零依赖。
 *
 * 走线坐标是写死的（不用随机），避免 SSR / 客户端渲染不一致导致 hydration 报错。
 * 信号沿走线流动用 stroke-dashoffset 动画，每条错开 delay，看起来像板子在上电自检。
 */

// 主干走线：PCB 风格（水平 / 垂直 + 45° 折角）
const TRACES = [
  "M -40 88 H 200 L 262 150 H 640 L 702 88 H 1060 L 1122 150 H 1500",
  "M -40 262 H 322 L 384 324 H 780 L 842 262 H 1500",
  "M -40 470 H 150 L 212 532 H 560 L 622 470 H 1000 L 1062 532 H 1500",
  "M -40 682 H 420 L 482 744 H 900 L 962 682 H 1500",
  "M 300 -40 V 180 L 362 242 V 470",
  "M 712 -40 V 220 L 650 282 V 520",
  "M 1104 -40 V 160 L 1166 222 V 720",
  "M 496 940 V 700 L 434 638 V 400",
  "M 880 -40 V 120 L 942 182 V 380 L 880 442 V 940",
  "M 140 -40 V 300 L 202 362 V 940",
];

// 焊盘 / 过孔：[x, y, r]
const PADS: [number, number, number][] = [
  [200, 88, 7], [262, 150, 7], [640, 150, 7], [702, 88, 7], [1060, 88, 7], [1122, 150, 7],
  [322, 262, 7], [384, 324, 7], [780, 324, 7], [842, 262, 7],
  [150, 470, 7], [212, 532, 7], [560, 532, 7], [622, 470, 7], [1000, 470, 7], [1062, 532, 7],
  [420, 682, 7], [482, 744, 7], [900, 744, 7], [962, 682, 7],
  [300, 180, 6], [362, 242, 6], [712, 220, 6], [650, 282, 6],
  [1104, 160, 6], [1166, 222, 6], [496, 700, 6], [434, 638, 6],
  [942, 182, 5], [202, 362, 5], [880, 442, 5],
  // 过孔阵列
  [560, 200, 3.5], [600, 200, 3.5], [640, 200, 3.5], [680, 200, 3.5],
  [560, 240, 3.5], [600, 240, 3.5], [640, 240, 3.5], [680, 240, 3.5],
  [1180, 560, 3.5], [1220, 560, 3.5], [1260, 560, 3.5],
  [1180, 600, 3.5], [1220, 600, 3.5], [1260, 600, 3.5],
];

// 芯片封装：[x, y, w, h, 引脚数]
const CHIPS: [number, number, number, number, number][] = [
  [620, 380, 190, 130, 8],
  [170, 556, 140, 96, 6],
  [1000, 596, 150, 110, 7],
];

export default function PcbBackdrop() {
  return (
    <div className="pcb-bg" aria-hidden="true">
      <svg
        viewBox="0 0 1440 900"
        preserveAspectRatio="xMidYMid slice"
        xmlns="http://www.w3.org/2000/svg"
      >
        <defs>
          <pattern id="pcb-grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="var(--pcb-grid)" strokeWidth="1" />
          </pattern>
          <linearGradient id="pcb-vignette" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--pcb-fade)" stopOpacity="0" />
            <stop offset="45%" stopColor="var(--pcb-fade)" stopOpacity="0.55" />
            <stop offset="100%" stopColor="var(--pcb-fade)" stopOpacity="0.9" />
          </linearGradient>
        </defs>

        <rect width="1440" height="900" fill="url(#pcb-grid)" />

        {/* 走线底层 */}
        <g fill="none" stroke="var(--pcb-trace)" strokeWidth="2.5" strokeLinecap="round">
          {TRACES.map((d, i) => (
            <path key={`t${i}`} d={d} />
          ))}
        </g>

        {/* 信号流动：同一批走线上叠一层发光虚线 */}
        <g fill="none" stroke="var(--pcb-signal)" strokeWidth="3" strokeLinecap="round">
          {TRACES.map((d, i) => (
            <path
              key={`s${i}`}
              d={d}
              className="pcb-flow"
              style={{ animationDelay: `${i * 1.15}s`, animationDuration: `${7 + (i % 4) * 1.6}s` }}
            />
          ))}
        </g>

        {/* 焊盘与过孔 */}
        <g>
          {PADS.map(([cx, cy, r], i) => (
            <g key={`p${i}`}>
              <circle cx={cx} cy={cy} r={r} fill="var(--pcb-pad)" />
              <circle cx={cx} cy={cy} r={r * 0.42} fill="var(--pcb-pad-hole)" />
            </g>
          ))}
        </g>

        {/* 芯片封装 */}
        <g>
          {CHIPS.map(([x, y, w, h, pins], i) => (
            <g key={`c${i}`}>
              <rect x={x} y={y} width={w} height={h} rx="8" fill="var(--pcb-chip)" stroke="var(--pcb-trace)" strokeWidth="2" />
              <rect x={x + 14} y={y + 14} width={w - 28} height={h - 28} rx="4" fill="none" stroke="var(--pcb-trace)" strokeWidth="1" opacity="0.6" />
              {Array.from({ length: pins }).map((_, k) => {
                const step = (h - 20) / pins;
                const py = y + 10 + step * (k + 0.5);
                return (
                  <g key={k}>
                    <rect x={x - 12} y={py - 3} width={12} height={6} rx="1.5" fill="var(--pcb-pad)" />
                    <rect x={x + w} y={py - 3} width={12} height={6} rx="1.5" fill="var(--pcb-pad)" />
                  </g>
                );
              })}
              {/* 极性点 */}
              <circle cx={x + 22} cy={y + 22} r="5" fill="var(--pcb-pad-hole)" opacity="0.7" />
            </g>
          ))}
        </g>

        {/* 顶部压暗，保证内容区可读 */}
        <rect width="1440" height="900" fill="url(#pcb-vignette)" />
      </svg>
    </div>
  );
}
