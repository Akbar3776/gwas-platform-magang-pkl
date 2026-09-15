module.exports = {
  root: true,
  env: {
    browser: true,
    es2022: true,
    jest: true,
  },
  extends: [
    "eslint:recommended",
    "plugin:react/recommended",
    "plugin:react-hooks/recommended",
  ],
  parserOptions: {
    ecmaVersion: "latest",
    sourceType: "module",
    ecmaFeatures: { jsx: true },
  },
  settings: {
    react: { version: "detect" },
  },
  plugins: ["react", "react-hooks", "react-refresh"],
  rules: {
    "react/react-in-jsx-scope": "off",
    "react/prop-types": "off",
    "react-refresh/only-export-components": "off",
    "no-unused-vars": ["error", { "argsIgnorePattern": "^_", "varsIgnorePattern": "^_" }],
  },
  ignorePatterns: ["dist/", "node_modules/", "coverage/"],
};
