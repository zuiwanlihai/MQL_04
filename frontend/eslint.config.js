// ESLint flat config (eslint 9).  Required: `npm run lint` is `eslint .`, and
// eslint 9 no longer reads `.eslintrc.*`.  Without this file the script aborted
// with "couldn't find an eslint.config.js" — so no frontend file was ever
// linted, and the `eslint-disable-next-line react-hooks/exhaustive-deps`
// comments throughout `src/` were inert (a disable for an unregistered rule is
// silently ignored, so they looked like deliberate suppressions while
// suppressing nothing).

import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist/**", "node_modules/**", "coverage/**"] },

  js.configs.recommended,
  ...tseslint.configs.recommended,

  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: globals.browser,
    },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,

      // TS already enforces both of these, and more accurately.
      "no-unused-vars": "off",
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
);
