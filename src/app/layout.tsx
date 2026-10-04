import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";

const sans = localFont({ src: "../assets/fonts/google-sans-flex-latin.woff2", weight: "100 1000", variable: "--font-sans", display: "swap" });
const book = localFont({ src: "../assets/fonts/lora-latin.woff2", weight: "400 700", variable: "--font-book", display: "swap" });
const handwriting = localFont({ src: "../assets/fonts/architects-daughter-latin.woff2", weight: "400", variable: "--font-hand", display: "swap" });
const mono = localFont({ src: [
  { path: "../assets/fonts/ibm-plex-mono-400-latin.woff2", weight: "400" },
  { path: "../assets/fonts/ibm-plex-mono-500-latin.woff2", weight: "500" },
], variable: "--font-mono", display: "swap" });

export const metadata: Metadata = {
  title: "LOOB Reading Companion",
  description: "A voice-first reading companion.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${sans.variable} ${book.variable} ${handwriting.variable} ${mono.variable}`}>{children}</body>
    </html>
  );
}
