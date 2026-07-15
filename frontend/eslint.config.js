import tsParser from "@typescript-eslint/parser";
import tsPlugin from "@typescript-eslint/eslint-plugin";
import hooksPlugin from "eslint-plugin-react-hooks";

export default [
  {
    ignores: ["dist/**", "node_modules/**"],
  },
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: 2020,
        sourceType: "module",
      },
    },
    plugins: {
      "@typescript-eslint": tsPlugin,
      "react-hooks": hooksPlugin,
    },
    rules: {
      ...tsPlugin.configs.recommended.rules,
      ...hooksPlugin.configs.recommended.rules,
      // TypeScript already enforces these more strictly; disable to avoid noise.
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unused-vars": "off",
      // react-hooks v5 introduced aggressive rules that flag common patterns in
      // the existing codebase (syncing server state in useEffect, ref access
      // during render). Disable for now; re-enable incrementally as code is
      // refactored to avoid setState-in-effect anti-patterns.
      "react-hooks/set-state-in-effect": "off",
      "react-hooks/purity": "off",
      "react-hooks/immutability": "off",
      "react-hooks/refs": "off",
    },
  },
];
