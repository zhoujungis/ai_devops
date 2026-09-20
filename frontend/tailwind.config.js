/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{vue,ts}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Match the Linear/Vercel register the plan calls for.
        surface: {
          DEFAULT: "#0b0d10",
          raised: "#12151a",
          border: "#1f242b",
        },
      },
    },
  },
  plugins: [],
};
