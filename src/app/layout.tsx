import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "LOOB Reading Companion",
  description: "A voice-first reading companion.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
