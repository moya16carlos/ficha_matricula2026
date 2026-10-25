/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/templates/**/*.html',  // todas las plantillas (incluye el JS inline, escaneado como texto)
  ],
  theme: {
    extend: {
      colors: {
        // Contraste: paleta slate un paso más oscura (textos secundarios legibles)
        slate: {
          200: '#e2e8f0', 300: '#94a3b8', 400: '#64748b', 500: '#475569',
          600: '#334155', 700: '#1e293b', 800: '#0f172a', 900: '#020617'
        }
      }
    },
  },
  plugins: [],
}
