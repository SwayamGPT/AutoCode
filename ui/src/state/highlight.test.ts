import { describe, expect, it } from "vitest";
import { highlight, languageFor } from "./highlight";

describe("highlight", () => {
  it("picks languages by extension and file name", () => {
    expect(languageFor("src/app.py")).toBe("python");
    expect(languageFor("ui/App.tsx")).toBe("typescript");
    expect(languageFor("Dockerfile")).toBe("bash");
    expect(languageFor("notes.unknown")).toBeUndefined();
  });

  it("escapes source so generated code can never inject markup", () => {
    expect(highlight("a.unknown", "<script>alert(1)</script>")).toBe("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(highlight("a.py", 'x = "<img src=x onerror=alert(1)>"')).not.toContain("<img");
    expect(highlight("a.py", "def f(): pass")).toContain("hljs-keyword");
  });
});
