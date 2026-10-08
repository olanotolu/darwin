import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DARWIN — concrete discovery instrument",
  description: "Factory-constrained concrete discovery over BOxCrete. Facilities are FICTIONAL.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark">
      <body>{children}</body>
    </html>
  );
}
