import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "GeoInstant",
    short_name: "GeoInstant",
    description: "Find where a photo was taken.",
    start_url: "/",
    display: "standalone",
    background_color: "#12151c",
    theme_color: "#0e9fb5",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icons/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
    // Android: "Share → GeoInstant" opens the app with the photo (handled by the service worker).
    share_target: {
      action: "/share-target",
      method: "POST",
      enctype: "multipart/form-data",
      params: { files: [{ name: "image", accept: ["image/*"] }] },
    },
  } as MetadataRoute.Manifest;
}
