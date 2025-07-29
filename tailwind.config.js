// tailwind.config.js
module.exports = {
    content: [
      "./templates/**/*.html", // 確保掃描所有 HTML 模板文件
      "./static/**/*.js",      // 如果您的JS中也有動態類名，也包含進來
    ],
    theme: {
      extend: {
        colors: {
          // 在這裡定義您的自定義顏色
          // 確保命名與 HTML 中使用的動態類名保持一致
          // 例如：
          '良好-aqi': '#00e676', // 綠色
          '普通-aqi': '#ffeb3b', // 黃色 (需要考慮文本顏色)
          '對敏感族群不健康-aqi': '#ff9800', // 橘色
          '不健康-aqi': '#f44336', // 紅色
          '非常不健康-aqi': '#9c27b0', // 紫色
          '危害-aqi': '#795548', // 咖啡色
          '維護-aqi': '#9e9e9e', // 灰色
          '無效-aqi': '#9e9e9e', // 灰色
          'n/a-aqi': '#9e9e9e', // 灰色
          '未知-aqi': '#9e9e9e', // 灰色
  
          '良好-status': '#00e676',
          '普通-status': '#ffeb3b',
          '對敏感族群不健康-status': '#ff9800',
          '不健康-status': '#f44336',
          '非常不健康-status': '#9c27b0',
          '危害-status': '#795548',
          '維護-status': '#9e9e9e',
          '無效-status': '#9e9e9e',
          'n/a-status': '#9e9e9e',
          '未知-status': '#9e9e9e',
        },
        // 如果 '普通' 狀態的文字需要是深色，可以考慮這樣做
        textColor: {
          '普通-aqi': '#333', // 讓黃色背景上的文字變成深色
          // 其他顏色保持預設的白字或根據需求調整
        }
      },
    },
    plugins: [],
  }