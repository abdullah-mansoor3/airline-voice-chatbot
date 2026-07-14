import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
          { key: "Cross-Origin-Embedder-Policy", value: "require-corp" },
        ],
      },
    ];
  },
  webpack: (config) => {
    // Tell webpack to process onnxruntime-web's .mjs files as regular JS
    // Without this, webpack 5 fails to emit the chunk
    config.module.rules.push({
      test: /\.mjs$/,
      include: /node_modules\/onnxruntime-web/,
      type: "javascript/auto",
    });
    return config;
  },
};

export default nextConfig;