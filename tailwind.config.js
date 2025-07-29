// tailwind.config.js
module.exports = {
    content: [
      "./templates/**/*.html",
      "./static/**/*.js",
    ],
    safelist: [
        // 對於 AQI 數值的文字顏色
        'text-good-aqi',
        'text-moderate-aqi',
        'text-unhealthy-for-sensitive-aqi',
        'text-unhealthy-aqi',
        'text-very-unhealthy-aqi',
        'text-hazardous-aqi',
        'text-maintenance-aqi',
        'text-invalid-aqi',
        'text-na-aqi', // for 'n/a'
        'text-unknown-aqi',

        // 對於狀態標籤的背景顏色
        'bg-good-status',
        'bg-moderate-status',
        'bg-unhealthy-for-sensitive-status',
        'bg-unhealthy-status',
        'bg-very-unhealthy-status',
        'bg-hazardous-status',
        'bg-maintenance-status',
        'bg-invalid-status',
        'bg-na-status', // for 'n/a'
        'bg-unknown-status',

        // 這些是篩選按鈕的 active 狀態
        'bg-blue-500',
        'text-white',
    ],
    theme: {
      extend: {
        colors: {
          // 在這裡定義您的自定義顏色，使用英文或拼音作為鍵名
          'good-aqi': '#00e676',
          'moderate-aqi': '#ffeb3b',
          'unhealthy-for-sensitive-aqi': '#ff9800',
          'unhealthy-aqi': '#f44336',
          'very-unhealthy-aqi': '#9c27b0',
          'hazardous-aqi': '#795548',
          'maintenance-aqi': '#9e9e9e',
          'invalid-aqi': '#9e9e9e',
          'na-aqi': '#9e9e9e',
          'unknown-aqi': '#9e9e9e',

          'good-status': '#00e676',
          'moderate-status': '#ffeb3b',
          'unhealthy-for-sensitive-status': '#ff9800',
          'unhealthy-status': '#f44336',
          'very-unhealthy-status': '#9c27b0',
          'hazardous-status': '#795548',
          'maintenance-status': '#9e9e9e',
          'invalid-status': '#9e9e9e',
          'na-status': '#9e9e9e',
          'unknown-status': '#9e9e9e',
        },
        textColor: {
          'moderate-aqi': '#333',
        }
      },
    },
    plugins: [],
}