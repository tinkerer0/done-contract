// calls.cjs <file.ts> <name>: how many it()/test() blocks call the imported function <name>.
// Alias-aware (import {name as x}), namespace-aware (ns.name()), and follows module-level wrapper functions to a fixed point.
// Calls inside comments or strings do not count, and a bare `void name` reference is not a call.
const ts = require(process.env.TRIAL_NODE_MODULES + "/typescript");
const fs = require("fs");
const [file, name] = process.argv.slice(2);
const sf = ts.createSourceFile("x.ts", fs.readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
const locals = new Set(), namespaces = new Set();
sf.forEachChild((n) => {
  if (ts.isImportDeclaration(n) && n.importClause && n.importClause.namedBindings) {
    const nb = n.importClause.namedBindings;
    if (ts.isNamedImports(nb)) nb.elements.forEach((e) => { if ((e.propertyName || e.name).text === name) locals.add(e.name.text); });
    else if (ts.isNamespaceImport(nb)) namespaces.add(nb.name.text);
  }
});
function directCall(x) {
  if (!ts.isCallExpression(x)) return false;
  const e = x.expression;
  if (ts.isIdentifier(e) && locals.has(e.text)) return true;
  return ts.isPropertyAccessExpression(e) && ts.isIdentifier(e.expression) && namespaces.has(e.expression.text) && e.name.text === name;
}
function contains(node) {
  let found = false;
  (function rec(x) { if (found) return; if (directCall(x)) { found = true; return; } ts.forEachChild(x, rec); })(node);
  return found;
}
// wrappers: module-level functions / arrow consts whose body calls the target (to a fixed point)
const decls = [];
sf.forEachChild((n) => {
  if (ts.isFunctionDeclaration(n) && n.name && n.body) decls.push([n.name.text, n.body]);
  if (ts.isVariableStatement(n)) n.declarationList.declarations.forEach((d) => {
    if (ts.isIdentifier(d.name) && d.initializer && (ts.isArrowFunction(d.initializer) || ts.isFunctionExpression(d.initializer))) decls.push([d.name.text, d.initializer.body]);
  });
});
let grew = true;
while (grew) {
  grew = false;
  for (const [n, body] of decls) if (!locals.has(n) && contains(body)) { locals.add(n); grew = true; }
}
let tests = 0, calling = 0;
(function visit(n) {
  if (ts.isCallExpression(n) && ts.isIdentifier(n.expression) && (n.expression.text === "it" || n.expression.text === "test")) {
    tests++; if (contains(n)) calling++;
  }
  ts.forEachChild(n, visit);
})(sf);
process.stdout.write(JSON.stringify({ tests, calling }));
