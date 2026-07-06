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
        localStorage: "readonly",
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
        CSS: "readonly",
        CustomEvent: "readonly",
        Event: "readonly",
        DragEvent: "readonly",
        FormData: "readonly",
        URL: "readonly",
        URLSearchParams: "readonly",
        TextDecoder: "readonly",
        AbortController: "readonly",
        EventSource: "readonly",
        location: "readonly",
        navigator: "readonly",
        console: "readonly",
        // Legacy page globals (issue #797 incremental extraction). These are
        // inline functions still living in project.html that the extracted
        // modules call across the bundle boundary at runtime. Remove each
        // entry as the function itself is extracted into static/src/.
        showToast: "readonly",
        _preflightLoadBanners: "readonly",
        _SMGMT_FILTER_PRIORITY: "readonly",
        _fmtWallClock: "readonly",
        _fmtElapsed: "readonly",
        _fmtStoppedAt: "readonly",
        _fmtRunningTime: "readonly",
        _fmtTicketElapsed: "readonly",
        _sizeMinutes: "readonly",
        _sfcRunningHtml: "readonly",
        _sfcCompletedHtml: "readonly",
        _sfcHasReworkHtml: "readonly",
        _sfcCancelledHtml: "readonly",
        _smgmtHotswapAvailableFor: "readonly",
        _smgmtHotswapModalOpen: "readonly",
        _smgmtPlanNextBtn: "readonly",
        // Shell globals written by tabs.js and read by other modules (issue #1175).
        _ticketsRepo: "writable",
        _deepLinkView: "writable",
        _deepLinkFilter: "writable",
      },
    },
    rules: {
      "no-undef": "error",
      "no-unused-vars": [
        "warn",
        {
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
          argsIgnorePattern: "^_",
        },
      ],
    },
  },
];
