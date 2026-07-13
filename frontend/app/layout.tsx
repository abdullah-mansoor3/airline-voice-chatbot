import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Claim Desk — Airline Assistant",
  description:
    "Bilingual airline dispute and policy assistant for refunds, baggage, cancellations, and flight questions.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={inter.variable}>
      <body>{children}</body>
    </html>
  );
}
