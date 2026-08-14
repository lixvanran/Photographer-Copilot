/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // 摄影主题浅色(Apple 风)
        phc: {
          ink: "#1A1F2E",        // 主深色(接近黑)
          deep: "#2A2F45",        // 二级深
          accent: "#FF7A45",      // 暖橙(落日金)
          accentDark: "#E56834",  // 橙暗
          sky: "#3A7BD5",         // 蓝
          green: "#34C77B",       // 绿
          red: "#E5484D",         // 警示
        },
        ok: "#34C77B",
        warn: "#F1A52A",
        err: "#E5484D",
      },
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          "'SF Pro Display'",
          "'SF Pro Text'",
          "PingFang SC",
          "Hiragino Sans GB",
          "Microsoft YaHei",
          "system-ui",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Monaco",
          "'Cascadia Code'",
          "monospace",
        ],
      },
      letterSpacing: {
        tightish: "-0.01em",
      },
      animation: {
        "pulse-slow": "pulse 2.4s cubic-bezier(0.4, 0, 0.6, 1) infinite",
      },
    },
  },
  plugins: [],
};
