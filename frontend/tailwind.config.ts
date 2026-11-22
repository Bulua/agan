import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{vue,js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        sans: [
          'Roboto',
          'Noto Sans SC',
          '-apple-system',
          'BlinkMacSystemFont',
          'Segoe UI',
          'PingFang SC',
          'Microsoft YaHei',
          'sans-serif',
        ],
        mono: ['JetBrains Mono', 'Roboto Mono', 'Consolas', 'monospace'],
      },
      borderRadius: {
        none: '0px',
        xs: '2px',
        sm: '2px',
        DEFAULT: '4px',
        md: '4px',
        lg: '4px',
        xl: '6px',
      },
    },
  },
  plugins: [],
} satisfies Config
