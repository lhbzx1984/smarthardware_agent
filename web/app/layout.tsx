import type { Metadata } from "next";
import PcbBackdrop from "@/components/PcbBackdrop";
import "./globals.css";

export const metadata: Metadata = {
  title: "智能产品设计智能体",
  description: "输入产品需求，自动完成外观、结构、硬件设计与软件仿真",
};

const themeInit = `
(function(){
  try {
    // ?theme=dark|light 优先（便于分享指定主题的链接、也便于无人值守截图）
    var q = new URLSearchParams(location.search).get('theme');
    var t = (q === 'dark' || q === 'light') ? q : localStorage.getItem('pda-theme');
    if (!t) t = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', t);
  } catch (e) { document.documentElement.setAttribute('data-theme', 'light'); }
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInit }} />
      </head>
      <body className="min-h-screen antialiased">
        <PcbBackdrop />
        <div className="app-shell">{children}</div>
      </body>
    </html>
  );
}
