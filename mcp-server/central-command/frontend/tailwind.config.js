/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // OsirisCare Brand
        brand: {
          teal: '#3CBCB4',      // Logo leaf color — primary accent
          'teal-dark': '#14A89E', // Gradient start / darker shade
          'teal-glow': 'rgba(60, 188, 180, 0.35)',
        },
        // iOS System Colors
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
        // Fill colors (iOS grouped content backgrounds)
        fill: {
          primary: 'rgba(120, 120, 128, 0.2)',
          secondary: 'rgba(120, 120, 128, 0.16)',
          tertiary: 'rgba(120, 120, 128, 0.12)',
          quaternary: 'rgba(120, 120, 128, 0.08)',
        },
        // Text colors
        label: {
          primary: '#1C1C1E',
          secondary: 'rgba(60, 60, 67, 0.6)',
          tertiary: '#8E8E93',
        },
        // Health status colors
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
        'ios': '10px',
        'ios-sm': '8px',
        'ios-md': '12px',
        'ios-lg': '16px',
        'ios-xl': '24px',
      },
      boxShadow: {
        'glass': '0 4px 30px rgba(0, 0, 0, 0.1)',
        'card': '0 0.5px 0 rgba(0, 0, 0, 0.04), 0 2px 8px rgba(0, 0, 0, 0.04), 0 4px 24px rgba(0, 0, 0, 0.06)',
        'card-hover': '0 0.5px 0 rgba(0, 0, 0, 0.04), 0 4px 12px rgba(0, 0, 0, 0.08), 0 8px 32px rgba(0, 0, 0, 0.1)',
        'card-inset': 'inset 0 1px 0 rgba(255, 255, 255, 0.5)',
        'glow-blue': '0 0 20px rgba(0, 122, 255, 0.15)',
        'glow-green': '0 0 20px rgba(52, 199, 89, 0.15)',
        'glow-red': '0 0 20px rgba(255, 59, 48, 0.15)',
        'glow-orange': '0 0 20px rgba(255, 149, 0, 0.15)',
        'glow-teal': '0 4px 20px rgba(60, 188, 180, 0.4)',
      },
      backdropBlur: {
        'glass': '20px',
      },
      backdropSaturate: {
        'glass': '180%',
      },
      keyframes: {
        'shimmer': {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        'fade-in': {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'slide-in-right': {
          '0%': { opacity: '0', transform: 'translateX(8px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
        'scale-in': {
          '0%': { opacity: '0', transform: 'scale(0.95)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
        'pulse-soft': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.6' },
        },
      },
      animation: {
        'shimmer': 'shimmer 2s ease-in-out infinite',
        'fade-in': 'fade-in 0.3s ease-out',
        'slide-in-right': 'slide-in-right 0.2s ease-out',
        'scale-in': 'scale-in 0.2s ease-out',
        'pulse-soft': 'pulse-soft 2s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
