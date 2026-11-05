import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const modules = new Map();
export function sourceURL(relative, parent = new URL("../src/", import.meta.url)) {
    let file = new URL(relative, parent);
    if (!existsSync(file)) file = new URL(file.href + (existsSync(new URL(file.href + ".ts")) ? ".ts" : ".tsx"));
    if (modules.has(file.href)) return modules.get(file.href);
    const output = ts.transpileModule(readFileSync(fileURLToPath(file), "utf8"), { fileName: file.pathname,
        compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023, jsx: ts.JsxEmit.ReactJSX } }).outputText;
    const code = output.replace(/from\s+["']([^"']+)["']/g, (_, specifier) =>
        `from ${JSON.stringify(specifier.startsWith(".") ? sourceURL(specifier, file) : import.meta.resolve(specifier))}`);
    const url = `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
    modules.set(file.href, url);
    return url;
}
