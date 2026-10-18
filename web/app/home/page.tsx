"use client";

/**
 * 产品主页面：智能硬件产品设计全流程 · 商业化 · 科技风
 *
 * 设计约束（企业级）：
 * - 信息优先：先讲清「做什么 → 怎么交付 → 能拿到什么」，再谈视觉
 * - 主色克制：--accent 只用于阶段序号、关键数字与 CTA；其余交给留白与分割线
 * - 全站复用 globals.css 的设计令牌，明暗主题自动跟随，无硬编码颜色
 * - 所有配图与视频由 Agnes 生成，写实工业风，不出现卡通 / 二次元元素
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import ThemeToggle from "@/components/ThemeToggle";

/* ------------------------------------------------------------------ 数据 */

type Stage = {
  id: string;
  no: string;
  name: string;
  agent: string;
  input: string;
  output: string[];
  img: string;
};

const STAGES: Stage[] = [
  {
    id: "prd",
    no: "01",
    name: "需求定义",
    agent: "主智能体 · 需求澄清",
    input: "一句话产品需求",
    output: ["PRD 文档", "待确认问题清单", "默认假设表"],
    img: "/home/flow-concept.png",
  },
  {
    id: "appearance",
    no: "02",
    name: "外观设计",
    agent: "industrial-designer",
    input: "PRD + 风格约束",
    output: ["外观方案文档", "产品渲染图（Agnes 出图）", "出图提示词存档"],
    img: "/home/flow-appearance.png",
  },
  {
    id: "structure",
    no: "03",
    name: "结构设计",
    agent: "mechanical-engineer",
    input: "外观提示词（同源约束）",
    output: ["STEP / STL 实体模型", "四视图 + 爆炸图", "外观一致性对照表"],
    img: "/home/flow-structure.png",
  },
  {
    id: "hardware",
    no: "04",
    name: "硬件设计",
    agent: "hardware-engineer",
    input: "结构与功能定义",
    output: ["电路原理图", "器件选型与 BOM", "功耗与接口表"],
    img: "/home/flow-electronics.png",
  },
  {
    id: "firmware",
    no: "05",
    name: "固件与仿真",
    agent: "embedded-engineer",
    input: "硬件接口定义",
    output: ["固件框架代码", "仿真与验证结果"],
    img: "/home/verify.png",
  },
  {
    id: "review",
    no: "06",
    name: "交叉评审",
    agent: "reviewer",
    input: "全部阶段产物",
    output: ["评审报告", "风险清单", "返工建议"],
    img: "/home/business.png",
  },
];

const METRICS = [
  { value: "6", unit: "个阶段", label: "需求 → 评审一条流水线" },
  { value: "10", unit: "个工具", label: "建模 / 出图 / 检索 / 文档全真实执行" },
  { value: "4", unit: "类专业角色", label: "ID · 结构 · 电子 · 固件协同" },
  { value: "3", unit: "类工程图", label: "渲染图 · 爆炸图 · 原理图" },
];

const DELIVERY = [
  {
    title: "私有部署，数据不出本地",
    desc: "会话、事件、产物全部落在本地 SQLite 与工作区目录，不依赖任何外部存储。",
    tag: "数据安全",
  },
  {
    title: "模型可插拔",
    desc: "文本、图像、CAD 三套能力分别可换服务商与模型，Agnes / OpenAI 兼容网关都能接。",
    tag: "架构",
  },
  {
    title: "产物直接进下游",
    desc: "STEP / STL 进结构设计，原理图与 BOM 进硬件，固件框架进研发，无需二次整理。",
    tag: "可交付",
  },
];

const ARTIFACTS = [
  ["00_prd/", "产品需求文档、假设与待确认清单"],
  ["10_appearance/", "外观方案、渲染图 PNG、出图提示词存档"],
  ["20_structure/", "STEP / STL、四视图、爆炸图、一致性对照"],
  ["30_hardware/", "电路原理图 SVG、BOM、器件选型"],
  ["40_firmware/", "固件框架、仿真结果"],
  ["90_review/", "交叉评审报告与风险清单"],
];

/* --------------------------------------------------------- 滚动入场动画 */

function Reveal({ children, delay = 0 }: { children: React.ReactNode; delay?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // 尊重系统的「减少动效」设置：直接显示，不做位移
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setShown(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            setShown(true);
            io.disconnect();
          }
        }
      },
      { threshold: 0.12 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      style={{
        opacity: shown ? 1 : 0,
        transform: shown ? "none" : "translateY(14px)",
        transition: `opacity .55s ease ${delay}ms, transform .55s cubic-bezier(.16,1,.3,1) ${delay}ms`,
      }}
    >
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------ 页面 */

export default function HomePage() {
  return (
    <div className="min-h-screen">
      {/* 顶部导航 */}
      <header
        className="sticky top-0 z-30 border-b backdrop-blur"
        style={{
          borderColor: "var(--border)",
          background: "color-mix(in srgb, var(--bg) 82%, transparent)",
        }}
      >
        <div className="mx-auto flex max-w-[1200px] items-center gap-4 px-6 py-3">
          <div className="flex items-center gap-3">
            <div
              className="grid h-9 w-9 place-items-center rounded-xl text-[13px] font-semibold"
              style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
            >
              PD
            </div>
            <div className="leading-tight">
              <div className="text-[15px] font-semibold tracking-tight">智能产品设计智能体</div>
              <div className="text-[12px]" style={{ color: "var(--text-faint)" }}>
                一句话需求 → 外观 · 结构 · 硬件 · 固件全套方案
              </div>
            </div>
          </div>
          <nav className="ml-6 hidden items-center gap-1 md:flex">
            {[
              ["#flow", "设计流程"],
              ["#video", "全过程演示"],
              ["#delivery", "交付与部署"],
              ["#artifacts", "产物清单"],
            ].map(([href, label]) => (
              <a
                key={href}
                href={href}
                className="rounded-lg px-3 py-1.5 text-[13px] transition-colors hover:bg-[var(--bg-sunken)]"
                style={{ color: "var(--text-dim)" }}
              >
                {label}
              </a>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <ThemeToggle />
            <Link
              href="/"
              className="rounded-xl px-4 py-2 text-[13px] font-medium text-white transition-opacity hover:opacity-90"
              style={{ background: "var(--accent)" }}
            >
              进入工作台
            </Link>
          </div>
        </div>
      </header>

      {/* Hero */}
      <section className="relative overflow-hidden">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(900px 460px at 78% 12%, var(--accent-soft) 0%, transparent 62%)",
          }}
        />
        <div className="mx-auto grid max-w-[1200px] items-center gap-12 px-6 py-20 md:grid-cols-[1.05fr_.95fr] md:py-24">
          <div>
            <div
              className="inline-flex items-center gap-2 rounded-full border px-3 py-1 text-[12px]"
              style={{ borderColor: "var(--border)", color: "var(--text-dim)" }}
            >
              <span
                className="h-1.5 w-1.5 rounded-full"
                style={{ background: "var(--accent-2)" }}
              />
              智能硬件 · 全流程设计智能体
            </div>
            <h1
              className="mt-6 text-[34px] font-semibold leading-[1.15] tracking-tight md:text-[44px]"
              style={{ color: "var(--text)" }}
            >
              从一句话需求，
              <br />
              到可交付的智能硬件方案
            </h1>
            <p className="mt-5 max-w-[560px] text-[15px] leading-7" style={{ color: "var(--text-dim)" }}>
              六个阶段一条流水线：需求定义、外观设计、结构设计、硬件设计、固件与仿真、交叉评审。
              渲染图、爆炸装配图、电路原理图与 STEP / STL 实体模型全部真实产出，不是文字描述。
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <Link
                href="/"
                className="rounded-xl px-5 py-2.5 text-[14px] font-medium text-white transition-opacity hover:opacity-90"
                style={{ background: "var(--accent)" }}
              >
                开始设计
              </Link>
              <a
                href="#video"
                className="rounded-xl border px-5 py-2.5 text-[14px] font-medium transition-colors hover:bg-[var(--bg-sunken)]"
                style={{ borderColor: "var(--border-strong)", color: "var(--text)" }}
              >
                观看全过程演示
              </a>
            </div>

            <dl className="mt-12 grid grid-cols-2 gap-x-8 gap-y-6 sm:grid-cols-4">
              {METRICS.map((m) => (
                <div key={m.label}>
                  <dt className="flex items-baseline gap-1">
                    <span
                      className="text-[26px] font-semibold tracking-tight"
                      style={{ color: "var(--accent)" }}
                    >
                      {m.value}
                    </span>
                    <span className="text-[12px]" style={{ color: "var(--text-faint)" }}>
                      {m.unit}
                    </span>
                  </dt>
                  <dd className="mt-1.5 text-[12px] leading-5" style={{ color: "var(--text-dim)" }}>
                    {m.label}
                  </dd>
                </div>
              ))}
            </dl>
          </div>

          <Reveal delay={80}>
            <div
              className="overflow-hidden rounded-2xl border"
              style={{ borderColor: "var(--border)", boxShadow: "var(--shadow)" }}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src="/home/hero.png"
                alt="智能硬件产品设计智能体主视觉：深色工作台上的智能设备渲染与工程数据面板"
                className="block h-full w-full object-cover"
                style={{ aspectRatio: "16 / 10" }}
              />
            </div>
          </Reveal>
        </div>
      </section>

      {/* 全流程 */}
      <section id="flow" className="border-t" style={{ borderColor: "var(--border)" }}>
        <div className="mx-auto max-w-[1200px] px-6 py-20">
          <div className="max-w-[620px]">
            <div
              className="text-[12px] font-medium uppercase tracking-[.18em]"
              style={{ color: "var(--accent)" }}
            >
              Design Pipeline
            </div>
            <h2
              className="mt-3 text-[26px] font-semibold tracking-tight md:text-[30px]"
              style={{ color: "var(--text)" }}
            >
              六个阶段，串成一条可追溯的流水线
            </h2>
            <p className="mt-3 text-[14px] leading-7" style={{ color: "var(--text-dim)" }}>
              每个阶段由专属子智能体执行，产物落到独立目录，下游阶段直接读取上游产物继续推进 ——
              外观设计出的提示词会被结构设计复用，保证「图上是同一个产品」。
            </p>
          </div>

          <ol className="mt-12 grid gap-5 md:grid-cols-2 lg:grid-cols-3">
            {STAGES.map((s, i) => (
              <li key={s.id}>
                <Reveal delay={i * 60}>
                  <article
                    className="h-full overflow-hidden rounded-2xl border transition-transform duration-300 hover:-translate-y-0.5"
                    style={{
                      borderColor: "var(--border)",
                      background: "var(--bg-elev)",
                      boxShadow: "var(--shadow)",
                    }}
                  >
                    <div className="relative">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={s.img}
                        alt={`${s.name}阶段示意`}
                        className="block w-full object-cover"
                        style={{ aspectRatio: "16 / 9" }}
                      />
                      <span
                        className="absolute left-3 top-3 rounded-lg px-2 py-0.5 text-[12px] font-semibold"
                        style={{
                          background: "color-mix(in srgb, var(--bg-elev) 88%, transparent)",
                          color: "var(--accent)",
                        }}
                      >
                        {s.no}
                      </span>
                    </div>
                    <div className="p-5">
                      <h3 className="text-[16px] font-semibold" style={{ color: "var(--text)" }}>
                        {s.name}
                      </h3>
                      <div className="mt-1 text-[12px]" style={{ color: "var(--text-faint)" }}>
                        {s.agent}
                      </div>
                      <div
                        className="mt-4 rounded-xl px-3 py-2 text-[12px] leading-5"
                        style={{ background: "var(--bg-sunken)", color: "var(--text-dim)" }}
                      >
                        <span style={{ color: "var(--text-faint)" }}>输入 </span>
                        {s.input}
                      </div>
                      <ul className="mt-3 space-y-1.5">
                        {s.output.map((o) => (
                          <li
                            key={o}
                            className="flex items-start gap-2 text-[13px] leading-5"
                            style={{ color: "var(--text-dim)" }}
                          >
                            <span
                              className="mt-[7px] h-1 w-1 shrink-0 rounded-full"
                              style={{ background: "var(--accent-2)" }}
                            />
                            {o}
                          </li>
                        ))}
                      </ul>
                    </div>
                  </article>
                </Reveal>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* 全过程视频 */}
      <section id="video" className="border-t" style={{ borderColor: "var(--border)" }}>
        <div className="mx-auto max-w-[1200px] px-6 py-20">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div className="max-w-[620px]">
              <div
                className="text-[12px] font-medium uppercase tracking-[.18em]"
                style={{ color: "var(--accent)" }}
              >
                End-to-end Demo
              </div>
              <h2
                className="mt-3 text-[26px] font-semibold tracking-tight md:text-[30px]"
                style={{ color: "var(--text)" }}
              >
                一个人，一句话，走完整个设计过程
              </h2>
              <p className="mt-3 text-[14px] leading-7" style={{ color: "var(--text-dim)" }}>
                下面的演示视频由 Agnes 生成：工程师输入需求后，智能体依次推进六个阶段，
                逐步产出产品渲染图、爆炸装配图与电路板走线图，最终交付成品方案。
              </p>
            </div>
            <span
              className="rounded-full border px-3 py-1 text-[12px]"
              style={{ borderColor: "var(--border)", color: "var(--text-faint)" }}
            >
              视频与页面配图均由 Agnes 生成
            </span>
          </div>

          <Reveal>
            <div
              className="mt-8 overflow-hidden rounded-2xl border p-2"
              style={{
                borderColor: "var(--border)",
                background: "var(--bg-elev)",
                boxShadow: "var(--shadow)",
              }}
            >
              <video
                className="block w-full rounded-xl bg-black"
                style={{ aspectRatio: "16 / 9" }}
                poster="/home/hero.png"
                controls
                muted
                loop
                playsInline
                preload="metadata"
              >
                <source src="/home/process.mp4" type="video/mp4" />
                当前浏览器不支持视频播放，请直接使用工作台体验完整流程。
              </video>
            </div>
          </Reveal>
        </div>
      </section>

      {/* 交付与部署 */}
      <section id="delivery" className="border-t" style={{ borderColor: "var(--border)" }}>
        <div className="mx-auto max-w-[1200px] px-6 py-20">
          <div className="max-w-[620px]">
            <div
              className="text-[12px] font-medium uppercase tracking-[.18em]"
              style={{ color: "var(--accent)" }}
            >
              Commercialization
            </div>
            <h2
              className="mt-3 text-[26px] font-semibold tracking-tight md:text-[30px]"
              style={{ color: "var(--text)" }}
            >
              可私有部署，也能直接接进现有研发流程
            </h2>
          </div>

          <div className="mt-12 grid items-center gap-10 lg:grid-cols-[1.1fr_.9fr]">
            <ul className="space-y-4">
              {DELIVERY.map((d, i) => (
                <li key={d.title}>
                  <Reveal delay={i * 60}>
                    <div
                      className="flex gap-4 rounded-2xl border p-5"
                      style={{
                        borderColor: "var(--border)",
                        background: "var(--bg-elev)",
                      }}
                    >
                      <span
                        className="mt-0.5 h-fit rounded-lg px-2 py-0.5 text-[12px]"
                        style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
                      >
                        {d.tag}
                      </span>
                      <div>
                        <div className="text-[15px] font-semibold" style={{ color: "var(--text)" }}>
                          {d.title}
                        </div>
                        <p
                          className="mt-1.5 text-[13px] leading-6"
                          style={{ color: "var(--text-dim)" }}
                        >
                          {d.desc}
                        </p>
                      </div>
                    </div>
                  </Reveal>
                </li>
              ))}
            </ul>

            <Reveal delay={80}>
              <div
                className="overflow-hidden rounded-2xl border"
                style={{ borderColor: "var(--border)", boxShadow: "var(--shadow)" }}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src="/home/business.png"
                  alt="商业化数据看板概念图"
                  className="block w-full object-cover"
                  style={{ aspectRatio: "4 / 3" }}
                />
              </div>
            </Reveal>
          </div>
        </div>
      </section>

      {/* 产物清单 */}
      <section id="artifacts" className="border-t" style={{ borderColor: "var(--border)" }}>
        <div className="mx-auto max-w-[1200px] px-6 py-20">
          <h2
            className="text-[26px] font-semibold tracking-tight md:text-[30px]"
            style={{ color: "var(--text)" }}
          >
            一次运行，交付这些文件
          </h2>
          <div
            className="mt-8 overflow-hidden rounded-2xl border"
            style={{ borderColor: "var(--border)", background: "var(--bg-elev)" }}
          >
            <table className="w-full border-collapse text-left">
              <thead>
                <tr className="border-b" style={{ borderColor: "var(--border)" }}>
                  <th className="px-5 py-3 text-[12px] font-medium" style={{ color: "var(--text-faint)" }}>
                    目录
                  </th>
                  <th className="px-5 py-3 text-[12px] font-medium" style={{ color: "var(--text-faint)" }}>
                    内容
                  </th>
                </tr>
              </thead>
              <tbody>
                {ARTIFACTS.map(([dir, desc]) => (
                  <tr key={dir} className="border-b last:border-0" style={{ borderColor: "var(--border)" }}>
                    <td
                      className="whitespace-nowrap px-5 py-3 font-mono text-[13px]"
                      style={{ color: "var(--accent)" }}
                    >
                      {dir}
                    </td>
                    <td className="px-5 py-3 text-[13px]" style={{ color: "var(--text-dim)" }}>
                      {desc}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* 结尾 CTA */}
      <section className="border-t" style={{ borderColor: "var(--border)" }}>
        <div
          className="mx-auto max-w-[1200px] px-6 py-20 text-center"
          style={{ background: "transparent" }}
        >
          <h2
            className="text-[26px] font-semibold tracking-tight md:text-[30px]"
            style={{ color: "var(--text)" }}
          >
            把下一款产品，交给这条流水线
          </h2>
          <p className="mx-auto mt-3 max-w-[560px] text-[14px] leading-7" style={{ color: "var(--text-dim)" }}>
            描述需求，智能体会先补齐关键信息，再依次推进六个阶段，随时可查看进度、中断或接管。
          </p>
          <Link
            href="/"
            className="mt-8 inline-block rounded-xl px-6 py-3 text-[14px] font-medium text-white transition-opacity hover:opacity-90"
            style={{ background: "var(--accent)" }}
          >
            进入工作台
          </Link>
        </div>
      </section>

      <footer className="border-t py-8" style={{ borderColor: "var(--border)" }}>
        <div
          className="mx-auto flex max-w-[1200px] flex-wrap items-center justify-between gap-3 px-6 text-[12px]"
          style={{ color: "var(--text-faint)" }}
        >
          <span>智能产品设计智能体 · 本地私有部署</span>
          <span>页面配图与演示视频由 Agnes 生成</span>
        </div>
      </footer>
    </div>
  );
}
