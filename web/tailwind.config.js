/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: '#0f1117',
        surface: '#1a1d27',
        surface2: '#22263a',
        border: '#2e3250',
        accent: '#4f8ef7',
        accent2: '#7c5cbf',
        green: '#22c55e',
        yellow: '#eab308',
        red: '#ef4444',
        blue: '#3b82f6',
        gray: '#6b7280',
        text: '#e2e8f0',
        text2: '#94a3b8',
      },
    },
  },
  plugins: [],
}
