import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Airline Dispute Voice Agent",
  description: "Voice prototype for airline dispute intake",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
