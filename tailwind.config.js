/** @type {import('tailwindcss').Config} */
module.exports = {
    content: [
      "./templates/**/*.html", // 掃描所有 HTML 模板檔案
      "./static/js/**/*.js",   // 如果有 JavaScript 檔案中使用 Tailwind 類別，也掃描
    ],
    theme: {
      extend: {},
    },
    plugins: [],
  }