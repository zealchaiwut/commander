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
        // Browser APIs the extracted board modules use (issue #797). ES
        // built-ins (Set, JSON, Promise, …) come from ecmaVersion; these Web
        // APIs must be declared so no-undef does not flag them.
        fetch: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        setInterval: "readonly",
        clearInterval: "readonly",
        requestAnimationFrame: "readonly",
        cancelAnimationFrame: "readonly",
        confirm: "readonly",
        alert: "readonly",
        prompt: "readonly",
        getComputedStyle: "readonly",
        CustomEvent: "readonly",
        Event: "readonly",
        DragEvent: "readonly",
        FormData: "readonly",
        URL: "readonly",
        URLSearchParams: "readonly",
        location: "readonly",
        navigator: "readonly",
        console: "readonly",
      },
    },
    rules: {
      "no-undef": "error",
      "no-unused-vars": "warn",
    },
  },
];
