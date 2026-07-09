import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Airline Dispute Voice Agent",
  description:
    "Bilingual (Urdu/English) voice-driven dispute resolution agent for airline customer claims — refunds, cancellations, and delay compensation.",
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
