import type { Metadata, Viewport } from "next";

import { ServiceWorker } from "@/components/ServiceWorker";

import "./globals.css";

export const metadata: Metadata = {
  title: "GeoInstant - where was this photo taken?",
  description: "Upload a photo and get its most likely location, with confidence and the visual evidence behind it.",
  applicationName: "GeoInstant",
  appleWebApp: { capable: true, title: "GeoInstant", statusBarStyle: "default" },
  icons: { icon: "/icon.svg", apple: "/icons/icon-192.png" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f8fafc" },
    { media: "(prefers-color-scheme: dark)", color: "#12151c" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="antialiased">
        {children}
        <ServiceWorker />
      </body>
    </html>
  );
}
