/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // iOS System Colors (matching Central Command)
        ios: {
          red: '#FF3B30',
          orange: '#FF9500',
          yellow: '#FFCC00',
          green: '#34C759',
          mint: '#00C7BE',
          teal: '#30B0C7',
          cyan: '#32ADE6',
          blue: '#007AFF',
          indigo: '#5856D6',
          purple: '#AF52DE',
          pink: '#FF2D55',
          gray: '#8E8E93',
        },
        // Background colors
        background: {
          primary: '#F2F2F7',
          secondary: '#FFFFFF',
          tertiary: 'rgba(255, 255, 255, 0.72)',
        },
        // Text colors
        label: {
          primary: '#1C1C1E',
          secondary: 'rgba(60, 60, 67, 0.6)',
          tertiary: '#8E8E93',
        },
        // Health/Compliance status colors
        health: {
          critical: '#FF3B30',
          warning: '#FF9500',
          healthy: '#34C759',
          neutral: '#8E8E93',
        },
        // Accent colors
        accent: {
          primary: '#007AFF',
          secondary: '#5856D6',
          tint: 'rgba(0, 122, 255, 0.1)',
        },
        // Border colors
        separator: {
          light: 'rgba(60, 60, 67, 0.1)',
          medium: 'rgba(60, 60, 67, 0.18)',
        },
      },
      fontFamily: {
        sans: [
          '-apple-system',
          'BlinkMacSystemFont',
          '"SF Pro Display"',
          '"SF Pro Text"',
          'system-ui',
          'sans-serif',
        ],
        mono: ['"SF Mono"', 'Menlo', 'Monaco', 'monospace'],
      },
      fontSize: {
        'xs': ['11px', { lineHeight: '13px' }],
        'sm': ['13px', { lineHeight: '18px' }],
        'base': ['15px', { lineHeight: '20px' }],
        'lg': ['17px', { lineHeight: '22px' }],
        'xl': ['20px', { lineHeight: '25px' }],
        '2xl': ['24px', { lineHeight: '29px' }],
        '3xl': ['34px', { lineHeight: '41px' }],
      },
      borderRadius: {
        'ios-sm': '8px',
        'ios-md': '12px',
        'ios-lg': '16px',
        'ios-xl': '24px',
      },
      boxShadow: {
        'glass': '0 4px 30px rgba(0, 0, 0, 0.1)',
        'card': '0 2px 8px rgba(0, 0, 0, 0.04), 0 4px 24px rgba(0, 0, 0, 0.08)',
        'card-hover': '0 4px 12px rgba(0, 0, 0, 0.08), 0 8px 32px rgba(0, 0, 0, 0.12)',
      },
    },
  },
  plugins: [],
}
