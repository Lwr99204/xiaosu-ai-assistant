import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "小苏 · 管理后台",
  description: "企业微信 AI 助手的知识库与对话审计后台",
};

const navigation = [
  { href: "/documents", label: "文档管理", icon: "▤" },
  { href: "/logs", label: "对话日志", icon: "◫" },
  { href: "/settings", label: "系统设置", icon: "⌁" },
];

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        <div className="app-shell">
          <aside className="sidebar">
            <div className="brand">
              <span className="brand-mark">苏</span>
              <span><strong>小苏</strong><small>AI ASSISTANT</small></span>
            </div>
            <nav aria-label="后台导航">
              {navigation.map((item) => (
                <Link href={item.href} key={item.href} className="nav-link">
                  <span aria-hidden="true">{item.icon}</span>{item.label}
                </Link>
              ))}
            </nav>
            <div className="sidebar-foot">
              <span className="status-dot" />
              <span>企业内部系统</span>
            </div>
          </aside>
          <main className="main-content">{children}</main>
        </div>
      </body>
    </html>
  );
}
