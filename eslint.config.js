// Flat ESLint config (issue #796). Lints the esbuild source root only —
// static/src/. The legacy inline JS in *.html is intentionally out of scope
// until it is incrementally extracted into modules in follow-on tickets.
export default [
  {
    files: ["apps/dashboard/static/src/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        window: "readonly",
        globalThis: "readonly",
        document: "readonly",
        module: "writable",
      },
    },
    rules: {
      "no-undef": "error",
      "no-unused-vars": "warn",
    },
  },
];
