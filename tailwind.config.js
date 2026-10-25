/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './templates/**/*.html',            // Busca en todas las plantillas HTML dentro de templates/
    './templates/app/**/*.html',        // Busca específicamente en las vistas de la carpeta app/
    './static/app/js/**/*.js',          // Busca en cualquier archivo JS dentro de static/app/js/
  ],
  theme: {
    extend: {},
  },
  plugins: [],
}