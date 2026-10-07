// tokens.cjs <file.ts>: formatter-invariant token stream of a TypeScript file (comments, whitespace, quote style and trailing commas ignored).
const ts = require(process.env.TRIAL_NODE_MODULES + "/typescript");
const fs = require("fs");
const text = fs.readFileSync(process.argv[2], "utf8");
const sf = ts.createSourceFile("x.ts", text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
const out = [];
function walk(node) {
  const kids = node.getChildren(sf);
  if (kids.length === 0) {
    if (node.kind === ts.SyntaxKind.EndOfFileToken) return;
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) out.push("s:" + node.text);
    else if (ts.isNumericLiteral(node)) out.push("n:" + node.text);
    else if (ts.isIdentifier(node)) out.push("i:" + node.text);
    else out.push("t:" + node.getText(sf));
    return;
  }
  const isList = node.kind === ts.SyntaxKind.SyntaxList;
  kids.forEach((c, i) => {
    if (isList && i === kids.length - 1 && c.kind === ts.SyntaxKind.CommaToken) return;   // trailing comma
    walk(c);
  });
}
walk(sf);
process.stdout.write(JSON.stringify(out));
