import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import css from "highlight.js/lib/languages/css";
import go from "highlight.js/lib/languages/go";
import java from "highlight.js/lib/languages/java";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import python from "highlight.js/lib/languages/python";
import rust from "highlight.js/lib/languages/rust";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";

// Only the languages generated projects commonly contain, to keep the bundle small.
const LANGUAGES = { bash, css, go, java, javascript, json, markdown, python, rust, sql, typescript, xml, yaml };
for (const [name, language] of Object.entries(LANGUAGES)) hljs.registerLanguage(name, language);

const BY_EXTENSION: Record<string, string> = {
  py: "python", js: "javascript", jsx: "javascript", mjs: "javascript", ts: "typescript", tsx: "typescript",
  json: "json", md: "markdown", sh: "bash", bash: "bash", css: "css", html: "xml", xml: "xml", svg: "xml",
  yml: "yaml", yaml: "yaml", sql: "sql", go: "go", rs: "rust", java: "java", toml: "bash",
};

export function languageFor(path: string): string | undefined {
  const name = path.split("/").pop() ?? "";
  if (name === "Dockerfile" || name === "Makefile") return "bash";
  return BY_EXTENSION[name.split(".").pop()?.toLowerCase() ?? ""];
}

/** HTML for a file. highlight.js escapes the source, so the result is safe to inject; unknown types are escaped here. */
export function highlight(path: string, source: string): string {
  const language = languageFor(path);
  if (language) return hljs.highlight(source, { language, ignoreIllegals: true }).value;
  return source.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c] ?? c);
}
